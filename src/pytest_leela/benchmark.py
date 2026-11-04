"""Benchmark mode — attribute speedup to each optimisation layer."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# On Python <=3.13, this lets an invalid ``BitOr -> BitAnd`` annotation
# mutation fail while the definition is executed. Python 3.14 uses PEP 649
# lazy annotations instead; the annotation-policy regression reads supported
# hints to exercise that failure. See ``pytest_leela.import_hook`` for why
# its ``compile()`` call must also remain unflagged.

from dataclasses import dataclass

import pytest

from pytest_leela.engine import Engine
from pytest_leela.plugin import _find_default_targets, _find_target_files


@dataclass
class _BenchmarkRow:
    label: str
    wall_time: float
    mutants_tested: int
    mutants_pruned: int


class BenchmarkPlugin:
    """Runs mutation testing under different configurations to show speedup attribution."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if exitstatus != 0:
            return

        targets = self.config.getoption("target", default=[])
        python_files = self.config.getini("python_files")
        if targets:
            target_files: list[str] = []
            for t in targets:
                target_files.extend(_find_target_files(t, python_files))
            target_files = sorted(set(target_files))
        else:
            target_files = _find_default_targets(session.config.rootpath, python_files)

        if not target_files:
            return

        test_dir = str(session.config.rootpath / "tests")

        configs: list[tuple[str, bool, bool]] = [
            ("No optimizations", False, False),
            ("+ per-test coverage", False, True),
            ("+ type-aware pruning", True, False),
            ("All optimizations", True, True),
        ]

        rows: list[_BenchmarkRow] = []
        for label, use_types, use_coverage in configs:
            engine = Engine(use_types=use_types, use_coverage=use_coverage)
            result = engine.run(target_files, test_dir)
            rows.append(
                _BenchmarkRow(
                    label=label,
                    wall_time=result.wall_time_seconds,
                    mutants_tested=result.mutants_tested,
                    mutants_pruned=result.mutants_pruned,
                )
            )

        report = _format_benchmark_report(rows)

        tw = (
            session.config.get_terminal_writer()
            if hasattr(session.config, "get_terminal_writer")
            else None
        )
        if tw:
            tw.write(report)
        else:
            print(report)


def _format_benchmark_report(rows: list[_BenchmarkRow]) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 70)
    lines.append("leela benchmark")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  {'Configuration':<30s} {'Time':>8s} {'Tested':>8s} {'Pruned':>8s}")
    lines.append("  " + "-" * 56)

    baseline = rows[0].wall_time if rows else 1.0

    for row in rows:
        speedup = baseline / row.wall_time if row.wall_time > 0 else 0.0
        suffix = "" if row is rows[0] else f"  ({speedup:.1f}x)"
        lines.append(
            f"  {row.label:<30s} {row.wall_time:>7.1f}s {row.mutants_tested:>8d} {row.mutants_pruned:>8d}{suffix}"
        )

    lines.append("")
    if len(rows) >= 2:
        total_speedup = baseline / rows[-1].wall_time if rows[-1].wall_time > 0 else 0.0
        lines.append(f"  Total speedup: {total_speedup:.1f}x")
        lines.append("")

    return "\n".join(lines)
