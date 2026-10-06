"""Plain pytest tests for ``pytest_leela.coverage_tracker``.

Mirrors the ``describe_coverage_tracker.py`` block-style tests
so the daemon's coverage collector (running under system Python
without ``pytest-describe``) can discover them. Exercises every
mutated path in ``coverage_tracker.py``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "src"),
)

from pytest_leela.coverage_tracker import (  # noqa: E402
    CoveragePlugin,
    _LineTracer,
    collect_coverage,
)
from pytest_leela.models import CoverageMap  # noqa: E402


# ---------------------------------------------------------------------------
# _LineTracer
# ---------------------------------------------------------------------------


def test_line_tracer_records_lines_in_target_file() -> None:
    """``start()`` enables tracing and records lines for target files.

    Kills mutations on ``_trace`` and ``_trace_lines``.
    """
    source = "def traced_func():\n    x = 1\n    y = 2\n    return x + y\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        tmp_path = f.name
    try:
        code = compile(source, tmp_path, "exec")
        namespace: dict = {}
        exec(code, namespace)

        tracer = _LineTracer(target_files={tmp_path})
        tracer.start()
        try:
            namespace["traced_func"]()
        finally:
            result = tracer.stop()

        hit_files = {f for f, _ in result}
        assert tmp_path in hit_files
        hit_lines = {line for f, line in result if f == tmp_path}
        assert 2 in hit_lines
        assert 3 in hit_lines
        assert 4 in hit_lines
    finally:
        os.unlink(tmp_path)


def test_line_tracer_stop_clears_on_restart() -> None:
    """``start()`` clears prior hits, ``stop()`` deactivates tracing.

    Kills mutations on ``stop()``.
    """
    tracer = _LineTracer(target_files={"/nonexistent/file.py"})
    tracer.lines_hit.add(("/nonexistent/file.py", 10))
    assert len(tracer.lines_hit) == 1

    tracer.start()
    try:
        assert tracer._active is True
        assert len(tracer.lines_hit) == 0
    finally:
        result = tracer.stop()
    assert tracer._active is False
    assert isinstance(result, set)


def test_line_tracer_inactive_returns_none() -> None:
    """Inactive ``_trace`` returns ``None`` for all events.

    Kills mutations on ``_trace`` that would skip the guard.
    """
    tracer = _LineTracer(target_files={"/some/file.py"})
    tracer._active = False
    assert tracer._trace(None, "call", None) is None
    assert tracer._trace(None, "line", None) is None


def test_line_tracer_inactive_does_not_trace_into_scopes() -> None:
    """Inactive tracer must not record line hits for any scope.

    If ``_trace`` returned a non-None value while inactive, line
    events would fire and ``lines_hit`` would not stay empty.
    """
    source = "def func():\n    x = 1\n    return x\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        tmp = f.name
    try:
        code = compile(source, tmp, "exec")
        ns: dict = {}
        exec(code, ns)

        tracer = _LineTracer(target_files={tmp})
        sys.settrace(tracer._trace)
        threading.settrace(tracer._trace)
        try:
            ns["func"]()
        finally:
            sys.settrace(None)
            threading.settrace(None)

        assert len(tracer.lines_hit) == 0
    finally:
        os.unlink(tmp)


def test_line_tracer_active_returns_none_for_non_call_events() -> None:
    """Active ``_trace`` returns ``None`` for events other than ``call``."""
    tracer = _LineTracer(target_files={"/some/file.py"})
    tracer._active = True
    assert tracer._trace(None, "line", None) is None
    assert tracer._trace(None, "return", None) is None


def test_line_tracer_active_returns_trace_lines_for_target_call() -> None:
    """Active ``_trace`` returns ``_trace_lines`` for target file calls."""
    tracer = _LineTracer(target_files={"/target/file.py"})
    tracer._active = True

    class FakeCode:
        co_filename = "/target/file.py"

    class FakeFrame:
        f_code = FakeCode()
        f_lineno = 1

    result = tracer._trace(FakeFrame(), "call", None)
    assert result is not None
    assert result.__func__ is _LineTracer._trace_lines


def test_line_tracer_active_returns_none_for_non_target_call() -> None:
    """Active ``_trace`` returns ``None`` for non-target file calls."""
    tracer = _LineTracer(target_files={"/target/file.py"})
    tracer._active = True

    class FakeCode:
        co_filename = "/other/file.py"

    class FakeFrame:
        f_code = FakeCode()

    assert tracer._trace(FakeFrame(), "call", None) is None


def test_line_tracer_excludes_non_target_files() -> None:
    """Calls into non-target files must not be sub-traced."""
    target_source = "def target_func():\n    return 42\n"
    other_source = "def other_func():\n    a = 1\n    b = 2\n    return a + b\n"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tf.write(target_source)
        tf.flush()
        target_path = tf.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as of:
        of.write(other_source)
        of.flush()
        other_path = of.name
    try:
        target_code = compile(target_source, target_path, "exec")
        other_code = compile(other_source, other_path, "exec")
        target_ns: dict = {}
        other_ns: dict = {}
        exec(target_code, target_ns)
        exec(other_code, other_ns)

        tracer = _LineTracer(target_files={target_path})
        tracer.start()
        try:
            other_ns["other_func"]()
            target_ns["target_func"]()
        finally:
            result = tracer.stop()

        hit_files = {f for f, _ in result}
        assert other_path not in hit_files
    finally:
        os.unlink(target_path)
        os.unlink(other_path)


def test_line_tracer_trace_lines_records_target_lines() -> None:
    """``_trace_lines`` adds ``(filename, lineno)`` to ``lines_hit``."""
    tracer = _LineTracer(target_files={"/target/file.py"})

    class FakeCode:
        co_filename = "/target/file.py"

    class FakeFrame:
        f_code = FakeCode()
        f_lineno = 42

    result = tracer._trace_lines(FakeFrame(), "line", None)
    assert ("/target/file.py", 42) in tracer.lines_hit
    assert result is not None
    assert result.__func__ is _LineTracer._trace_lines


def test_line_tracer_trace_lines_ignores_non_target_files() -> None:
    """``_trace_lines`` ignores lines from non-target files."""
    tracer = _LineTracer(target_files={"/target/file.py"})

    class FakeCode:
        co_filename = "/other/file.py"

    class FakeFrame:
        f_code = FakeCode()
        f_lineno = 10

    tracer._trace_lines(FakeFrame(), "line", None)
    assert len(tracer.lines_hit) == 0


def test_line_tracer_trace_lines_always_returns_itself() -> None:
    """``_trace_lines`` returns itself for continued tracing."""
    tracer = _LineTracer(target_files={"/target/file.py"})

    class FakeCode:
        co_filename = "/target/file.py"

    class FakeFrame:
        f_code = FakeCode()
        f_lineno = 1

    result = tracer._trace_lines(FakeFrame(), "return", None)
    assert result is not None
    assert result.__func__ is _LineTracer._trace_lines


# ---------------------------------------------------------------------------
# CoverageMap
# ---------------------------------------------------------------------------


def test_coverage_map_returns_empty_set_for_uncovered_line() -> None:
    """Uncovered line -> empty set."""
    cov = CoverageMap()
    assert cov.tests_for("foo.py", 10) == set()


def test_coverage_map_returns_tests_for_covered_line() -> None:
    """Covered line -> set of test ids."""
    cov = CoverageMap()
    cov.add("foo.py", 5, "test_a")
    assert cov.tests_for("foo.py", 5) == {"test_a"}


def test_coverage_map_tracks_multiple_tests_per_line() -> None:
    """Multiple tests for the same line are all returned."""
    cov = CoverageMap()
    cov.add("foo.py", 5, "test_a")
    cov.add("foo.py", 5, "test_b")
    cov.add("foo.py", 5, "test_c")
    assert cov.tests_for("foo.py", 5) == {"test_a", "test_b", "test_c"}


def test_coverage_map_distinguishes_files() -> None:
    """Different files -> different test sets."""
    cov = CoverageMap()
    cov.add("foo.py", 5, "test_a")
    cov.add("bar.py", 5, "test_b")
    assert cov.tests_for("foo.py", 5) == {"test_a"}
    assert cov.tests_for("bar.py", 5) == {"test_b"}


def test_coverage_map_distinguishes_lines() -> None:
    """Different lines -> different test sets."""
    cov = CoverageMap()
    cov.add("foo.py", 5, "test_a")
    cov.add("foo.py", 10, "test_b")
    assert cov.tests_for("foo.py", 5) == {"test_a"}
    assert cov.tests_for("foo.py", 10) == {"test_b"}


# ---------------------------------------------------------------------------
# CoveragePlugin
# ---------------------------------------------------------------------------


def test_coverage_plugin_records_test_times_as_positive_durations() -> None:
    """``pytest_runtest_teardown`` records elapsed duration.

    Kills ``- -> +`` and ``- -> *`` mutations: a correct
    subtraction yields a small positive duration; the mutated
    operators yield huge nonsensical values.
    """
    plugin = CoveragePlugin(set())
    fake_item = MagicMock()
    fake_item.nodeid = "tests/test_example.py::test_one"

    with patch(
        "pytest_leela.coverage_tracker.time.monotonic",
        side_effect=[1000.0, 1000.05],
    ):
        plugin.pytest_runtest_setup(fake_item)
        plugin.pytest_runtest_teardown(fake_item, nextitem=None)

    recorded = plugin.test_times["tests/test_example.py::test_one"]
    assert 0.0 < recorded < 1.0


def test_coverage_plugin_records_exact_elapsed_difference() -> None:
    """Elapsed time is the exact difference of two monotonic calls."""
    plugin = CoveragePlugin(set())
    fake_item = MagicMock()
    fake_item.nodeid = "test::exact"

    with patch(
        "pytest_leela.coverage_tracker.time.monotonic",
        side_effect=[500.0, 503.25],
    ):
        plugin.pytest_runtest_setup(fake_item)
        plugin.pytest_runtest_teardown(fake_item, nextitem=None)

    assert plugin.test_times["test::exact"] == pytest.approx(3.25)


# ---------------------------------------------------------------------------
# collect_coverage
# ---------------------------------------------------------------------------


def test_collect_coverage_returns_coverage_map(tmp_path: Path) -> None:
    """``collect_coverage`` returns a ``CoverageMap``, not ``None``.

    Kills ``return expr -> return None`` on the final return.
    """
    target_dir = tmp_path / "src"
    test_dir = tmp_path / "tests"
    target_dir.mkdir()
    test_dir.mkdir()

    target_file = target_dir / "target.py"
    target_file.write_text("def add(a, b):\n    return a + b\n")

    test_file = test_dir / "test_target.py"
    test_file.write_text(
        f"import sys\n"
        f"sys.path.insert(0, {str(target_dir)!r})\n"
        "from target import add\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n"
    )

    result = collect_coverage([str(target_file)], str(test_dir))
    assert isinstance(result, CoverageMap)
    assert result is not None
