"""Tests for pytest_leela.coverage_tracker and CoverageMap."""

import os
import sys
import tempfile
import threading

from pytest_leela.coverage_tracker import _LineTracer, collect_coverage
from pytest_leela.models import CoverageMap


def describe_LineTracer():
    def it_starts_tracing_and_records_lines_hit():
        """start() activates tracing and records line executions for target files."""
        # Create a temp file path that we'll use as the "target"
        # We need a real function whose co_filename matches our target
        import tempfile
        import os

        source = "def traced_func():\n    x = 1\n    y = 2\n    return x + y\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            f.flush()
            tmp_path = f.name

        try:
            code = compile(source, tmp_path, "exec")
            namespace = {}
            exec(code, namespace)

            tracer = _LineTracer(target_files={tmp_path})
            tracer.start()
            try:
                namespace["traced_func"]()
            finally:
                result = tracer.stop()

            # We should have hits for lines in the traced file
            hit_files = {f for f, _ in result}
            assert tmp_path in hit_files
            # Lines 2, 3, 4 are the body of traced_func
            hit_lines = {line for f, line in result if f == tmp_path}
            assert 2 in hit_lines
            assert 3 in hit_lines
            assert 4 in hit_lines
        finally:
            os.unlink(tmp_path)

    def it_stops_tracing_and_clears_on_restart():
        """stop() deactivates tracing; start() clears previous hits."""
        tracer = _LineTracer(target_files={"/nonexistent/file.py"})

        # Manually add some lines_hit to simulate prior tracing
        tracer.lines_hit.add(("/nonexistent/file.py", 10))
        assert len(tracer.lines_hit) == 1

        # start() should clear lines_hit and set _active
        tracer.start()
        try:
            assert tracer._active is True
            assert len(tracer.lines_hit) == 0
        finally:
            result = tracer.stop()

        # After stop, _active should be False
        assert tracer._active is False
        # Result should be a copy (empty since no real lines were hit)
        assert isinstance(result, set)


def describe_LineTracer_trace():
    def it_returns_none_when_not_active():
        """_trace returns None when _active is False."""
        tracer = _LineTracer(target_files={"/some/file.py"})
        tracer._active = False
        result = tracer._trace(None, "call", None)
        assert result is None

    def it_does_not_collect_data_when_inactive():
        """An inactive tracer must not trace into any scope (line 39 guard).

        If _trace returned a non-None value when inactive, Python's trace
        mechanism would trace into function scopes and _trace_lines would
        record line hits for target files. This verifies no data leaks.
        """
        import os
        import tempfile

        source = "def func():\n    x = 1\n    return x\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            f.flush()
            tmp = f.name

        try:
            code = compile(source, tmp, "exec")
            ns = {}
            exec(code, ns)

            tracer = _LineTracer(target_files={tmp})
            # Do NOT call start() — _active stays False
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

    def it_returns_none_for_non_call_events():
        """_trace returns None for events that aren't 'call'."""
        tracer = _LineTracer(target_files={"/some/file.py"})
        tracer._active = True
        result = tracer._trace(None, "line", None)
        assert result is None
        result = tracer._trace(None, "return", None)
        assert result is None

    def it_does_not_trace_non_call_events_behaviorally():
        """Non-call events must return None so Python doesn't sub-trace (line 45).

        Even for an active tracer, events like 'line' and 'return' at the top
        level must return None — returning a trace function would cause Python
        to invoke it as a local tracer for subsequent events in that scope.
        """
        tracer = _LineTracer(target_files={"/target/file.py"})
        tracer._active = True

        class FakeCode:
            co_filename = "/target/file.py"

        class FakeFrame:
            f_code = FakeCode()
            f_lineno = 5

        # For non-"call" events, _trace must return None (not _trace_lines)
        for event in ("line", "return", "exception"):
            result = tracer._trace(FakeFrame(), event, None)
            assert result is None, f"_trace returned {result!r} for event {event!r}"

    def it_returns_trace_lines_for_target_file_call():
        """_trace returns _trace_lines when the call is from a target file."""
        tracer = _LineTracer(target_files={"/target/file.py"})
        tracer._active = True

        class FakeCode:
            co_filename = "/target/file.py"

        class FakeFrame:
            f_code = FakeCode()
            f_lineno = 1

        result = tracer._trace(FakeFrame(), "call", None)
        # Bound methods are recreated each access, so compare __func__
        assert result is not None
        assert result.__func__ is _LineTracer._trace_lines

    def it_returns_none_for_non_target_file_call():
        """_trace returns None when the call is NOT from a target file."""
        tracer = _LineTracer(target_files={"/target/file.py"})
        tracer._active = True

        class FakeCode:
            co_filename = "/other/file.py"

        class FakeFrame:
            f_code = FakeCode()

        result = tracer._trace(FakeFrame(), "call", None)
        assert result is None

    def it_excludes_non_target_files_from_tracing():
        """Non-target file calls must return None so they aren't sub-traced (line 44).

        If _trace returned a trace function for non-target files, Python would
        install it as the local tracer. Even though _trace_lines also guards on
        target_files, returning non-None is semantically wrong and wastes CPU.
        Verify with a real settrace to confirm no sub-tracing occurs.
        """
        import os
        import tempfile

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
            # Compile both — only target_path is in target_files
            target_code = compile(target_source, target_path, "exec")
            other_code = compile(other_source, other_path, "exec")
            target_ns = {}
            other_ns = {}
            exec(target_code, target_ns)
            exec(other_code, other_ns)

            tracer = _LineTracer(target_files={target_path})
            tracer.start()
            try:
                # Call both functions — only target should be traced
                other_ns["other_func"]()
                target_ns["target_func"]()
            finally:
                result = tracer.stop()

            # Only target_path lines should appear
            hit_files = {f for f, _ in result}
            assert other_path not in hit_files
        finally:
            os.unlink(target_path)
            os.unlink(other_path)


def describe_LineTracer_trace_lines():
    def it_records_line_hits_for_target_files():
        """_trace_lines adds (filename, lineno) to lines_hit for targets."""
        tracer = _LineTracer(target_files={"/target/file.py"})

        class FakeCode:
            co_filename = "/target/file.py"

        class FakeFrame:
            f_code = FakeCode()
            f_lineno = 42

        result = tracer._trace_lines(FakeFrame(), "line", None)
        assert ("/target/file.py", 42) in tracer.lines_hit
        # Returns itself for continued tracing
        assert result is not None
        assert result.__func__ is _LineTracer._trace_lines

    def it_ignores_non_target_files():
        """_trace_lines does not record lines for non-target files."""
        tracer = _LineTracer(target_files={"/target/file.py"})

        class FakeCode:
            co_filename = "/other/file.py"

        class FakeFrame:
            f_code = FakeCode()
            f_lineno = 10

        tracer._trace_lines(FakeFrame(), "line", None)
        assert len(tracer.lines_hit) == 0

    def it_always_returns_itself():
        """_trace_lines returns self._trace_lines regardless of event."""
        tracer = _LineTracer(target_files={"/target/file.py"})

        class FakeCode:
            co_filename = "/target/file.py"

        class FakeFrame:
            f_code = FakeCode()
            f_lineno = 1

        result = tracer._trace_lines(FakeFrame(), "return", None)
        assert result is not None
        assert result.__func__ is _LineTracer._trace_lines


def describe_CoverageMap():
    def it_returns_empty_set_for_uncovered_line():
        cov = CoverageMap()
        result = cov.tests_for("foo.py", 10)
        assert result == set()

    def it_returns_tests_for_covered_line():
        cov = CoverageMap()
        cov.add("foo.py", 5, "test_a")
        result = cov.tests_for("foo.py", 5)
        assert result == {"test_a"}

    def it_tracks_multiple_tests_per_line():
        cov = CoverageMap()
        cov.add("foo.py", 5, "test_a")
        cov.add("foo.py", 5, "test_b")
        cov.add("foo.py", 5, "test_c")
        result = cov.tests_for("foo.py", 5)
        assert result == {"test_a", "test_b", "test_c"}

    def it_distinguishes_different_files():
        cov = CoverageMap()
        cov.add("foo.py", 5, "test_a")
        cov.add("bar.py", 5, "test_b")
        assert cov.tests_for("foo.py", 5) == {"test_a"}
        assert cov.tests_for("bar.py", 5) == {"test_b"}

    def it_distinguishes_different_lines():
        cov = CoverageMap()
        cov.add("foo.py", 5, "test_a")
        cov.add("foo.py", 10, "test_b")
        assert cov.tests_for("foo.py", 5) == {"test_a"}
        assert cov.tests_for("foo.py", 10) == {"test_b"}


def describe_collect_coverage():
    def it_returns_a_coverage_map():
        """collect_coverage returns a CoverageMap, not None.

        Kills: line 95 return expr → return None.
        """
        # Create a minimal target file and test file in a temp dir
        with tempfile.TemporaryDirectory() as tmpdir:
            target_dir = os.path.join(tmpdir, "src")
            test_dir = os.path.join(tmpdir, "tests")
            os.makedirs(target_dir)
            os.makedirs(test_dir)

            target_file = os.path.join(target_dir, "target.py")
            with open(target_file, "w") as f:
                f.write("def add(a, b):\n    return a + b\n")

            test_file = os.path.join(test_dir, "test_target.py")
            with open(test_file, "w") as f:
                f.write(
                    "import sys\n"
                    f"sys.path.insert(0, {target_dir!r})\n"
                    "from target import add\n"
                    "def test_add():\n"
                    "    assert add(1, 2) == 3\n"
                )

            result = collect_coverage([target_file], test_dir)
            assert isinstance(result, CoverageMap)
            assert result is not None
