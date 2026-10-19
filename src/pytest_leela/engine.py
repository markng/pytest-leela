"""Mutation testing orchestrator."""

from __future__ import annotations

import ntpath  # noqa: F401 — keep in sys.modules; Python 3.13 pathlib lazily

#                              imports ntpath from PurePath.__init__, and
#                              pytest's assertion rewriter calls PurePath in
#                              find_spec — if ntpath is absent the import
#                              re-enters find_spec causing infinite recursion.
import hashlib
import os
import sys
import tempfile
import time
from collections.abc import Callable

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.coverage_tracker import collect_coverage
from pytest_leela.git_diff import changed_lines
from pytest_leela.index import IndexDB, extract_symbols
from pytest_leela.models import (
    CoverageMap,
    EngineProgress,
    EnrichmentStats,
    Mutant,
    MutantResult,
    RunResult,
)
from pytest_leela.operators import build_allowed_keys, count_pruned, mutations_for
from pytest_leela.resources import ResourceLimits, apply_limits, is_memory_ok
from pytest_leela.runner import precompute_user_modules, run_tests_for_mutant
from pytest_leela.type_extractor import enrich_mutation_points


def _module_name_from_path(file_path: str) -> str:
    """Convert an absolute file path to a dotted module name.

    Resolves the module name relative to ``sys.path`` entries so that
    projects using a ``src/`` layout (where ``src`` is on ``sys.path``)
    produce the correct importable name (e.g. ``pytest_leela.models``
    instead of ``src.pytest_leela.models``).

    Falls back to CWD-relative resolution when no ``sys.path`` entry
    matches.
    """
    abs_path = os.path.abspath(file_path)
    # Try each sys.path entry, longest first, to find the correct base
    candidates: list[tuple[int, str]] = []
    for entry in sys.path:
        base = os.path.abspath(entry)
        if abs_path.startswith(base + os.sep):
            candidates.append((len(base), base))
    # Prefer the longest (most specific) sys.path match
    if candidates:
        candidates.sort(reverse=True)
        base = candidates[0][1]
        rel = os.path.relpath(abs_path, base)
        if rel.endswith(".py"):
            rel = rel[:-3]
        return rel.replace(os.sep, ".")
    # Fallback: CWD-relative
    rel = os.path.relpath(abs_path)
    if rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace(os.sep, ".")


def _clean_process_state() -> None:
    """Remove stale state left by prior test runs.

    When the engine runs inside ``pytest_sessionfinish`` (i.e. self-
    mutation), the outer test session may have polluted ``sys.meta_path``
    with stale ``MutatingFinder`` instances and ``sys.modules`` with
    temporary modules from test fixtures.  Both must be cleaned up before
    inner ``pytest.main()`` calls can work correctly.
    """
    from pytest_leela.import_hook import MutatingFinder

    # 1. Remove stale MutatingFinders from sys.meta_path
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, MutatingFinder)]

    # 2. Remove modules loaded from temp directories (left by test
    #    fixtures that create throwaway target files).
    tmp_prefix = tempfile.gettempdir() + os.sep
    stale = [
        name
        for name, mod in sys.modules.items()
        if mod is not None
        and (f := getattr(mod, "__file__", None)) is not None
        and f.startswith(tmp_prefix)
    ]
    for name in stale:
        sys.modules.pop(name, None)


class Engine:
    """Orchestrates a full mutation testing run."""

    def __init__(
        self,
        use_types: bool = True,
        use_coverage: bool = True,
        enabled_categories: tuple[str, ...] | list[str] | None = None,
        index: IndexDB | None = None,
        on_progress: Callable[[EngineProgress], None] | None = None,
    ) -> None:
        self.use_types = use_types
        self.use_coverage = use_coverage
        self._enabled_categories = enabled_categories
        self._allowed_keys = build_allowed_keys(enabled_categories)
        self.index = index
        self._on_progress = on_progress

    def run(
        self,
        target_files: list[str],
        test_dir: str | None = None,
        limits: ResourceLimits | None = None,
        diff_base: str | None = None,
        test_node_ids: list[str] | None = None,
        pre_coverage_map: CoverageMap | None = None,
    ) -> RunResult:
        start = time.monotonic()

        _clean_process_state()

        if limits is not None:
            apply_limits(limits)

        # 1-4. For each target file: read source, find mutation points, enrich types
        all_mutants: list[Mutant] = []
        target_sources: dict[str, str] = {}
        module_to_file: dict[str, str] = {}
        # Maps (file_path, lineno) -> symbol_id for every mutation point.
        # Built alongside mutant generation so the persistence loop can
        # resolve a mutant to its owning symbol without re-querying.
        mutant_to_symbol: dict[int, str] = {}
        # Per-file symbol map for warm-cache lookups.
        file_symbols: dict[str, dict[int, str]] = {}
        # Per-file source hash for cache lookups.
        file_source_hash: dict[str, str] = {}
        total_pruned = 0
        total_enrichment_stats = EnrichmentStats()
        mutant_id = 0

        for file_path in target_files:
            abs_path = os.path.abspath(file_path)
            with open(abs_path) as f:
                source = f.read()

            module_name = _module_name_from_path(abs_path)
            target_sources[module_name] = source
            module_to_file[module_name] = abs_path

            # Reconcile the file against the index (if any) so symbols
            # are upserted and removed symbols are cascaded out.
            if self.index is not None:
                self.index.reconcile_file(abs_path, source)

            # Build a per-file lineno -> symbol_id map from the freshly
            # extracted symbols. Used to resolve each mutant to its
            # owning symbol in the persistence loop below.
            # Most-specific symbol wins: a method (narrow range) beats
            # the enclosing class (wide range), so method-level mutants
            # are attributed to the method, not the class. This matches
            # how the daemon's reconcile_file would assign them when
            # each is a separate row in the ``symbols`` table.
            symbols = extract_symbols(abs_path, source)
            sorted_symbols = sorted(
                symbols.items(), key=lambda kv: kv[1].end_line - kv[1].start_line
            )
            lineno_to_symbol: dict[int, str] = {}
            for sid, sym in sorted_symbols:
                for ln in range(sym.start_line, sym.end_line + 1):
                    if ln not in lineno_to_symbol:
                        lineno_to_symbol[ln] = sid
            file_symbols[abs_path] = lineno_to_symbol
            file_source_hash[abs_path] = hashlib.sha256(
                source.encode("utf-8")
            ).hexdigest()

            # AST analysis
            points = find_mutation_points(source, abs_path, module_name)

            # Type extraction
            points, file_stats = enrich_mutation_points(source, points)
            total_enrichment_stats = total_enrichment_stats + file_stats

            # Track pruned count
            total_pruned += count_pruned(
                points, self.use_types, allowed_keys=self._allowed_keys
            )

            # Generate mutants
            for point in points:
                for replacement_op in mutations_for(
                    point, self.use_types, allowed_keys=self._allowed_keys
                ):
                    all_mutants.append(
                        Mutant(
                            point=point,
                            replacement_op=replacement_op,
                            mutant_id=mutant_id,
                        )
                    )
                    mutant_to_symbol[mutant_id] = lineno_to_symbol.get(point.lineno, "")
                    mutant_id += 1

        total_mutants = len(all_mutants) + total_pruned

        # 6. If diff_base: filter to only changed lines
        if diff_base is not None:
            diff_lines = changed_lines(diff_base)
            all_mutants = [
                m
                for m in all_mutants
                if m.point.file_path in diff_lines
                and m.point.lineno in diff_lines[m.point.file_path]
            ]

        # 7. Collect per-test coverage if enabled.
        # If a pre-built coverage map was provided (from the outer session),
        # skip the expensive re-run of all tests.
        coverage_map: CoverageMap | None = None
        if pre_coverage_map is not None:
            coverage_map = pre_coverage_map
        elif self.use_coverage:
            coverage_map = collect_coverage(
                target_files, test_dir, test_node_ids=test_node_ids
            )

        # 8. Precompute user modules once before the mutation loop.
        # This turns O(N) scans of sys.modules into O(K) targeted pops
        # inside each run_tests_for_mutant call (K << N).
        known_user_modules = precompute_user_modules()
        test_times = coverage_map.test_times if coverage_map is not None else None

        # Cache key: same source + same test set -> cache hit.
        test_set_hash = hashlib.sha256(
            "\n".join(sorted(test_node_ids or [])).encode("utf-8")
        ).hexdigest()

        # 9. Run each mutant
        results: list[MutantResult] = []
        analyzed_symbols: set[str] = set()
        # Track every symbol that lives in any of the target files so
        # the post-loop pass can mark them clean. Without this set,
        # small helper methods (no mutation points generated) stay
        # dirty forever because the main loop never visits them.
        all_target_symbols: set[str] = set()
        for file_path in target_files:
            fap = os.path.abspath(file_path)
            for sid in set(file_symbols.get(fap, {}).values()):
                all_target_symbols.add(sid)
        try:
            for mutant in all_mutants:
                # Check memory limits
                if limits is not None and not is_memory_ok(limits):
                    break

                # Resolve the owning symbol for this mutant.
                abs_path = os.path.abspath(mutant.point.file_path)
                symbol_id = mutant_to_symbol.get(mutant.mutant_id, "")
                source_hash = file_source_hash.get(abs_path, "")

                # Look up relevant tests from coverage map
                test_ids: list[str] | None = None
                if coverage_map is not None:
                    covered = coverage_map.tests_for(
                        mutant.point.file_path, mutant.point.lineno
                    )
                    if covered:
                        test_ids = sorted(covered)

                # Fallback: use all session tests when no coverage info available
                if test_ids is None and test_node_ids is not None:
                    test_ids = test_node_ids

                # Warm cache: if the index has a clean result for this exact
                # (symbol, source, test_set) triple, reuse it without
                # re-executing tests. The test caller observed the same
                # mutation score in fewer cycles.
                cached: MutantResult | None = None
                if self.index is not None and symbol_id and source_hash:
                    cached = self.index.get_cached_result(
                        symbol_id, source_hash, test_set_hash, mutant
                    )

                if cached is not None:
                    result = cached
                    # Warm-cache hit: do NOT rewrite the row. The test
                    # ``it_skips_already_analyzed_mutants`` relies on the
                    # ``analyzed_at`` timestamp being unchanged across warm
                    # runs. We still record the symbol as analyzed so the
                    # post-loop ``mark_symbol_clean`` covers it.
                    if symbol_id:
                        analyzed_symbols.add(symbol_id)
                else:
                    result = run_tests_for_mutant(
                        mutant,
                        target_sources,
                        module_to_file,
                        test_ids=test_ids,
                        test_dir=test_dir,
                        known_user_modules=known_user_modules,
                        test_times=test_times,
                    )
                    # Persist the cold-cache result.
                    if self.index is not None and symbol_id and source_hash:
                        self.index.write_mutant_result(
                            symbol_id,
                            mutant,
                            result,
                            source_hash,
                            test_set_hash,
                        )
                        analyzed_symbols.add(symbol_id)
                results.append(result)
        finally:
            # Flip every symbol in the target files to clean. This
            # covers three cases:
            #
            # 1. Cold cache: just wrote fresh results, symbol is in
            #    ``analyzed_symbols``.
            # 2. Warm cache: all mutants were cache hits, no writes
            #    but symbol was fully covered (also in
            #    ``analyzed_symbols``).
            # 3. No mutants: a small helper method (``def __exit__:
            #    pass``) generates zero mutation points, so the
            #    main loop never visits it. Without this pass,
            #    ``reconcile_file`` would leave it dirty forever
            #    and the daemon would loop.
            #
            # The ``finally`` ensures the daemon's reanalysis
            # thread always advances the dirty set even if the
            # main loop crashes partway through (e.g. an inner
            # ``pytest.main()`` raises a database lock error).
            if self.index is not None:
                for sid in all_target_symbols:
                    self.index.mark_symbol_clean(sid)

        wall_time = time.monotonic() - start

        return RunResult(
            target_files=target_files,
            total_mutants=total_mutants,
            mutants_tested=len(results),
            mutants_pruned=total_pruned,
            results=results,
            wall_time_seconds=wall_time,
            coverage_map=coverage_map,
            target_sources={
                module_to_file[mod]: src for mod, src in target_sources.items()
            },
            enrichment_stats=total_enrichment_stats,
            diff_base=diff_base,
        )
