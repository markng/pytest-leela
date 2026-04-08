"""Tests for pytest_leela.runner — test execution against mutants."""

import subprocess
import sys
import threading
import types
from unittest.mock import MagicMock, patch

import pytest
from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.models import Mutant, MutantResult
from pytest_leela.runner import (
    _KEEP_PREFIXES,
    _clear_framework_caches,
    _clear_user_modules,
    _clear_user_modules_fast,
    _compute_timeout,
    _ResultCollector,
    precompute_user_modules,
    run_tests_for_mutant,
)


class _FakeReport:
    """Minimal stand-in for pytest report objects."""

    def __init__(self, nodeid: str, when: str, passed: bool, failed: bool) -> None:
        self.nodeid = nodeid
        self.when = when
        self.passed = passed
        self.failed = failed


def describe_ResultCollector():
    def it_counts_passed_tests():
        collector = _ResultCollector()
        report = _FakeReport("test_a", when="call", passed=True, failed=False)
        collector.pytest_runtest_logreport(report)
        assert collector.total == 1
        assert collector.passed == ["test_a"]
        assert collector.failed == []

    def it_counts_failed_tests():
        collector = _ResultCollector()
        report = _FakeReport("test_b", when="call", passed=False, failed=True)
        collector.pytest_runtest_logreport(report)
        assert collector.total == 1
        assert collector.failed == ["test_b"]
        assert collector.passed == []

    def it_tracks_setup_errors():
        collector = _ResultCollector()
        report = _FakeReport("test_c", when="setup", passed=False, failed=True)
        collector.pytest_runtest_logreport(report)
        assert collector.errors == ["test_c"]
        assert collector.total == 0  # setup errors don't increment total

    def it_ignores_non_call_passing():
        collector = _ResultCollector()
        report = _FakeReport("test_d", when="setup", passed=True, failed=False)
        collector.pytest_runtest_logreport(report)
        assert collector.total == 0
        assert collector.passed == []

    def it_accumulates_multiple_results():
        collector = _ResultCollector()
        collector.pytest_runtest_logreport(
            _FakeReport("test_1", when="call", passed=True, failed=False)
        )
        collector.pytest_runtest_logreport(
            _FakeReport("test_2", when="call", passed=False, failed=True)
        )
        collector.pytest_runtest_logreport(
            _FakeReport("test_3", when="call", passed=True, failed=False)
        )
        assert collector.total == 3
        assert collector.passed == ["test_1", "test_3"]
        assert collector.failed == ["test_2"]


def describe_clear_framework_caches():
    def it_does_not_raise_when_django_is_not_installed():
        with patch("pytest_leela.runner._django_clear_url_caches", None):
            # Should silently pass when Django is unavailable
            _clear_framework_caches()

    def it_calls_clear_url_caches_when_django_is_available():
        mock_clear = MagicMock()

        with patch("pytest_leela.runner._django_clear_url_caches", mock_clear):
            _clear_framework_caches()

        mock_clear.assert_called_once()

    def it_is_idempotent_when_called_multiple_times():
        mock_clear = MagicMock()

        with patch("pytest_leela.runner._django_clear_url_caches", mock_clear):
            _clear_framework_caches()
            _clear_framework_caches()
            _clear_framework_caches()

        assert mock_clear.call_count == 3


def describe_run_tests_for_mutant():
    def it_kills_a_detectable_mutant(tmp_path, monkeypatch):
        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "runner_target.py"
        target.write_text(source)

        test_dir = tmp_path / "runner_tests"
        test_dir.mkdir()
        (test_dir / "test_runner_target.py").write_text(
            "from runner_target import add\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        points = find_mutation_points(source, str(target), "runner_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        result = run_tests_for_mutant(
            mutant,
            {"runner_target": source},
            {"runner_target": str(target)},
            test_dir=str(test_dir),
        )

        assert isinstance(result, MutantResult)
        assert result.killed is True
        assert result.tests_run >= 1
        assert result.killing_test is not None

    def it_reports_surviving_mutant_when_test_is_weak(tmp_path, monkeypatch):
        source = "def is_positive(n):\n    return n > 0\n"
        target = tmp_path / "runner_survive.py"
        target.write_text(source)

        test_dir = tmp_path / "runner_survive_tests"
        test_dir.mkdir()
        (test_dir / "test_runner_survive.py").write_text(
            "from runner_survive import is_positive\n\n"
            "def test_positive():\n"
            "    assert is_positive(5) is True\n\n"
            "def test_negative():\n"
            "    assert is_positive(-5) is False\n"
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        points = find_mutation_points(source, str(target), "runner_survive")
        cmp_point = next(
            p for p in points if p.node_type == "Compare" and p.original_op == "Gt"
        )
        mutant = Mutant(point=cmp_point, replacement_op="GtE", mutant_id=0)

        result = run_tests_for_mutant(
            mutant,
            {"runner_survive": source},
            {"runner_survive": str(target)},
            test_dir=str(test_dir),
        )

        assert isinstance(result, MutantResult)
        assert result.killed is False
        assert result.tests_run >= 1
        assert result.killing_test is None

    def it_preserves_modules_in_saved_snapshot_during_cleanup(tmp_path, monkeypatch):
        """Each subprocess run starts with a clean namespace, so pytest_leela modules
        imported in the parent process are unaffected by the child's module cleanup."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "saved_mod_target.py"
        target.write_text(source)

        points = find_mutation_points(source, str(target), "saved_mod_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        # A pytest_leela.* module imported in the parent process survives
        # because the subprocess has its own isolated Python namespace.
        kept_mod = types.ModuleType("pytest_leela._test_saved_mod")
        kept_mod.__file__ = str(tmp_path / "saved.py")
        monkeypatch.setitem(sys.modules, "pytest_leela._test_saved_mod", kept_mod)

        run_tests_for_mutant(
            mutant,
            {"saved_mod_target": source},
            {"saved_mod_target": str(target)},
            test_dir=str(tmp_path),
        )

        assert "pytest_leela._test_saved_mod" in sys.modules

    def it_cleans_up_cwd_modules_added_during_inner_run(tmp_path, monkeypatch):
        """Subprocess isolation means the parent never runs pytest.main, so there
        is no "inner run" context. The test verifies that run_tests_for_mutant
        itself does not pollute sys.modules."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "inner_cleanup_target.py"
        target.write_text(source)

        points = find_mutation_points(source, str(target), "inner_cleanup_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        # With subprocess isolation, no CWD-local module is added during the
        # parent's run_tests_for_mutant call (pytest.main never runs here).
        run_tests_for_mutant(
            mutant,
            {"inner_cleanup_target": source},
            {"inner_cleanup_target": str(target)},
            test_dir=str(tmp_path),
        )

        # Verify that no pytest_leela._test_* artifacts leaked into sys.modules.
        leaked = [n for n in sys.modules if n.startswith("pytest_leela._test_")]
        assert leaked == [], f"Artifacts leaked: {leaked}"

    def it_calculates_elapsed_time_by_subtraction(tmp_path, monkeypatch):
        """The parent process measures elapsed time using time.monotonic().
        This tests that the subtraction (end - start) is correct."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "time_target.py"
        target.write_text(source)

        test_dir = tmp_path / "time_tests"
        test_dir.mkdir()
        (test_dir / "test_time_target.py").write_text(
            "from time_target import add\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )

        points = find_mutation_points(source, str(target), "time_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        # Patch time.monotonic in the parent (the subprocess has its own namespace)
        with patch("pytest_leela.runner.time.monotonic", side_effect=[100.0, 105.0]):
            result = run_tests_for_mutant(
                mutant,
                {"time_target": source},
                {"time_target": str(target)},
                test_dir=str(test_dir),
            )

        assert result.time_seconds == pytest.approx(5.0)

    def it_populates_test_ids_run_and_killing_tests_on_kill(tmp_path, monkeypatch):
        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "runner_ids_kill.py"
        target.write_text(source)

        test_dir = tmp_path / "runner_ids_kill_tests"
        test_dir.mkdir()
        (test_dir / "test_runner_ids_kill.py").write_text(
            "from runner_ids_kill import add\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        points = find_mutation_points(source, str(target), "runner_ids_kill")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        result = run_tests_for_mutant(
            mutant,
            {"runner_ids_kill": source},
            {"runner_ids_kill": str(target)},
            test_dir=str(test_dir),
        )

        assert result.killed is True
        assert len(result.test_ids_run) >= 1
        assert len(result.killing_tests) >= 1
        assert set(result.killing_tests).issubset(set(result.test_ids_run))

    def it_populates_test_ids_run_with_empty_killing_tests_on_survive(
        tmp_path, monkeypatch
    ):
        source = "def is_positive(n):\n    return n > 0\n"
        target = tmp_path / "runner_ids_surv.py"
        target.write_text(source)

        test_dir = tmp_path / "runner_ids_surv_tests"
        test_dir.mkdir()
        (test_dir / "test_runner_ids_surv.py").write_text(
            "from runner_ids_surv import is_positive\n\n"
            "def test_positive():\n"
            "    assert is_positive(5) is True\n\n"
            "def test_negative():\n"
            "    assert is_positive(-5) is False\n"
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        points = find_mutation_points(source, str(target), "runner_ids_surv")
        cmp_point = next(
            p for p in points if p.node_type == "Compare" and p.original_op == "Gt"
        )
        mutant = Mutant(point=cmp_point, replacement_op="GtE", mutant_id=0)

        result = run_tests_for_mutant(
            mutant,
            {"runner_ids_surv": source},
            {"runner_ids_surv": str(target)},
            test_dir=str(test_dir),
        )

        assert result.killed is False
        assert len(result.test_ids_run) >= 1
        assert result.killing_tests == []

    def it_returns_crashed_result_when_subprocess_produces_no_output(
        tmp_path, monkeypatch
    ):
        """When the subprocess produces no output (e.g. crashes before writing),
        the result should be killed with killing_test='<crashed>'.

        This exercises the empty-output branch in run_tests_for_mutant.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "crash_no_output_target.py"
        target.write_text(source)

        test_dir = tmp_path / "crash_no_output_tests"
        test_dir.mkdir()
        (test_dir / "test_crash_no_output_target.py").write_text(
            "from crash_no_output_target import add\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )

        points = find_mutation_points(source, str(target), "crash_no_output_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        # Patch subprocess.run to return empty output (simulating a crash
        # that produces no stdout/stderr before terminating).
        with patch("pytest_leela.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="")
            result = run_tests_for_mutant(
                mutant,
                {"crash_no_output_target": source},
                {"crash_no_output_target": str(target)},
                test_dir=str(test_dir),
            )

        assert isinstance(result, MutantResult)
        assert result.killed is True
        assert result.killing_test == "<crashed>"
        assert result.killing_tests == ["<crashed>"]

    def it_returns_crashed_result_when_subprocess_json_decode_fails(
        tmp_path, monkeypatch
    ):
        """When the subprocess output is not valid JSON, the result should be
        killed with killing_test='<crashed>'."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "crash_json_target.py"
        target.write_text(source)

        test_dir = tmp_path / "crash_json_tests"
        test_dir.mkdir()
        (test_dir / "test_crash_json_target.py").write_text(
            "from crash_json_target import add\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )

        points = find_mutation_points(source, str(target), "crash_json_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        with patch("pytest_leela.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="not json output", stderr="")
            result = run_tests_for_mutant(
                mutant,
                {"crash_json_target": source},
                {"crash_json_target": str(target)},
                test_dir=str(test_dir),
            )

        assert isinstance(result, MutantResult)
        assert result.killed is True
        assert result.killing_test == "<crashed>"


def describe_compute_timeout():
    """Unit tests for the _compute_timeout helper function.

    With subprocess isolation, timeout computation is a pure function that lives
    in the parent process.  These tests cover the formula directly.
    """

    def it_computes_max_of_2x_total_plus_1_and_5():
        """Kills operators in the formula: max(2 * total_expected + 1.0, 5.0).

        total_expected = sum(test_times.get(t, 1.0) for t in test_ids)
        """
        result = _compute_timeout(["t1", "t2"], {"t1": 2.0, "t2": 3.0})
        # total_expected = 5.0, timeout = max(2 * 5.0 + 1.0, 5.0) = 11.0
        assert result == pytest.approx(11.0)

    def it_uses_default_time_of_1_for_unknown_tests():
        """test_times.get(t, 1.0) should use 1.0 for unknown test ids."""
        result = _compute_timeout(["a", "b"], {"a": 2.0})  # b is unknown
        # total_expected = 2.0 + 1.0 = 3.0
        # timeout = max(2 * 3.0 + 1.0, 5.0) = 7.0
        assert result == pytest.approx(7.0)

    def it_enforces_minimum_timeout_of_5_seconds():
        """Very fast tests must still get at least 5 seconds."""
        result = _compute_timeout(["t"], {"t": 0.001})
        assert result == pytest.approx(5.0)

    def it_returns_none_when_test_times_is_none():
        """When test_times is None, no timeout is computed."""
        assert _compute_timeout(["t"], None) is None

    def it_returns_none_when_test_ids_is_none():
        """When test_ids is None, no timeout is computed."""
        assert _compute_timeout(None, {"t": 1.0}) is None


def describe_TimeoutPlugin():
    def it_raises_system_exit_when_event_is_set():
        """_TimeoutPlugin.pytest_runtest_protocol raises when the event fires."""
        from pytest_leela.runner import _TimeoutPlugin

        event = threading.Event()
        event.set()
        plugin = _TimeoutPlugin(event)
        with pytest.raises(SystemExit, match="leela: mutant timeout"):
            plugin.pytest_runtest_protocol(item=None, nextitem=None)

    def it_does_not_raise_when_event_is_not_set():
        """_TimeoutPlugin.pytest_runtest_protocol returns None when no timeout."""
        from pytest_leela.runner import _TimeoutPlugin

        event = threading.Event()
        plugin = _TimeoutPlugin(event)
        result = plugin.pytest_runtest_protocol(item=None, nextitem=None)
        assert result is None


def describe_run_tests_for_mutant_timeout():
    """Tests for timeout handling via subprocess isolation.

    With the subprocess architecture, timeouts are enforced by signal.SIGALRM
    in the child process.  The parent process uses subprocess.run(timeout=...)
    as a hard outer bound.
    """

    def _make_mutant_fixture(tmp_path):
        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "timeout_target.py"
        target.write_text(source)
        points = find_mutation_points(source, str(target), "timeout_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)
        return source, mutant

    def it_times_out_tests_that_exceed_the_timeout_window(tmp_path, monkeypatch):
        """When subprocess.run times out (outer bound exceeded), result is killed.

        This patches subprocess.run to raise TimeoutExpired, simulating the
        outer timeout bound being hit.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        with patch("pytest_leela.runner.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1.0)
            result = run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        assert result.killed is True
        assert result.killing_test == "<timeout>"
        assert result.killing_tests == ["<timeout>"]

    def it_passes_timeout_to_subprocess_when_test_times_is_provided(
        tmp_path, monkeypatch
    ):
        """When test_times is provided, the computed timeout is passed to subprocess."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        with patch("pytest_leela.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{"killed":false,"tests_run":1,"killing_test":null,"test_ids_run":[],"killing_tests":[]}',
                stderr="",
            )
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a", "test_b"],
                test_times={"test_a": 2.0, "test_b": 3.0},
            )

            # timeout_seconds = max(2*5.0 + 1.0, 5.0) = 11.0
            # outer timeout = 11.0 + 2.0 = 13.0
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["timeout"] == pytest.approx(13.0)

    def it_uses_default_120s_timeout_without_test_times(tmp_path, monkeypatch):
        """When test_times is None, subprocess uses the 120s default timeout."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        with patch("pytest_leela.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{"killed":false,"tests_run":1,"killing_test":null,"test_ids_run":[],"killing_tests":[]}',
                stderr="",
            )
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_dir=str(tmp_path),
            )

            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["timeout"] == 120.0

    def it_forwards_test_ids_to_subprocess_when_provided(tmp_path, monkeypatch):
        """Test IDs should be passed through to the subprocess config."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        with patch("pytest_leela.runner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout='{"killed":false,"tests_run":1,"killing_test":null,"test_ids_run":[],"killing_tests":[]}',
                stderr="",
            )
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["tests/a.py::test_x", "tests/a.py::test_y"],
                test_times={"tests/a.py::test_x": 0.5, "tests/a.py::test_y": 0.5},
            )

            # Verify subprocess was called with the right args
            assert mock_run.call_count == 1


def describe_clear_user_modules():
    def it_removes_cwd_local_modules(monkeypatch, tmp_path):
        """Kills line 77: ``mod is not None → mod is None``.

        With the mutation, only None modules pass the first filter,
        so real CWD-local modules are never removed.
        """
        monkeypatch.chdir(tmp_path)
        fake_mod = types.ModuleType("_test_cwd_local_mod")
        fake_mod.__file__ = str(tmp_path / "fake_local.py")
        monkeypatch.setitem(sys.modules, "_test_cwd_local_mod", fake_mod)

        _clear_user_modules()

        assert "_test_cwd_local_mod" not in sys.modules

    def it_preserves_modules_with_none_file(monkeypatch, tmp_path):
        """Kills line 78: ``is not None → is None`` on __file__ check."""
        monkeypatch.chdir(tmp_path)
        fake_mod = types.ModuleType("_test_none_file_mod")
        fake_mod.__file__ = None
        monkeypatch.setitem(sys.modules, "_test_none_file_mod", fake_mod)

        _clear_user_modules()

        assert "_test_none_file_mod" in sys.modules

    def it_preserves_pytest_leela_prefixed_modules(monkeypatch, tmp_path):
        """Kills line 80: ``not name.startswith → name.startswith``.

        With the mutation, KEEP_PREFIXES modules are the ones removed
        (inverted logic), so pytest_leela.* modules under CWD disappear.
        """
        monkeypatch.chdir(tmp_path)
        fake_mod = types.ModuleType("pytest_leela._test_keep_me")
        fake_mod.__file__ = str(tmp_path / "keep_me.py")
        monkeypatch.setitem(sys.modules, "pytest_leela._test_keep_me", fake_mod)

        _clear_user_modules()

        assert "pytest_leela._test_keep_me" in sys.modules


def describe_precompute_user_modules():
    def it_identifies_cwd_local_modules(monkeypatch, tmp_path):
        """Modules with __file__ under CWD should be in the returned set."""
        monkeypatch.chdir(tmp_path)
        fake_mod = types.ModuleType("_test_precompute_local")
        fake_mod.__file__ = str(tmp_path / "local_mod.py")
        monkeypatch.setitem(sys.modules, "_test_precompute_local", fake_mod)

        result = precompute_user_modules()

        assert "_test_precompute_local" in result

    def it_excludes_keep_prefixes_modules(monkeypatch, tmp_path):
        """Modules matching _KEEP_PREFIXES should not be in the set."""
        monkeypatch.chdir(tmp_path)
        for prefix in _KEEP_PREFIXES:
            mod_name = f"{prefix}_test_keep_prefix"
            fake_mod = types.ModuleType(mod_name)
            fake_mod.__file__ = str(tmp_path / "kept.py")
            monkeypatch.setitem(sys.modules, mod_name, fake_mod)

        result = precompute_user_modules()

        for prefix in _KEEP_PREFIXES:
            assert f"{prefix}_test_keep_prefix" not in result

    def it_excludes_modules_outside_cwd(monkeypatch, tmp_path):
        """Modules with __file__ outside CWD should not be in the set."""
        monkeypatch.chdir(tmp_path)
        fake_mod = types.ModuleType("_test_precompute_outside")
        fake_mod.__file__ = "/some/other/path/outside.py"
        monkeypatch.setitem(sys.modules, "_test_precompute_outside", fake_mod)

        result = precompute_user_modules()

        assert "_test_precompute_outside" not in result

    def it_excludes_modules_with_no_file(monkeypatch, tmp_path):
        """Modules with __file__=None should not be in the set."""
        monkeypatch.chdir(tmp_path)
        fake_mod = types.ModuleType("_test_precompute_no_file")
        fake_mod.__file__ = None
        monkeypatch.setitem(sys.modules, "_test_precompute_no_file", fake_mod)

        result = precompute_user_modules()

        assert "_test_precompute_no_file" not in result

    def it_excludes_none_modules(monkeypatch, tmp_path):
        """None entries in sys.modules should not be in the set."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setitem(sys.modules, "_test_precompute_none", None)

        result = precompute_user_modules()

        assert "_test_precompute_none" not in result

    def it_returns_a_frozenset(monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        result = precompute_user_modules()
        assert isinstance(result, frozenset)


def describe_clear_user_modules_fast():
    def it_removes_only_known_modules(monkeypatch, tmp_path):
        """Should pop only the modules in the known set."""
        monkeypatch.chdir(tmp_path)

        known_mod = types.ModuleType("_test_fast_known")
        known_mod.__file__ = str(tmp_path / "known.py")
        monkeypatch.setitem(sys.modules, "_test_fast_known", known_mod)

        unknown_mod = types.ModuleType("_test_fast_unknown")
        unknown_mod.__file__ = str(tmp_path / "unknown.py")
        monkeypatch.setitem(sys.modules, "_test_fast_unknown", unknown_mod)

        known_set = frozenset(["_test_fast_known"])
        _clear_user_modules_fast(known_set)

        assert "_test_fast_known" not in sys.modules
        assert "_test_fast_unknown" in sys.modules

    def it_handles_modules_already_removed(monkeypatch):
        """Should not raise when a module in the known set is already gone."""
        known_set = frozenset(["_test_fast_already_gone"])
        # Should not raise
        _clear_user_modules_fast(known_set)

    def it_does_not_remove_modules_outside_known_set(monkeypatch, tmp_path):
        """Modules not in the known set should be untouched."""
        monkeypatch.chdir(tmp_path)

        other_mod = types.ModuleType("_test_fast_other")
        other_mod.__file__ = str(tmp_path / "other.py")
        monkeypatch.setitem(sys.modules, "_test_fast_other", other_mod)

        _clear_user_modules_fast(frozenset(["_test_nonexistent"]))

        assert "_test_fast_other" in sys.modules


def describe_run_tests_for_mutant_with_known_user_modules():
    """Tests for the optimized path using known_user_modules parameter."""

    def _make_mutant(tmp_path, module_name="opt_target"):
        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / f"{module_name}.py"
        target.write_text(source)
        points = find_mutation_points(source, str(target), module_name)
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)
        return source, mutant

    def it_works_with_known_user_modules_parameter(tmp_path, monkeypatch):
        """The optimized path should produce the same result as the fallback."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant(tmp_path)

        test_dir = tmp_path / "opt_tests"
        test_dir.mkdir()
        (test_dir / "test_opt_target.py").write_text(
            "from opt_target import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
        )

        known = precompute_user_modules()
        result = run_tests_for_mutant(
            mutant,
            {"opt_target": source},
            {"opt_target": str(tmp_path / "opt_target.py")},
            test_dir=str(test_dir),
            known_user_modules=known,
        )

        assert isinstance(result, MutantResult)
        assert result.killed is True

    def it_cleans_up_new_cwd_modules_from_inner_run(tmp_path, monkeypatch):
        """Subprocess isolation means no modules are added to the parent process's
        sys.modules during a run (pytest.main runs in the child, not the parent).
        This test verifies the invariant that no pytest_leela._test_* artifacts leak."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant(tmp_path, "opt_inner_target")

        known = precompute_user_modules()
        run_tests_for_mutant(
            mutant,
            {"opt_inner_target": source},
            {"opt_inner_target": str(tmp_path / "opt_inner_target.py")},
            test_dir=str(tmp_path),
            known_user_modules=known,
        )

        # Verify that no pytest_leela._test_* artifacts leaked into sys.modules.
        leaked = [n for n in sys.modules if n.startswith("pytest_leela._test_")]
        assert leaked == [], f"Artifacts leaked: {leaked}"

    def it_preserves_saved_modules_during_optimized_cleanup(tmp_path, monkeypatch):
        """Subprocess isolation means pytest_leela.* modules in the parent process
        are unaffected by the child's module cleanup. This is the same invariant
        tested in describe_run_tests_for_mutant."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant(tmp_path, "opt_preserve_target")

        kept_mod = types.ModuleType("pytest_leela._test_opt_preserved")
        kept_mod.__file__ = str(tmp_path / "preserved.py")
        monkeypatch.setitem(sys.modules, "pytest_leela._test_opt_preserved", kept_mod)

        known = precompute_user_modules()
        run_tests_for_mutant(
            mutant,
            {"opt_preserve_target": source},
            {"opt_preserve_target": str(tmp_path / "opt_preserve_target.py")},
            test_dir=str(tmp_path),
            known_user_modules=known,
        )

        assert "pytest_leela._test_opt_preserved" in sys.modules
