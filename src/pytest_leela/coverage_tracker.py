"""Per-test line coverage via sys.settrace."""

# NOTE: Do NOT add from __future__ import annotations here.
# Without that flag, every annotation is evaluated at function
# def time, so a BitOr->BitAnd mutation on a type annotation
# (e.g. "int | None" -> "int & None") raises TypeError at
# import — which is exactly the test-killing signal mutation
# testing depends on. See the top-of-file note in
# pytest_leela.import_hook for the full reasoning.
#

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
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
        self.test_times: dict[str, float] = {}
        self._test_start: float = 0.0

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        self._test_start = time.monotonic()
        self.tracer.start()

    def pytest_runtest_teardown(
        self, item: pytest.Item, nextitem: pytest.Item | None
    ) -> None:
        lines = self.tracer.stop()
        test_id = item.nodeid
        self.test_times[test_id] = time.monotonic() - self._test_start
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
        "--tb=no",
        "-q",
        "--no-header",
        "--override-ini=addopts=",
        "-p",
        "no:leela",
        "-p",
        "no:leela-benchmark",
        "--capture=sys",
    ]

    if test_node_ids:
        args.extend(test_node_ids)
    elif test_dir:
        args.append(test_dir)

    if extra_args:
        args.extend(extra_args)

    # Run pytest with our coverage plugin (suppress noisy output)
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        pytest.main(args, plugins=[plugin])

    plugin.coverage_map.test_times = plugin.test_times
    return plugin.coverage_map


def collect_coverage_subprocess(
    target_files: list[str],
    test_node_ids: list[str] | None = None,
    cwd: str | None = None,
) -> CoverageMap:
    """Run the coverage collector in a fresh subprocess.

    A standalone entry point invoked by
    ``coverage_tracker.collect_coverage_subprocess`` from a
    fresh Python interpreter (not the one hosting the daemon)
    so pytest discovers its rootdir from ``cwd`` and finds the
    project's ``pyproject.toml``. The in-process variant
    (``collect_coverage``) inherits the outer pytest's state,
    which is wrong when called from inside another test run.

    Returns an empty ``CoverageMap`` if ``target_files`` is empty
    or the subprocess fails to launch — callers should treat
    empty coverage as "no tests ran" rather than a hard error.
    """
    if not target_files:
        return CoverageMap()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as out:
        output_path: str = out.name
    try:
        cmd = [
            sys.executable,
            "-m",
            "pytest_leela.coverage_collector",
            "--output",
            output_path,
        ]
        for target_file in target_files:
            cmd.extend(["--target", target_file])
        for tid in test_node_ids or []:
            cmd.extend(["--test", tid])
        subprocess.run(
            cmd,
            cwd=cwd,
            check=True,
            capture_output=True,
        )
        with open(output_path) as f:
            payload = json.load(f)
    except (OSError, subprocess.CalledProcessError):
        return CoverageMap()
    finally:
        try:
            os.unlink(output_path)
        except OSError:
            pass

    cov = CoverageMap()
    line_to_tests: dict[tuple[str, int], set[str]] = {}
    for key, tests in payload.get("line_to_tests", {}).items():
        # key is "file_path:lineno"
        if ":" in key:
            file_part, _, line_part = key.rpartition(":")
            try:
                line_no = int(line_part)
            except ValueError:
                continue
            line_to_tests[(file_part, line_no)] = set(tests)
    cov.line_to_tests = line_to_tests
    cov.test_times = dict(payload.get("test_times", {}))
    return cov
