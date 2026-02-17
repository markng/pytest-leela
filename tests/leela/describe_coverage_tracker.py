"""Tests for pytest_leela.coverage_tracker and CoverageMap."""

import sys
import threading

from pytest_leela.coverage_tracker import _LineTracer
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

    def it_returns_none_for_non_call_events():
        """_trace returns None for events that aren't 'call'."""
        tracer = _LineTracer(target_files={"/some/file.py"})
        tracer._active = True
        result = tracer._trace(None, "line", None)
        assert result is None
        result = tracer._trace(None, "return", None)
        assert result is None

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
