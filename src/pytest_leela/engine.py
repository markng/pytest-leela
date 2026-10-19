"""Mutation testing orchestrator."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# On Python <=3.13, this lets an invalid ``BitOr -> BitAnd`` annotation
# mutation fail while the definition is executed. Python 3.14 uses PEP 649
# lazy annotations instead; the annotation-policy regression reads supported
# hints to exercise that failure. See ``pytest_leela.import_hook`` for why
# its ``compile()`` call must also remain unflagged.

import ntpath  # noqa: F401 — keep in sys.modules; Python 3.13 pathlib lazily

#                              imports ntpath from PurePath.__init__, and
#                              pytest's assertion rewriter calls PurePath in
#                              find_spec — if ntpath is absent the import
#                              re-enters find_spec causing infinite recursion.
import ast
import hashlib
import importlib.machinery
import importlib.metadata
import os
import sys
import sysconfig
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.coverage_tracker import collect_coverage
from pytest_leela.git_diff import changed_lines
from pytest_leela.index import IndexDB, compute_test_set_hash, extract_symbols
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


class _TestDependencies:
    """Per-run static test-file dependency graph, never importing user code.

    Selected tests share a fingerprint at file granularity, including local
    transitive imports and ancestor fixtures/config. Unknown origins, dynamic
    imports, and unavailable inputs disable caching for the affected selection.
    Non-Python resources and runtime state are outside this static contract.
    """

    def __init__(self, test_dir: str | None) -> None:
        self.root = Path(test_dir).resolve() if test_dir is not None else None
        self._scans: dict[Path, set[Path] | None] = {}
        self._hashes: dict[tuple[str, ...], str | None] = {}
        self._files: dict[tuple[str, ...], set[Path] | None] = {}
        self.environment = _cache_environment_identity()
        self.environment_roots = {
            Path(p).resolve() for p in sysconfig.get_paths().values()
            if p and p != sysconfig.get_path("data") and p != sysconfig.get_path("scripts")
        }

    def _resolve_import(self, name: str, file: Path) -> set[Path] | None:
        """Resolve static module origins; ambiguity is an uncached boundary."""
        parts = name.split(".")
        if parts[0] in sys.builtin_module_names:
            return set()
        if importlib.machinery.FrozenImporter.find_spec(parts[0]) is not None:
            return set()
        # pytest inserts test/package directories; sys.path supplies src-layout
        # roots. Checking each candidate avoids guessing between local origins.
        roots = {str(file.parent), *(os.path.abspath(p) for p in sys.path)}
        if self.root is not None:
            roots.add(str(self.root.parent))
        origins: dict[Path, set[Path]] = {}
        for root in sorted(roots):
            paths = [root]
            files: set[Path] = set()
            for i in range(len(parts)):
                spec = importlib.machinery.PathFinder.find_spec(".".join(parts[:i + 1]), paths)
                if spec is None or spec.origin is None:
                    break
                origin = Path(spec.origin).resolve()
                files.add(origin)
                if i == len(parts) - 1:
                    origins[origin] = files
                paths = list(spec.submodule_search_locations or [])
        if len(origins) != 1:
            return None
        origin, files = next(iter(origins.items()))
        if any(origin.is_relative_to(p) for p in self.environment_roots):
            return set()
        if any(p.suffix != ".py" for p in files):
            return None
        return files

    def _scan(self, file: Path) -> set[Path] | None:
        if file in self._scans:
            return self._scans[file]
        try:
            content = file.read_bytes()
            tree = ast.parse(content, filename=str(file))
            dependencies: set[Path] = set()
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"__import__", "eval", "exec"}
                ):
                    self._scans[file] = None
                    return None
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "pytest_plugins"
                    for target in node.targets
                ):
                    try:
                        plugins = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        self._scans[file] = None
                        return None
                    if isinstance(plugins, str):
                        plugins = [plugins]
                    if not isinstance(plugins, (list, tuple)) or not all(
                        isinstance(p, str) for p in plugins
                    ):
                        self._scans[file] = None
                        return None
                    for plugin in plugins:
                        resolved_plugin = self._resolve_import(plugin, file)
                        if resolved_plugin is None:
                            self._scans[file] = None
                            return None
                        dependencies.update(resolved_plugin)
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        parent = file.parent
                        for _ in range(node.level - 1):
                            parent = parent.parent
                        base = parent.joinpath(*(node.module or "").split("."))
                        candidates = [base.with_suffix(".py"), base / "__init__.py"] if node.module else []
                        found = [p.resolve() for p in candidates if p.is_file()]
                        if node.module and len(found) != 1:
                            self._scans[file] = None
                            return None
                        dependencies.update(found)
                        for alias in node.names:
                            child = base / alias.name
                            children = {p.resolve() for p in (child.with_suffix(".py"), child / "__init__.py") if p.is_file()}
                            if not node.module and not children:
                                self._scans[file] = None
                                return None
                            dependencies.update(children)
                        continue
                    names = [node.module] if node.module else []
                for name in names:
                    # Dynamic import APIs can be aliased, so a file importing
                    # them has no proven static dependency boundary.
                    if name.split(".", 1)[0] in {"importlib", "runpy"}:
                        self._scans[file] = None
                        return None
                    resolved = self._resolve_import(name, file)
                    if resolved is None:
                        self._scans[file] = None
                        return None
                    dependencies.update(resolved)
                    # ``from package import submodule`` may import a module
                    # or just a value. Fingerprint existing local submodules.
                    if isinstance(node, ast.ImportFrom):
                        for package_file in resolved:
                            if package_file.name == "__init__.py":
                                for alias in node.names:
                                    child = package_file.parent / alias.name
                                    dependencies.update(p.resolve() for p in (child.with_suffix(".py"), child / "__init__.py") if p.is_file())
            self._scans[file] = dependencies
            return dependencies
        except (OSError, SyntaxError):
            self._scans[file] = None
            return None

    def fingerprint(self, test_ids: list[str] | None) -> str | None:
        selection = tuple(sorted(test_ids or []))
        if selection in self._hashes:
            return self._hashes[selection]
        value = self._fingerprint(selection)
        self._hashes[selection] = value
        return value

    def dependency_files(self, test_ids: list[str] | None) -> set[Path] | None:
        """Local ``.py`` inputs of the selection, or ``None`` when uncached.

        The mutation runner drops cached bytecode that predates these inputs so
        a same-size, same-integer-second source edit cannot execute stale
        ``.pyc`` artifacts. ``None`` marks the same unknown-input boundary as
        ``fingerprint``; the runner then falls back to its own selection scope.
        """
        files = self._selection_files(tuple(sorted(test_ids or [])))
        if files is None:
            return None
        return {path for path in files if path.suffix == ".py"}

    def _selection_files(self, selection: tuple[str, ...]) -> set[Path] | None:
        if selection in self._files:
            return self._files[selection]
        value = self._collect_selection_files(selection)
        self._files[selection] = value
        return value

    def _collect_selection_files(self, selection: tuple[str, ...]) -> set[Path] | None:
        if self.root is None or not self.root.is_dir():
            return None
        try:
            if selection:
                files = {Path(t.split("::", 1)[0]).resolve() for t in selection}
                if any(not p.is_relative_to(self.root) or not p.is_file() for p in files):
                    return None
            else:
                # No coverage/session selection: pytest really runs the tree.
                files = set()
                def fail(error: OSError) -> None:
                    raise error
                for directory, _, names in os.walk(self.root, onerror=fail):
                    files.update(Path(directory) / name for name in names if name.endswith(".py"))
            config: set[Path] = set()
            for file in files:
                for parent in file.parents:
                    for name in ("__init__.py", "conftest.py", "pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
                        path = parent / name
                        if path.is_file():
                            config.add(path)
            pending = list(files | {p for p in config if p.suffix == ".py"})
            visited: set[Path] = set()
            while pending:
                file = pending.pop()
                if file in visited:
                    continue
                visited.add(file)
                dependencies = self._scan(file)
                if dependencies is None:
                    return None
                pending.extend(dependencies - visited)
            return visited | config
        except OSError:
            return None

    def _fingerprint(self, selection: tuple[str, ...]) -> str | None:
        files = self._selection_files(selection)
        if files is None:
            return None
        try:
            records = [
                f"{path}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
                for path in sorted(files)
            ]
            return compute_test_set_hash([self.environment, *selection, *records])
        except OSError:
            return None


# Bool-only rows predating PR12 cannot prove the baseline/crash/skip contract.
_EXECUTION_CLASSIFICATION_REVISION = "baseline-tristate-pr12-v1"


def _cache_environment_identity() -> str:
    """Partition persisted cache by interpreter and installed package versions."""
    packages = sorted(
        f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
    )
    return compute_test_set_hash([
        _EXECUTION_CLASSIFICATION_REVISION,
        str(Path(sys.executable).resolve()), sys.version, sys.platform,
        str(sys.implementation.cache_tag), *packages,
    ])

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

        # In diff mode only changed lines are mutated, so candidates, the
        # pruned count and enrichment stats are taken over those lines too.
        diff_lines = changed_lines(diff_base) if diff_base is not None else None

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

            if diff_lines is not None:
                file_lines = diff_lines.get(abs_path, set())
                points = [p for p in points if p.lineno in file_lines]

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

        test_dependencies = _TestDependencies(test_dir) if self.index is not None else None

        mutant_test_ids = [
            self._tests_for(mutant, coverage_map, test_node_ids)
            for mutant in all_mutants
        ]

        # 9. Prove the tests pass with no mutation applied, through the same
        # in-process path every mutant uses.  Otherwise a test that fails
        # for an unrelated reason would score every mutant as killed.
        if all_mutants:
            self._check_baseline(
                mutant_test_ids, test_dir, known_user_modules, scope, test_dependencies
            )

        # 10. Run each mutant
        results: list[MutantResult] = []
        # Origins whose derived bytecode has already been prepared this run, so
        # each selection recompiles at most once while executing mutants.
        prepared_dependencies: set[Path] = set()
        symbol_counts = Counter(mutant_to_symbol[m.mutant_id] for m in all_mutants)
        current_symbol: str | None = None
        # A symbol becomes clean only after every eligible mutant attributed to
        # it completed, either by a valid cache hit or a successful test run.
        # Symbols with no eligible mutants are complete without execution.
        all_target_symbols: set[str] = set()
        for file_path in target_files:
            fap = os.path.abspath(file_path)
            all_target_symbols.update(file_symbols.get(fap, {}).values())
        remaining_by_symbol = Counter(symbol_counts)
        completed_symbols: set[str] = set()
        errored_symbols: set[str] = set()
        if self.index is not None:
            for sid in all_target_symbols:
                if remaining_by_symbol[sid] == 0:
                    self.index.mark_symbol_clean(sid)
                    completed_symbols.add(sid)

        for mutant, test_ids in zip(all_mutants, mutant_test_ids):
            # Check memory limits
            if limits is not None and not is_memory_ok(limits):
                break

            # Resolve the owning symbol for this mutant.
            abs_path = os.path.abspath(mutant.point.file_path)
            symbol_id = mutant_to_symbol.get(mutant.mutant_id, "")
            source_hash = file_source_hash.get(abs_path, "")

            # Only the selected test files and their transitive inputs
            # determine validity. Unresolved dependencies bypass the cache.
            test_set_hash = (
                test_dependencies.fingerprint(test_ids)
                if test_dependencies is not None else None
            )

            if self._on_progress is not None and symbol_id != current_symbol:
                current_symbol = symbol_id
                self._on_progress(EngineProgress(
                    kind="symbol-start", file_path=abs_path,
                    lineno=mutant.point.lineno, symbol_id=symbol_id or None,
                    symbol_short=symbol_id.rsplit(":", 1)[-1],
                    n_mutants_in_symbol=symbol_counts[symbol_id],
                ))

            cached: MutantResult | None = None
            if self.index is not None and symbol_id and test_set_hash is not None:
                cached = self.index.get_cached_result(
                    symbol_id, source_hash, test_set_hash, mutant
                )

            if cached is not None:
                result = cached
                status = "cache-hit"
            else:
                # Local inputs whose cached bytecode must be current before the
                # selected tests run; ``None`` hands scope selection to the runner.
                # Prepared once per run across executing mutants only.
                dependency_files = (
                    test_dependencies.dependency_files(test_ids)
                    if test_dependencies is not None else None
                )
                try:
                    result = run_tests_for_mutant(
                        mutant,
                        target_sources,
                        module_to_file,
                        test_ids=test_ids,
                        test_dir=test_dir,
                        known_user_modules=known_user_modules,
                        test_times=test_times,
                        scope=scope,
                        dependency_files=dependency_files,
                        prepared_dependencies=prepared_dependencies,
                    )
                except Exception:
                    # The runner did not produce a result for this mutant,
                    # so its symbol must remain pending for reanalysis.
                    if self.index is not None and symbol_id:
                        self.index.mark_symbol_error(symbol_id)
                    if self._on_progress is not None:
                        self._on_progress(EngineProgress(
                            kind="mutant", file_path=abs_path,
                            lineno=mutant.point.lineno,
                            op=f"{mutant.point.node_type}:{mutant.replacement_op}",
                            status="error", symbol_id=symbol_id or None,
                        ))
                    raise
                status = result.status
                if (
                    result.error is None and self.index is not None
                    and symbol_id and test_set_hash is not None
                ):
                    self.index.write_mutant_result(
                        symbol_id, mutant, result, source_hash, test_set_hash,
                    )
            results.append(result)
            if symbol_id:
                if result.error is not None:
                    errored_symbols.add(symbol_id)
                    if self.index is not None:
                        self.index.mark_symbol_error(symbol_id)
                remaining_by_symbol[symbol_id] -= 1
                if (
                    self.index is not None
                    and remaining_by_symbol[symbol_id] == 0
                    and symbol_id not in completed_symbols
                    and symbol_id not in errored_symbols
                ):
                    self.index.mark_symbol_clean(symbol_id)
                    completed_symbols.add(symbol_id)
            if self._on_progress is not None:
                self._on_progress(EngineProgress(
                    kind="mutant", file_path=abs_path,
                    lineno=mutant.point.lineno,
                    op=f"{mutant.point.node_type}:{mutant.replacement_op}",
                    status=status, symbol_id=symbol_id or None,
                    killing_test=result.killing_test,
                ))

        if self._on_progress is not None:
            self._on_progress(EngineProgress(kind="done"))

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
        test_dependencies: _TestDependencies | None = None,
    ) -> None:
        """Raise BaselineFailure unless every selected test passes unmutated.

        Runs the union of all mutants' tests once, with no import hook
        installed, so nothing can be mutated, and without ``-x``, so the
        failure names every failing test.
        """
        baseline_ids: list[str] | None = None
        if all(ids is not None for ids in mutant_test_ids):
            baseline_ids = sorted({t for ids in mutant_test_ids for t in ids or ()})
        dependency_files = (
            test_dependencies.dependency_files(baseline_ids)
            if test_dependencies is not None else None
        )
        session = run_baseline(
            baseline_ids, test_dir, known_user_modules, scope,
            dependency_files=dependency_files,
        )
        failures = session.failures()
        error = session.error()
        if not failures and error is None:
            return
        reason = f"failed: {', '.join(failures)}" if failures else error
        raise BaselineFailure(
            f"tests do not pass with no mutation applied ({reason}); "
            "mutant results would be meaningless, so no mutants were run"
        )
