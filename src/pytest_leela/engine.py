"""Mutation testing orchestrator."""

from __future__ import annotations

import ntpath  # noqa: F401 — keep in sys.modules; Python 3.13 pathlib lazily

#                              imports ntpath from PurePath.__init__, and
#                              pytest's assertion rewriter calls PurePath in
#                              find_spec — if ntpath is absent the import
#                              re-enters find_spec causing infinite recursion.
import os
import sys
import tempfile
import time

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.coverage_tracker import collect_coverage
from pytest_leela.git_diff import changed_lines
from pytest_leela.models import (
    CoverageMap,
    EnrichmentStats,
    Mutant,
    MutantResult,
    RunResult,
)
from pytest_leela.operators import build_allowed_keys, count_pruned, mutations_for
from pytest_leela.resources import ResourceLimits, apply_limits, is_memory_ok
from pytest_leela.runner import (
    ProjectModuleScope,
    precompute_user_modules,
    run_baseline,
    run_tests_for_mutant,
)
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
    #    fixtures that create throwaway target files).  A project or
    #    virtualenv that itself lives under the temp directory is not a
    #    fixture: its installed packages and leela's own modules stay.
    tmp_scope = ProjectModuleScope(tempfile.gettempdir())
    for name in tmp_scope.module_names():
        sys.modules.pop(name, None)


class BaselineFailure(Exception):
    """The tests fail with no mutation applied.

    Every mutant result would then be untrustworthy: a test failing for a
    reason unrelated to the mutant (a fixture that cannot run in-process,
    a broken import) would score every mutant as killed.
    """


class Engine:
    """Orchestrates a full mutation testing run."""

    def __init__(
        self,
        use_types: bool = True,
        use_coverage: bool = True,
        enabled_categories: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self.use_types = use_types
        self.use_coverage = use_coverage
        self._enabled_categories = enabled_categories
        self._allowed_keys = build_allowed_keys(enabled_categories)

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
        total_pruned = 0
        total_enrichment_stats = EnrichmentStats()
        mutant_id = 0

        # In diff mode only changed lines are mutated, so candidates and the
        # pruned count are taken over those lines too.
        diff_lines = changed_lines(diff_base) if diff_base is not None else None

        for file_path in target_files:
            abs_path = os.path.abspath(file_path)
            with open(abs_path) as f:
                source = f.read()

            module_name = _module_name_from_path(abs_path)
            target_sources[module_name] = source
            module_to_file[module_name] = abs_path

            # AST analysis
            points = find_mutation_points(source, abs_path, module_name)

            # Type extraction
            points, file_stats = enrich_mutation_points(source, points)
            total_enrichment_stats = total_enrichment_stats + file_stats

            if diff_lines is not None:
                file_lines = diff_lines.get(abs_path, set())
                points = [p for p in points if p.lineno in file_lines]

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
                    mutant_id += 1

        total_mutants = len(all_mutants) + total_pruned

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
        scope = ProjectModuleScope()
        known_user_modules = precompute_user_modules(scope)
        test_times = coverage_map.test_times if coverage_map is not None else None

        mutant_test_ids = [
            self._tests_for(mutant, coverage_map, test_node_ids)
            for mutant in all_mutants
        ]

        # 9. Prove the tests pass with no mutation applied, through the same
        # in-process path every mutant uses.  Otherwise a test that fails
        # for an unrelated reason would score every mutant as killed.
        if all_mutants:
            self._check_baseline(mutant_test_ids, test_dir, known_user_modules, scope)

        # 10. Run each mutant
        results: list[MutantResult] = []
        for mutant, test_ids in zip(all_mutants, mutant_test_ids):
            # Check memory limits
            if limits is not None and not is_memory_ok(limits):
                break

            result = run_tests_for_mutant(
                mutant,
                target_sources,
                module_to_file,
                test_ids=test_ids,
                test_dir=test_dir,
                known_user_modules=known_user_modules,
                test_times=test_times,
                scope=scope,
            )
            results.append(result)

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

    @staticmethod
    def _tests_for(
        mutant: Mutant,
        coverage_map: CoverageMap | None,
        test_node_ids: list[str] | None,
    ) -> list[str] | None:
        """Tests to run against *mutant*; None means the whole ``test_dir``."""
        if coverage_map is not None:
            covered = coverage_map.tests_for(
                mutant.point.file_path, mutant.point.lineno
            )
            if covered:
                return sorted(covered)
        # Fallback: use all session tests when no coverage info available
        return test_node_ids

    @staticmethod
    def _check_baseline(
        mutant_test_ids: list[list[str] | None],
        test_dir: str | None,
        known_user_modules: frozenset[str],
        scope: ProjectModuleScope,
    ) -> None:
        """Raise BaselineFailure unless every selected test passes unmutated.

        Runs the union of all mutants' tests once, with no import hook
        installed, so nothing can be mutated, and without ``-x``, so the
        failure names every failing test.
        """
        baseline_ids: list[str] | None = None
        if all(ids is not None for ids in mutant_test_ids):
            baseline_ids = sorted({t for ids in mutant_test_ids for t in ids or ()})
        session = run_baseline(baseline_ids, test_dir, known_user_modules, scope)
        failures = session.failures()
        error = session.error()
        if not failures and error is None:
            return
        reason = f"failed: {', '.join(failures)}" if failures else error
        raise BaselineFailure(
            f"tests do not pass with no mutation applied ({reason}); "
            "mutant results would be meaningless, so no mutants were run"
        )
