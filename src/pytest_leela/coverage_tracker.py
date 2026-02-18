"""Per-test line coverage via sys.settrace."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import threading
from typing import Any

import pytest

from pytest_leela.models import CoverageMap


class _LineTracer:
    """Trace function that records line execution for target files."""

    def __init__(self, target_files: set[str]) -> None:
        self.target_files = target_files
        self.lines_hit: set[tuple[str, int]] = set()
        self._active = False

    def start(self) -> None:
        self.lines_hit.clear()
        self._active = True
        sys.settrace(self._trace)
        threading.settrace(self._trace)

    def stop(self) -> set[tuple[str, int]]:
        sys.settrace(None)
        threading.settrace(None)
        self._active = False
        return self.lines_hit.copy()

    def _trace(self, frame: Any, event: str, arg: Any) -> Any:
        if not self._active:
            return None
        if event == "call":
            filename = frame.f_code.co_filename
            if filename in self.target_files:
                return self._trace_lines
            return None
        return None

    def _trace_lines(self, frame: Any, event: str, arg: Any) -> Any:
        if event == "line":
            filename = frame.f_code.co_filename
            if filename in self.target_files:
                self.lines_hit.add((filename, frame.f_lineno))
        return self._trace_lines


class CoveragePlugin:
    """pytest plugin that collects per-test coverage."""

    def __init__(self, target_files: set[str]) -> None:
        self.target_files = {os.path.abspath(f) for f in target_files}
        self.tracer = _LineTracer(self.target_files)
        self.coverage_map = CoverageMap()

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        self.tracer.start()

    def pytest_runtest_teardown(self, item: pytest.Item, nextitem: pytest.Item | None) -> None:
        lines = self.tracer.stop()
        test_id = item.nodeid
        for file_path, lineno in lines:
            self.coverage_map.add(file_path, lineno, test_id)


def collect_coverage(
    target_files: list[str],
    test_dir: str | None = None,
    extra_args: list[str] | None = None,
    test_node_ids: list[str] | None = None,
) -> CoverageMap:
    """Run all tests once, collecting per-test line coverage."""
    plugin = CoveragePlugin(set(target_files))

    args = [
        "--tb=no", "-q", "--no-header",
        "--override-ini=addopts=",
        "-p", "no:leela",
        "-p", "no:leela-benchmark",
        "--capture=sys",
    ]

    if test_node_ids:
        args.extend(test_node_ids)
    elif test_dir:
        args.append(test_dir)

    if extra_args:
        args.extend(extra_args)

    # Run pytest with our coverage plugin (suppress noisy output)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        pytest.main(args, plugins=[plugin])

    return plugin.coverage_map
