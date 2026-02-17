"""pytest plugin entry point for leela mutation testing."""

from __future__ import annotations

import glob as _glob
import os
from pathlib import Path

from pytest_leela.engine import Engine
from pytest_leela.git_diff import changed_files
from pytest_leela.output import format_terminal_report
from pytest_leela.resources import ResourceLimits


def pytest_addoption(parser):  # type: ignore[no-untyped-def]
    group = parser.getgroup("leela", "mutation testing")
    group.addoption(
        "--leela", action="store_true", default=False, help="Enable mutation testing"
    )
    group.addoption(
        "--diff", default=None, help="Only mutate files changed since this git ref"
    )
    group.addoption(
        "--target", default=None, help="Specific file/directory to mutate"
    )
    group.addoption(
        "--max-cores", type=int, default=None, help="Max CPU cores"
    )
    group.addoption(
        "--max-memory", type=int, default=None, help="Max memory percent"
    )
    group.addoption(
        "--leela-benchmark",
        action="store_true",
        default=False,
        help="Run benchmark mode",
    )


def pytest_configure(config):  # type: ignore[no-untyped-def]
    if config.getoption("leela", default=False):
        config.pluginmanager.register(LeelaPlugin(config), "leela-plugin")
    elif config.getoption("leela_benchmark", default=False):
        from pytest_leela.benchmark import BenchmarkPlugin

        config.pluginmanager.register(BenchmarkPlugin(config), "leela-benchmark")


def _find_target_files(target: str) -> list[str]:
    """Resolve a --target path to a list of .py files."""
    target_path = os.path.abspath(target)
    if os.path.isfile(target_path) and target_path.endswith(".py"):
        return [target_path]
    if os.path.isdir(target_path):
        return sorted(
            os.path.abspath(p)
            for p in _glob.glob(os.path.join(target_path, "**", "*.py"), recursive=True)
            if not os.path.basename(p).startswith("__")
        )
    return []


def _find_default_targets(rootpath: Path) -> list[str]:
    """Look for common source directories to use as default targets."""
    for candidate in ("target", "src"):
        candidate_dir = rootpath / candidate
        if candidate_dir.is_dir():
            return sorted(
                os.path.abspath(str(p))
                for p in candidate_dir.rglob("*.py")
                if not p.name.startswith("__")
            )
    return []


class LeelaPlugin:
    def __init__(self, config):  # type: ignore[no-untyped-def]
        self.config = config

    def pytest_sessionfinish(self, session, exitstatus):  # type: ignore[no-untyped-def]
        if exitstatus != 0:
            return

        target = self.config.getoption("target", default=None)
        diff_base = self.config.getoption("diff", default=None)

        # Determine target files
        if target:
            target_files = _find_target_files(target)
        elif diff_base:
            target_files = changed_files(diff_base)
        else:
            target_files = _find_default_targets(session.config.rootpath)

        if not target_files:
            return

        test_dir = str(session.config.rootpath / "tests")

        limits = ResourceLimits(
            max_cores=self.config.getoption("max_cores", default=None),
            max_memory_percent=self.config.getoption("max_memory", default=None),
        )

        engine = Engine()
        result = engine.run(
            target_files, test_dir, limits=limits, diff_base=diff_base
        )

        report = format_terminal_report(result)

        tw = (
            session.config.get_terminal_writer()
            if hasattr(session.config, "get_terminal_writer")
            else None
        )
        if tw:
            tw.write(report)
        else:
            print(report)
