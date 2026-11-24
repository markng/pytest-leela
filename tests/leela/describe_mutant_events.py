"""Synthetic CLI and I/O checks for the optional completion stream."""

import json
import math
import os
import subprocess
import sys
import time
from unittest.mock import MagicMock

import pytest

from pytest_leela import mutant_events
from pytest_leela.html_report import generate_html_report
from pytest_leela.models import Mutant, MutantTiming, MutationPoint, RunResult
from pytest_leela.plugin import LeelaPlugin, pytest_configure


def _read_events(path):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    assert all(line.endswith("\n") for line in lines)
    return [json.loads(line) for line in lines]


def _empty_run_result():
    return RunResult(
        target_files=[],
        total_mutants=0,
        mutants_tested=0,
        mutants_pruned=0,
        results=[],
        wall_time_seconds=0.0,
    )


def _wait_until_blocked(process, marker, log):
    deadline = time.monotonic() + 30
    while not marker.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            pytest.fail(f"Second mutant never reached the barrier:\n{log.read_text()}")
        time.sleep(0.01)


def describe_jsonl_cli():
    def it_flushes_the_first_result_while_the_second_mutant_is_blocked(pytester):
        pytester.makepyprojecttoml('[tool.pytest-leela]\noperators = ["assignment"]')
        source = pytester.makepyfile(
            subject="""
            def first():
                value = 1
                return value

            def second():
                value = 2
                return value
        """
        )
        pytester.makepyfile(
            test_subject="""
            from pathlib import Path
            import time
            import subject

            def test_first():
                assert subject.first() == 1

            def test_second():
                value = subject.second()
                if value is None:
                    Path("second-blocked").touch()
                    deadline = time.monotonic() + 30
                    while not Path("release-second").exists():
                        if time.monotonic() >= deadline:
                            Path("fixture-timeout").touch()
                            raise RuntimeError("test barrier timed out")
                        time.sleep(0.01)
                    Path("second-released").touch()
                try:
                    assert value == 2
                except AssertionError:
                    Path("second-behavior-failed").touch()
                    raise
        """
        )
        path = pytester.path / "events.jsonl"
        log = pytester.path / "pytest.log"
        release = pytester.path / "release-second"
        released = pytester.path / "second-released"
        failed_behavior = pytester.path / "second-behavior-failed"
        fixture_timeout = pytester.path / "fixture-timeout"
        html = pytester.path / "report.html"
        with log.open("wb") as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "--leela-jsonl",
                    str(path),
                    "--leela-html",
                    str(html),
                ],
                cwd=pytester.path,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_until_blocked(process, pytester.path / "second-blocked", log)
                assert process.poll() is None
                early = _read_events(path)
                assert [(event["mutant_id"], event["lineno"]) for event in early] == [
                    (0, 2)
                ]
                assert not any(
                    marker.exists()
                    for marker in (released, failed_behavior, fixture_timeout)
                )
                release.touch()
                process.wait(timeout=30)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        assert process.returncode == 0, log.read_text()
        assert not fixture_timeout.exists()
        assert released.exists() and failed_behavior.exists()
        # A runner timeout can also count as killed. Require the actual test
        # node IDs (not <timeout>) to be credited for the two kills.
        report_json = html.read_text(encoding="utf-8").split("window.LEELA_DATA = ", 1)[
            1
        ]
        report, _ = json.JSONDecoder().raw_decode(report_json)
        (file_data,) = report["files"].values()
        assert [mutant["killing_test"]["id"] for mutant in file_data["mutants"]] == [
            "test_subject.py::test_first",
            "test_subject.py::test_second",
        ]
        events = _read_events(path)
        assert events[:1] == early
        assert [(event["mutant_id"], event["lineno"]) for event in events] == [
            (0, 2),
            (1, 6),
        ]
        for event, mutant in zip(events, file_data["mutants"], strict=True):
            seconds = event.pop("wall_time_seconds")
            assert math.isfinite(seconds) and seconds > 0
            assert mutant["wall_time_seconds"] == seconds
            assert 0 <= mutant["time_seconds"] <= seconds
            assert event == {
                "schema_version": 1,
                "mutant_id": event["mutant_id"],
                "file_path": str(source),
                "module_name": "subject",
                "lineno": event["lineno"],
                "col_offset": 12,
                "original_op": "assignment",
                "replacement_op": "rhs_to_none",
                "verdict": "killed",
            }
        assert "Overall: 2/2 killed" in log.read_text()

    @pytest.mark.parametrize("target", ["subject.py", "missing.py"])
    def it_creates_an_empty_file_for_zero_work_without_changing_exit(pytester, target):
        pytester.makepyfile(subject="pass", test_subject="def test_ok(): pass")
        path = pytester.path / "events.jsonl"
        regular = pytester.runpytest_subprocess("-q", "--leela", "--target", target)
        assert regular.ret == 0
        assert not path.exists()
        streamed = pytester.runpytest_subprocess(
            "-q", "--leela-jsonl", str(path), "--target", target
        )
        assert streamed.ret == regular.ret
        assert path.read_bytes() == b""

    def it_never_overwrites_existing_files_and_reports_the_abort(pytester):
        pytester.makepyfile(subject="pass", test_subject="def test_ok(): pass")
        path = pytester.path / "events.jsonl"
        path.write_bytes(b"keep existing\n")
        result = pytester.runpytest_subprocess("-q", "--leela-jsonl", str(path))
        assert result.ret == pytest.ExitCode.TESTS_FAILED
        assert "aborted: could not open mutant events file" in result.stdout.str()
        assert str(path) in result.stdout.str()
        assert "Overall:" not in result.stdout.str()
        assert path.read_bytes() == b"keep existing\n"

    def it_fails_the_session_when_a_flush_fails_even_with_fail_on_error_disabled(
        pytester,
    ):
        pytester.makepyprojecttoml("""[tool.pytest-leela]
            operators = ["assignment"]
            fail_on_error = false
        """)
        pytester.makeconftest("""
            from unittest.mock import MagicMock
            from pytest_leela import mutant_events

            def pytest_configure():
                stream = MagicMock(name="sink")
                stream.name = "events.jsonl"
                stream.flush.side_effect = OSError("synthetic flush failure")
                mutant_events.open = lambda *args, **kwargs: stream
        """)
        pytester.makepyfile(
            subject="value = 1",
            test_subject="""
            import subject
            def test_value():
                assert subject.value == 1
        """,
        )
        result = pytester.runpytest_subprocess("-q", "--leela-jsonl", "events.jsonl")
        assert result.ret == pytest.ExitCode.TESTS_FAILED
        assert (
            "aborted: could not write mutant events file 'events.jsonl'"
            in result.stdout.str()
        )
        assert "synthetic flush failure" in result.stdout.str()
        assert "Overall:" not in result.stdout.str()


def describe_LeelaPlugin_event_sink():
    def it_registers_only_when_the_new_option_is_set():
        for path in (None, "events.jsonl"):
            config = MagicMock()
            config.getoption.side_effect = lambda name, default=None: {
                "leela_jsonl": path
            }.get(name, default)
            pytest_configure(config)
            if path is None:
                config.pluginmanager.register.assert_not_called()
            else:
                config.pluginmanager.register.assert_called_once()
                assert config.pluginmanager.register.call_args.args[1] == "leela-plugin"

    @pytest.mark.parametrize("has_target", [False, True])
    def it_streams_only_from_the_engine_timing_callback(
        tmp_path, monkeypatch, has_target
    ):
        path = tmp_path / "events.jsonl"
        point = MutationPoint(
            str(tmp_path / "subject.py"), "subject", 2, 8, "BinOp", "Add", None
        )
        config = MagicMock(rootpath=tmp_path)
        config.getoption.side_effect = lambda name, default=None: {
            "leela_jsonl": str(path)
        }.get(name, default)
        session = MagicMock(config=config, exitstatus=0)
        plugin = LeelaPlugin(config)
        monkeypatch.setattr(
            plugin,
            "_resolve_target_files",
            lambda _: [point.file_path] if has_target else [],
        )
        engine = MagicMock()
        monkeypatch.setattr("pytest_leela.plugin.Engine", engine)

        def run(*args, on_mutant_timing, **kwargs):
            on_mutant_timing(MutantTiming(Mutant(point, "Sub", 8), "killed", 0.1))
            assert [event["mutant_id"] for event in _read_events(path)] == [8]
            return MagicMock(survived=[], errors=[])

        engine.return_value.run.side_effect = run
        monkeypatch.setattr(
            "pytest_leela.plugin.format_terminal_report", lambda _: "report"
        )
        plugin.pytest_sessionfinish(session, exitstatus=0)
        assert session.exitstatus == 0
        assert [event["mutant_id"] for event in _read_events(path)] == (
            [8] if has_target else []
        )
        assert engine.return_value.run.call_count == int(has_target)

    @pytest.mark.parametrize("html_mode", ["same", "late-link", "new", "existing"])
    def it_keeps_jsonl_separate_from_the_html_report(tmp_path, monkeypatch, html_mode):
        path = tmp_path / "events.jsonl"
        html_path = path if html_mode == "same" else tmp_path / "report.html"
        old_content = "previous report" * 10_000
        if html_mode == "existing":
            html_path.write_text(old_content, encoding="utf-8")
        config = MagicMock(rootpath=tmp_path)
        config.getoption.side_effect = lambda name, default=None: {
            "leela_jsonl": str(path),
            "leela_html": str(html_path),
        }.get(name, default)
        session = MagicMock(config=config, exitstatus=0)
        plugin = LeelaPlugin(config)
        monkeypatch.setattr(
            plugin, "_resolve_target_files", lambda _: [str(tmp_path / "subject.py")]
        )
        point = MutationPoint(
            str(tmp_path / "subject.py"), "subject", 2, 8, "BinOp", "Add", None
        )

        def run(*args, on_mutant_timing, **kwargs):
            on_mutant_timing(MutantTiming(Mutant(point, "Sub", 8), "killed", 0.1))
            if html_mode == "late-link":
                # The destinations were distinct at launch; alias after a
                # flushed record to test the actual HTML write, not a precheck.
                html_path.hardlink_to(path)
            return _empty_run_result()

        engine = MagicMock()
        engine.return_value.run.side_effect = run
        monkeypatch.setattr("pytest_leela.plugin.Engine", engine)
        plugin.pytest_sessionfinish(session, exitstatus=0)

        calls = config.get_terminal_writer.return_value.write.call_args_list
        if html_mode in ("same", "late-link"):
            assert session.exitstatus == 1
            assert (
                "aborted: JSONL and HTML outputs must use different files"
                in calls[-1].args[0]
            )
            assert not any("Overall:" in call.args[0] for call in calls)
            assert html_path.read_bytes() == path.read_bytes()
        else:
            assert session.exitstatus == 0
            html = html_path.read_text(encoding="utf-8")
            assert html.rstrip().endswith("</html>")
            assert "previous report" not in html
        engine.return_value.run.assert_called_once()
        assert [event["mutant_id"] for event in _read_events(path)] == [8]


def describe_guarded_html_writer():
    @pytest.mark.skipif(sys.platform == "win32", reason="renames an open file on POSIX")
    def it_uses_the_checked_descriptor_without_reopening_the_path(
        tmp_path, monkeypatch
    ):
        path = tmp_path / "events.jsonl"
        html_path = tmp_path / "report.html"
        original_html = tmp_path / "report-before-retarget.html"
        real_truncate = os.ftruncate

        def retarget_after_check(fd, length):
            html_path.rename(original_html)
            html_path.hardlink_to(path)
            real_truncate(fd, length)

        with path.open("x", encoding="utf-8") as events:
            events.write("keep existing JSONL\n")
            events.flush()
            monkeypatch.setattr(os, "ftruncate", retarget_after_check)
            generate_html_report(
                _empty_run_result(), str(html_path), jsonl_fd=events.fileno()
            )
        assert path.read_text(encoding="utf-8") == "keep existing JSONL\n"
        assert original_html.read_text(encoding="utf-8").rstrip().endswith("</html>")

    def it_allows_a_distinct_nonregular_destination(tmp_path):
        path = tmp_path / "events.jsonl"
        with path.open("x", encoding="utf-8") as events:
            generate_html_report(
                _empty_run_result(), os.devnull, jsonl_fd=events.fileno()
            )


def describe_mutant_events():
    def it_closes_the_stream_when_the_run_fails(monkeypatch):
        stream = MagicMock()
        monkeypatch.setattr(
            mutant_events, "open", lambda *args, **kwargs: stream, raising=False
        )
        with (
            pytest.raises(OSError, match="runner failed"),
            mutant_events.open_mutant_events("events"),
        ):
            raise OSError("runner failed")
        stream.close.assert_called_once_with()

    def it_surfaces_a_close_error(monkeypatch):
        stream = MagicMock()
        stream.close.side_effect = OSError("close failed")
        monkeypatch.setattr(
            mutant_events, "open", lambda *args, **kwargs: stream, raising=False
        )
        with pytest.raises(
            mutant_events.MutantEventsError, match="close.*'events'.*close failed"
        ):
            with mutant_events.open_mutant_events("events") as opened:
                assert opened is stream

    @pytest.mark.parametrize("verdict", ["survived", "error"])
    def it_preserves_verdicts_and_does_not_round_wall_time(tmp_path, verdict):
        path = tmp_path / "events"
        point = MutationPoint(
            '/source/file\nwith "quotes".py', "module", 2, 8, "BinOp", "Add", None
        )
        timing = MutantTiming(Mutant(point, "Sub", 42), verdict, 0.000123)
        with mutant_events.open_mutant_events(str(path)) as stream:
            mutant_events.write_mutant_event(stream, timing)
            (event,) = _read_events(path)
        assert event["file_path"] == point.file_path
        assert event["verdict"] == verdict
        assert event["wall_time_seconds"] == timing.wall_time_seconds
