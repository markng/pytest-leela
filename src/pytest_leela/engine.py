"""Mutation testing orchestrator."""

from __future__ import annotations

import os
import sys
import tempfile
import time

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.coverage_tracker import collect_coverage
from pytest_leela.git_diff import changed_lines
from pytest_leela.models import CoverageMap, Mutant, MutantResult, RunResult
from pytest_leela.operators import count_pruned, mutations_for
from pytest_leela.resources import ResourceLimits, apply_limits, is_memory_ok
from pytest_leela.runner import run_tests_for_mutant
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
    sys.meta_path[:] = [
        f for f in sys.meta_path
        if not isinstance(f, MutatingFinder)
    ]

    # 2. Remove modules loaded from temp directories (left by test
    #    fixtures that create throwaway target files).
    tmp_prefix = tempfile.gettempdir() + os.sep
    stale = [
        name for name, mod in sys.modules.items()
        if mod is not None
        and getattr(mod, "__file__", None) is not None
        and mod.__file__.startswith(tmp_prefix)
    ]
    for name in stale:
        sys.modules.pop(name, None)


class Engine:
    """Orchestrates a full mutation testing run."""

    def __init__(self, use_types: bool = True, use_coverage: bool = True) -> None:
        self.use_types = use_types
        self.use_coverage = use_coverage

    def run(
        self,
        target_files: list[str],
        test_dir: str | None = None,
        limits: ResourceLimits | None = None,
        diff_base: str | None = None,
        test_node_ids: list[str] | None = None,
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
        mutant_id = 0

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
            points = enrich_mutation_points(source, points)

            # Track pruned count
            total_pruned += count_pruned(points, self.use_types)

            # Generate mutants
            for point in points:
                for replacement_op in mutations_for(point, self.use_types):
                    all_mutants.append(
                        Mutant(
                            point=point,
                            replacement_op=replacement_op,
                            mutant_id=mutant_id,
                        )
                    )
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

        # 7. Collect per-test coverage if enabled
        coverage_map: CoverageMap | None = None
        if self.use_coverage:
            coverage_map = collect_coverage(
                target_files, test_dir, test_node_ids=test_node_ids
            )

        # 8. Run each mutant
        results: list[MutantResult] = []
        for mutant in all_mutants:
            # Check memory limits
            if limits is not None and not is_memory_ok(limits):
                break

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

            result = run_tests_for_mutant(
                mutant,
                target_sources,
                module_to_file,
                test_ids=test_ids,
                test_dir=test_dir,
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
        )
