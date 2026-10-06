"""Tests for pytest_leela.runner — test execution against mutants."""

import importlib
import os
import sys
import threading
import types
from unittest.mock import MagicMock, patch

import pytest

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.import_hook import MutatingFinder
from pytest_leela.models import Mutant, MutantResult
from pytest_leela.runner import (
    _KEEP_PREFIXES,
    ProjectModuleScope,
    _ResultCollector,
    _clear_framework_caches,
    _django_registry_module_names,
    _inner_run_error,
    _last_line,
    InnerSession,
    _referenced_closure,
    _clear_user_modules,
    _clear_user_modules_fast,
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


class _FakeCollectReport:
    """Minimal stand-in for a pytest CollectReport."""

    def __init__(self, nodeid: str, failed: bool, longreprtext: str = "") -> None:
        self.nodeid = nodeid
        self.failed = failed
        self.longreprtext = longreprtext


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
    def it_calls_clear_framework_caches_at_both_call_sites(tmp_path, monkeypatch):
        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "runner_caches.py"
        target.write_text(source)

        test_dir = tmp_path / "runner_caches_tests"
        test_dir.mkdir()
        (test_dir / "test_runner_caches.py").write_text(
            "from runner_caches import add\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        points = find_mutation_points(source, str(target), "runner_caches")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        with patch("pytest_leela.runner._clear_framework_caches") as mock_clear:
            run_tests_for_mutant(
                mutant,
                {"runner_caches": source},
                {"runner_caches": str(target)},
                test_dir=str(test_dir),
            )

        # Called at both sites: pre-test setup (line 108) and finally cleanup (line 188)
        assert mock_clear.call_count == 2

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
        # Mutate > to >= (n >= 0 still passes for n=5 and n=-5)
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

    def it_returns_error_result_when_pytest_main_crashes(tmp_path, monkeypatch):
        """A crashed runner is an ERROR, never a kill: no test caught anything.

        Also kills ``- → +/*`` on the crash handler's elapsed time and
        ``return expr → None`` on its return.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "crash_target.py"
        target.write_text(source)

        points = find_mutation_points(source, str(target), "crash_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        with patch("pytest_leela.runner.pytest.main", side_effect=RuntimeError("boom")):
            result = run_tests_for_mutant(
                mutant,
                {"crash_target": source},
                {"crash_target": str(target)},
                test_dir=str(tmp_path),
            )

        assert isinstance(result, MutantResult)
        assert result.killed is False
        assert result.status == "error"
        assert result.error == "pytest crashed: RuntimeError: boom"
        assert result.killing_test is None
        # - → + would produce a value >> 60
        assert 0 <= result.time_seconds < 60

    def it_preserves_modules_in_saved_snapshot_during_cleanup(tmp_path, monkeypatch):
        """Kills line 186: ``not in → in`` in cleanup loop.

        The cleanup loop (lines 185-190) should only examine modules NOT in
        saved_modules (new ones from inner run).  With the mutation it examines
        modules that ARE in saved_modules, incorrectly removing KEEP_PREFIXES
        modules with CWD __file__.
        """
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

        # A pytest_leela.* module survives _clear_user_modules (KEEP_PREFIXES)
        # and enters saved_modules.  With the mutation, the cleanup loop
        # examines it and removes it (CWD __file__).
        kept_mod = types.ModuleType("pytest_leela._test_saved_mod")
        kept_mod.__file__ = str(tmp_path / "saved.py")
        monkeypatch.setitem(sys.modules, "pytest_leela._test_saved_mod", kept_mod)

        with patch("pytest_leela.runner.pytest.main", return_value=0):
            run_tests_for_mutant(
                mutant,
                {"saved_mod_target": source},
                {"saved_mod_target": str(target)},
                test_dir=str(tmp_path),
            )

        assert "pytest_leela._test_saved_mod" in sys.modules

    def it_cleans_up_cwd_modules_added_during_inner_run(tmp_path, monkeypatch):
        """Kills line 188: ``is not → is`` in cleanup mod_file check.

        With the mutation, non-None modules get mod_file=None (from else
        branch), so CWD-local modules added during inner run are never removed.
        Uses KEEP_PREFIXES name so only the cleanup loop (not _clear_user_modules
        in outer finally) is responsible for removal.
        """
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

        inner_mod_name = "pytest_leela._test_inner_artifact"
        inner_mod_file = str(tmp_path / "inner_artifact.py")

        def mock_pytest_main(args, plugins=None):
            """Simulate inner run adding a CWD-local module."""
            fake = types.ModuleType(inner_mod_name)
            fake.__file__ = inner_mod_file
            sys.modules[inner_mod_name] = fake
            return 0

        with patch("pytest_leela.runner.pytest.main", side_effect=mock_pytest_main):
            run_tests_for_mutant(
                mutant,
                {"inner_cleanup_target": source},
                {"inner_cleanup_target": str(target)},
                test_dir=str(tmp_path),
            )

        # With correct code: new CWD-local module is removed by cleanup loop.
        # With mutation: mod_file is None for non-None modules → not removed.
        assert inner_mod_name not in sys.modules

    def it_preserves_non_cwd_modules_added_during_inner_run(tmp_path, monkeypatch):
        """Kills the ``and → or`` mutation on the inline cleanup mod_file
        check (the ``mod_file is not None and mod_file.startswith(cwd_prefix)``
        guard).  With the mutation, the inline cleanup pops every non-None
        module added during the inner run, regardless of whether its
        __file__ is under CWD — clobbering modules that legitimately live
        outside the project tree.

        Uses a KEEP_PREFIXES name so the outer ``_clear_user_modules`` is
        not responsible for removal — only the inline cleanup loop.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "outside_cwd_target.py"
        target.write_text(source)

        points = find_mutation_points(source, str(target), "outside_cwd_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        inner_mod_name = "pytest_leela._test_outside_cwd_artifact"
        # __file__ deliberately outside the temporary CWD.
        outside_mod_file = "/non/existent/elsewhere/outside.py"

        def mock_pytest_main(args, plugins=None):
            fake = types.ModuleType(inner_mod_name)
            fake.__file__ = outside_mod_file
            sys.modules[inner_mod_name] = fake
            return 0

        try:
            with patch(
                "pytest_leela.runner.pytest.main", side_effect=mock_pytest_main
            ):
                run_tests_for_mutant(
                    mutant,
                    {"outside_cwd_target": source},
                    {"outside_cwd_target": str(target)},
                    test_dir=str(tmp_path),
                )

            # Original: non-CWD module is preserved (False AND ... or True AND False).
            # Mutated (and → or): True OR ... → popped, even though __file__
            # is not under cwd_prefix.
            assert inner_mod_name in sys.modules
            assert sys.modules[inner_mod_name].__file__ == outside_mod_file
        finally:
            sys.modules.pop(inner_mod_name, None)

    def it_calculates_elapsed_time_by_subtraction(tmp_path, monkeypatch):
        """Kills line 199: ``- → +/*`` in final elapsed calculation.

        Mocks time.monotonic to return controlled values; asserts the result
        is the difference (5.0), not the sum (205.0) or product (10500.0).
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "time_target.py"
        target.write_text(source)

        points = find_mutation_points(source, str(target), "time_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        with (
            patch("pytest_leela.runner.time.monotonic", side_effect=[100.0, 105.0]),
            patch("pytest_leela.runner.pytest.main", return_value=0),
        ):
            result = run_tests_for_mutant(
                mutant,
                {"time_target": source},
                {"time_target": str(target)},
                test_dir=str(tmp_path),
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
        # killing_tests should be a subset of test_ids_run
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

    def it_populates_crash_fields_when_pytest_main_crashes(tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "crash_ids_target.py"
        target.write_text(source)

        points = find_mutation_points(source, str(target), "crash_ids_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        with patch("pytest_leela.runner.pytest.main", side_effect=RuntimeError("boom")):
            result = run_tests_for_mutant(
                mutant,
                {"crash_ids_target": source},
                {"crash_ids_target": str(target)},
                test_dir=str(tmp_path),
            )

        assert result.killed is False
        assert result.test_ids_run == []
        assert result.killing_tests == []

    def it_removes_stale_mutating_finders_from_meta_path(tmp_path, monkeypatch):
        """Kills line 219: ``not isinstance → isinstance``.

        With the mutation, the safety-net filter keeps ONLY MutatingFinders
        and removes all other finders — the opposite of intended behavior.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "stale_finder_target.py"
        target.write_text(source)

        points = find_mutation_points(source, str(target), "stale_finder_target")
        binop_point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)

        stale = MutatingFinder({"stale": "x = 1"}, mutant)
        saved_meta_path = sys.meta_path[:]
        sys.meta_path.insert(0, stale)

        try:
            with patch("pytest_leela.runner.pytest.main", return_value=0):
                run_tests_for_mutant(
                    mutant,
                    {"stale_finder_target": source},
                    {"stale_finder_target": str(target)},
                    test_dir=str(tmp_path),
                )

            remaining = [f for f in sys.meta_path if isinstance(f, MutatingFinder)]
            assert remaining == []
        finally:
            # Restore sys.meta_path if the mutation clobbered it
            sys.meta_path[:] = saved_meta_path


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
    """Tests for the timeout computation and timeout-related code paths."""

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

    def it_computes_timeout_correctly_from_test_times(tmp_path, monkeypatch):
        """Kills line 218: ``+ → -``, ``* → +``, ``* → /``, ``+ → *``.

        timeout_seconds = max(2 * total_expected + 1.0, 5.0)
        total_expected = sum(test_times.get(t, 1.0) for t in test_ids)

        With test_times = {"t1": 2.0, "t2": 3.0}, total_expected = 5.0
        Correct: max(2 * 5.0 + 1.0, 5.0) = max(11.0, 5.0) = 11.0

        Mutant ``2 * total_expected - 1.0``: max(10.0 - 1.0, 5.0) = max(9.0, 5.0) = 9.0
        Mutant ``2 + total_expected + 1.0``: max(2 + 5.0 + 1.0, 5.0) = max(8.0, 5.0) = 8.0
        Mutant ``2 * total_expected * 1.0``: max(10.0, 5.0) = 10.0
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        test_times = {"test_a": 2.0, "test_b": 3.0}
        test_ids = ["test_a", "test_b"]

        created_timer = []

        original_timer_init = threading.Timer.__init__

        def capture_timer(self, interval, function, *args, **kwargs):
            created_timer.append(interval)
            original_timer_init(self, interval, function, *args, **kwargs)

        with (
            patch("pytest_leela.runner.pytest.main", return_value=0),
            patch.object(threading.Timer, "__init__", capture_timer),
        ):
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=test_ids,
                test_times=test_times,
            )

        assert len(created_timer) == 1
        # Correct: max(2 * 5.0 + 1.0, 5.0) = 11.0
        assert created_timer[0] == pytest.approx(11.0)

    def it_uses_default_time_for_unknown_tests(tmp_path, monkeypatch):
        """Tests that test_times.get(t, 1.0) uses 1.0 default for unknown tests."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        test_times = {"test_a": 2.0}  # test_b is unknown
        test_ids = ["test_a", "test_b"]

        created_timer = []
        original_timer_init = threading.Timer.__init__

        def capture_timer(self, interval, function, *args, **kwargs):
            created_timer.append(interval)
            original_timer_init(self, interval, function, *args, **kwargs)

        with (
            patch("pytest_leela.runner.pytest.main", return_value=0),
            patch.object(threading.Timer, "__init__", capture_timer),
        ):
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=test_ids,
                test_times=test_times,
            )

        assert len(created_timer) == 1
        # total_expected = 2.0 + 1.0 = 3.0
        # timeout = max(2 * 3.0 + 1.0, 5.0) = max(7.0, 5.0) = 7.0
        assert created_timer[0] == pytest.approx(7.0)

    def it_does_not_create_timer_without_test_times(tmp_path, monkeypatch):
        """Kills line 224: ``is not → is`` on timer guard.

        When test_times is None, no timer should be created and
        _TimeoutPlugin should NOT be in the plugins list.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        captured_plugins = []

        def mock_pytest_main(args, plugins=None):
            captured_plugins.extend(plugins or [])
            return 0

        with patch("pytest_leela.runner.pytest.main", side_effect=mock_pytest_main):
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times=None,  # No test_times
            )

        from pytest_leela.runner import _TimeoutPlugin

        timeout_plugins = [p for p in captured_plugins if isinstance(p, _TimeoutPlugin)]
        assert timeout_plugins == [], (
            "TimeoutPlugin should not be added without test_times"
        )

    def it_includes_timeout_plugin_when_timer_is_created(tmp_path, monkeypatch):
        """Positive case: when test_times is provided, TimeoutPlugin IS added."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        captured_plugins = []

        def mock_pytest_main(args, plugins=None):
            captured_plugins.extend(plugins or [])
            return 0

        with patch("pytest_leela.runner.pytest.main", side_effect=mock_pytest_main):
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        from pytest_leela.runner import _TimeoutPlugin

        timeout_plugins = [p for p in captured_plugins if isinstance(p, _TimeoutPlugin)]
        assert len(timeout_plugins) == 1

    def it_returns_timeout_result_when_timed_out_flag_is_set(tmp_path, monkeypatch):
        """Kills lines 274-283: timed_out.is_set() post-run check.

        When the timeout fires but pytest catches the SystemExit internally,
        the post-run check at line 274 should detect it and return a killed result.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        def mock_pytest_main(args, plugins=None):
            # Simulate: one test passes, then the timeout fires and pytest
            # swallows the SystemExit
            plugins[0].pytest_runtest_logreport(
                _FakeReport("test_a", "call", passed=True, failed=False)
            )
            for p in plugins or []:
                if hasattr(p, "event"):
                    p.event.set()  # Set the timed_out event
            return 0  # pytest returns normally

        with patch("pytest_leela.runner.pytest.main", side_effect=mock_pytest_main):
            result = run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        assert result.killed is True
        assert result.killing_test == "<timeout>"
        assert "<timeout>" in result.killing_tests
        # The run did not crash, so the tests it ran are still reported.
        assert result.test_ids_run == ["test_a"]
        # Elapsed should be reasonable (not a huge value from wrong arithmetic)
        assert 0 <= result.time_seconds < 60

    def it_reports_no_tests_run_when_the_timeout_escapes_as_a_crash(
        tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        def mock_pytest_main(args, plugins=None):
            plugins[0].pytest_runtest_logreport(
                _FakeReport("test_a", "call", passed=True, failed=False)
            )
            plugins[1].event.set()
            raise SystemExit("leela: mutant timeout")

        with patch("pytest_leela.runner.pytest.main", side_effect=mock_pytest_main):
            result = run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        assert result.killing_test == "<timeout>"
        assert result.test_ids_run == []

    def it_returns_timeout_result_when_system_exit_is_raised(tmp_path, monkeypatch):
        """When timeout causes SystemExit to propagate, result should be killed."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        def mock_pytest_main(args, plugins=None):
            for p in plugins or []:
                if hasattr(p, "event"):
                    p.event.set()
            raise SystemExit("leela: mutant timeout")

        with patch("pytest_leela.runner.pytest.main", side_effect=mock_pytest_main):
            result = run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        assert result.killed is True
        assert result.killing_test == "<timeout>"

    def it_enforces_minimum_timeout_of_5_seconds(tmp_path, monkeypatch):
        """Timeout should be at least 5.0 seconds even for very fast tests."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        test_times = {"test_a": 0.001}
        test_ids = ["test_a"]

        created_timer = []
        original_timer_init = threading.Timer.__init__

        def capture_timer(self, interval, function, *args, **kwargs):
            created_timer.append(interval)
            original_timer_init(self, interval, function, *args, **kwargs)

        with (
            patch("pytest_leela.runner.pytest.main", return_value=0),
            patch.object(threading.Timer, "__init__", capture_timer),
        ):
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=test_ids,
                test_times=test_times,
            )

        assert len(created_timer) == 1
        # max(2 * 0.001 + 1.0, 5.0) = max(1.002, 5.0) = 5.0
        assert created_timer[0] == pytest.approx(5.0)

    def it_does_not_create_timer_without_test_ids(tmp_path, monkeypatch):
        """When test_ids is None/empty, no timer should be created."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        captured_plugins = []

        def mock_pytest_main(args, plugins=None):
            captured_plugins.extend(plugins or [])
            return 0

        with patch("pytest_leela.runner.pytest.main", side_effect=mock_pytest_main):
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_dir=str(tmp_path),
                test_times={"test_a": 1.0},
                # test_ids is None by default
            )

        from pytest_leela.runner import _TimeoutPlugin

        timeout_plugins = [p for p in captured_plugins if isinstance(p, _TimeoutPlugin)]
        assert timeout_plugins == []


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
        """Optimized path should remove NEW CWD-local modules added by inner run."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant(tmp_path, "opt_inner_target")

        inner_mod_name = "pytest_leela._test_opt_inner_artifact"
        inner_mod_file = str(tmp_path / "inner_artifact.py")

        def mock_pytest_main(args, plugins=None):
            fake = types.ModuleType(inner_mod_name)
            fake.__file__ = inner_mod_file
            sys.modules[inner_mod_name] = fake
            return 0

        known = precompute_user_modules()
        with patch("pytest_leela.runner.pytest.main", side_effect=mock_pytest_main):
            run_tests_for_mutant(
                mutant,
                {"opt_inner_target": source},
                {"opt_inner_target": str(tmp_path / "opt_inner_target.py")},
                test_dir=str(tmp_path),
                known_user_modules=known,
            )

        assert inner_mod_name not in sys.modules

    def it_preserves_saved_modules_during_optimized_cleanup(tmp_path, monkeypatch):
        """Optimized path should not evict modules that existed at snapshot time."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant(tmp_path, "opt_preserve_target")

        kept_mod = types.ModuleType("pytest_leela._test_opt_preserved")
        kept_mod.__file__ = str(tmp_path / "preserved.py")
        monkeypatch.setitem(sys.modules, "pytest_leela._test_opt_preserved", kept_mod)

        known = precompute_user_modules()
        with patch("pytest_leela.runner.pytest.main", return_value=0):
            run_tests_for_mutant(
                mutant,
                {"opt_preserve_target": source},
                {"opt_preserve_target": str(tmp_path / "opt_preserve_target.py")},
                test_dir=str(tmp_path),
                known_user_modules=known,
            )

        assert "pytest_leela._test_opt_preserved" in sys.modules

    def it_uses_fast_clear_instead_of_full_scan(tmp_path, monkeypatch):
        """When known_user_modules is provided, _clear_user_modules_fast is used."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant(tmp_path, "opt_fast_target")
        known = frozenset(["some_module"])

        with (
            patch("pytest_leela.runner.pytest.main", return_value=0),
            patch("pytest_leela.runner._clear_user_modules_fast") as mock_fast,
            patch("pytest_leela.runner._clear_user_modules") as mock_full,
        ):
            run_tests_for_mutant(
                mutant,
                {"opt_fast_target": source},
                {"opt_fast_target": str(tmp_path / "opt_fast_target.py")},
                test_dir=str(tmp_path),
                known_user_modules=known,
            )

        # Fast path called at both pre-test and finally cleanup sites
        assert mock_fast.call_count == 2
        mock_full.assert_not_called()

    def it_falls_back_to_full_scan_without_known_user_modules(tmp_path, monkeypatch):
        """Without known_user_modules, the original _clear_user_modules is used."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant(tmp_path, "opt_fallback_target")

        with (
            patch("pytest_leela.runner.pytest.main", return_value=0),
            patch("pytest_leela.runner._clear_user_modules_fast") as mock_fast,
            patch("pytest_leela.runner._clear_user_modules") as mock_full,
        ):
            run_tests_for_mutant(
                mutant,
                {"opt_fallback_target": source},
                {"opt_fallback_target": str(tmp_path / "opt_fallback_target.py")},
                test_dir=str(tmp_path),
            )

        mock_fast.assert_not_called()
        assert mock_full.call_count == 2


_ONCE_ONLY_EXT = (
    "import builtins\n"
    "\n"
    "# Stand-in for a C extension (numpy's _multiarray_umath and friends):\n"
    "# executing it a second time in one process raises, exactly as CPython\n"
    "# does for single-phase-init extension modules.\n"
    "_FLAG = '_leela_once_only_ext_loaded'\n"
    "if getattr(builtins, _FLAG, False):\n"
    "    raise ImportError('cannot load module more than once per process')\n"
    "setattr(builtins, _FLAG, True)\n"
)


def describe_run_tests_for_mutant_with_venv_inside_project():
    """Regression: a virtualenv under the project cwd (uv's ``.venv``) must
    not have its packages evicted between mutants.  Evicting them makes a
    once-only extension fail to re-import, and that crash used to be scored
    as a kill."""

    def _make_project(tmp_path, monkeypatch):
        import builtins

        site_packages = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages"
        site_packages.mkdir(parents=True)
        (site_packages / "once_only_ext.py").write_text(_ONCE_ONLY_EXT)

        source = "import once_only_ext\n\n\ndef add(a, b):\n    return a + b\n"
        target = tmp_path / "venv_calc.py"
        target.write_text(source)

        test_dir = tmp_path / "venv_calc_tests"
        test_dir.mkdir()
        # Deliberately weak: add(0, 0) == 0 also holds for a - b, so the
        # Add -> Sub mutant must SURVIVE.  The import is inside the test so
        # a broken re-import surfaces as a test failure, not a collection
        # error.
        (test_dir / "test_venv_calc.py").write_text(
            "def test_add_zeros():\n"
            "    from venv_calc import add\n"
            "    assert add(0, 0) == 0\n"
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(site_packages))
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.setattr(
            builtins, "_leela_once_only_ext_loaded", False, raising=False
        )
        for name in ("once_only_ext", "venv_calc"):
            monkeypatch.delitem(sys.modules, name, raising=False)

        # Prime the extension as the outer pytest session would have.
        ext = importlib.import_module("once_only_ext")

        points = find_mutation_points(source, str(target), "venv_calc")
        point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
        return source, target, test_dir, mutant, ext

    def it_reports_a_weakly_tested_mutant_as_survived(tmp_path, monkeypatch):
        source, target, test_dir, mutant, _ = _make_project(tmp_path, monkeypatch)

        result = run_tests_for_mutant(
            mutant,
            {"venv_calc": source},
            {"venv_calc": str(target)},
            test_dir=str(test_dir),
            known_user_modules=precompute_user_modules(),
        )

        assert result.status == "survived"
        assert result.killed is False
        assert result.tests_run == 1
        assert result.killing_tests == []

    def it_reports_survived_on_the_full_scan_path_too(tmp_path, monkeypatch):
        source, target, test_dir, mutant, _ = _make_project(tmp_path, monkeypatch)

        result = run_tests_for_mutant(
            mutant,
            {"venv_calc": source},
            {"venv_calc": str(target)},
            test_dir=str(test_dir),
        )

        assert result.status == "survived"
        assert result.killed is False

    def it_keeps_the_extension_loaded_across_mutants(tmp_path, monkeypatch):
        source, target, test_dir, mutant, ext = _make_project(tmp_path, monkeypatch)

        run_tests_for_mutant(
            mutant,
            {"venv_calc": source},
            {"venv_calc": str(target)},
            test_dir=str(test_dir),
            known_user_modules=precompute_user_modules(),
        )

        assert sys.modules["once_only_ext"] is ext


def describe_run_tests_for_mutant_inner_run_errors():
    """A mutant whose inner run never exercised a test is an ERROR — neither
    a kill (nothing caught it) nor a survival (nothing ran)."""

    def it_kills_a_mutant_that_makes_the_target_raise_on_import(tmp_path, monkeypatch):
        """The suite went red: every test importing the target errored.

        Add -> Sub makes DIVISOR zero, so executing the mutated module raises
        and the test file errors during collection.
        """
        source = "DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n"
        target = tmp_path / "collect_err_target.py"
        target.write_text(source)

        test_dir = tmp_path / "collect_err_tests"
        test_dir.mkdir()
        (test_dir / "test_collect_err_target.py").write_text(
            "from collect_err_target import RATIO\n\n"
            "def test_ratio():\n"
            "    assert RATIO > 0\n"
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        points = find_mutation_points(source, str(target), "collect_err_target")
        point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)

        result = run_tests_for_mutant(
            mutant,
            {"collect_err_target": source},
            {"collect_err_target": str(target)},
            test_dir=str(test_dir),
        )

        assert result.status == "killed"
        assert result.error is None
        assert result.tests_run == 0
        assert result.killing_test == "collect_err_tests/test_collect_err_target.py"
        assert result.killing_tests == ["collect_err_tests/test_collect_err_target.py"]

    def it_reports_a_collection_error_raised_outside_the_target_as_error(
        tmp_path, monkeypatch
    ):
        """The target imports fine; the *test* module raises at import."""
        source = "LIMIT = 1 + 1\n"
        target = tmp_path / "coll_outside_target.py"
        target.write_text(source)
        test_dir = tmp_path / "coll_outside_tests"
        test_dir.mkdir()
        (test_dir / "test_coll_outside.py").write_text(
            "from coll_outside_target import LIMIT\n\n"
            "if LIMIT != 2:\n"
            "    raise RuntimeError('limit changed')\n\n"
            "def test_limit():\n"
            "    assert True\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        points = find_mutation_points(source, str(target), "coll_outside_target")
        point = next(p for p in points if p.node_type == "BinOp")
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)

        result = run_tests_for_mutant(
            mutant,
            {"coll_outside_target": source},
            {"coll_outside_target": str(target)},
            test_dir=str(test_dir),
        )

        assert result.status == "error"
        assert result.error == (
            "pytest exited with INTERRUPTED (coll_outside_tests/"
            "test_coll_outside.py: E   RuntimeError: limit changed)"
        )

    def it_names_the_failed_import_when_no_collection_report_exists(
        tmp_path, monkeypatch
    ):
        """A conftest importing the broken target yields no collect report."""
        source = "DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n"
        target = tmp_path / "conftest_target.py"
        target.write_text(source)
        test_dir = tmp_path / "conftest_tests"
        test_dir.mkdir()
        (test_dir / "conftest.py").write_text("import conftest_target\n")
        (test_dir / "test_c.py").write_text("def test_c():\n    pass\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        points = find_mutation_points(source, str(target), "conftest_target")
        point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)

        result = run_tests_for_mutant(
            mutant,
            {"conftest_target": source},
            {"conftest_target": str(target)},
            test_dir=str(test_dir),
        )

        assert result.status == "killed"
        [killing_test] = result.killing_tests
        # The exception message differs between Python versions.
        assert killing_test.startswith("<import of conftest_target: ZeroDivisionError:")


def _isolated_scope_env(monkeypatch, prefix, site_packages=(), user_site="/nowhere"):
    """Pin every environment root ProjectModuleScope reads."""
    for attr in ("prefix", "base_prefix", "exec_prefix", "base_exec_prefix"):
        monkeypatch.setattr(sys, attr, str(prefix))
    monkeypatch.setattr(
        "pytest_leela.runner.site.getsitepackages", lambda: list(site_packages)
    )
    monkeypatch.setattr(
        "pytest_leela.runner.site.getusersitepackages", lambda: str(user_site)
    )


def describe_ProjectModuleScope():
    def it_defaults_cwd_to_the_working_directory(tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert ProjectModuleScope().cwd == str(tmp_path) + os.sep

    def it_uses_an_explicit_cwd_over_the_working_directory(tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        other = tmp_path / "other"
        assert ProjectModuleScope(str(other)).cwd == str(other) + os.sep

    def it_contains_project_source_under_cwd(tmp_path, monkeypatch):
        _isolated_scope_env(monkeypatch, "/opt/py")
        scope = ProjectModuleScope(str(tmp_path))
        assert scope.contains(str(tmp_path / "app" / "models.py")) is True

    def it_excludes_files_outside_cwd(tmp_path, monkeypatch):
        _isolated_scope_env(monkeypatch, "/opt/py")
        scope = ProjectModuleScope(str(tmp_path / "proj"))
        assert scope.contains(str(tmp_path / "elsewhere.py")) is False

    def it_excludes_a_sibling_directory_sharing_the_cwd_prefix(tmp_path, monkeypatch):
        _isolated_scope_env(monkeypatch, "/opt/py")
        scope = ProjectModuleScope(str(tmp_path / "proj"))
        assert scope.contains(str(tmp_path / "proj2" / "x.py")) is False

    def it_excludes_the_active_venv_inside_cwd(tmp_path, monkeypatch):
        """sys.prefix under the project (uv's .venv) is not project source,
        even for a file outside any site-packages directory."""
        venv = tmp_path / ".venv"
        _isolated_scope_env(monkeypatch, venv)
        scope = ProjectModuleScope(str(tmp_path))
        assert scope.contains(str(venv / "lib" / "python3.13" / "x.py")) is False

    def it_excludes_reported_site_packages_dirs(tmp_path, monkeypatch):
        pkgs = tmp_path / "env" / "pkgs"
        _isolated_scope_env(monkeypatch, "/opt/py", site_packages=[pkgs])
        scope = ProjectModuleScope(str(tmp_path))
        assert scope.contains(str(pkgs / "numpy" / "__init__.py")) is False

    def it_excludes_the_user_site_dir(tmp_path, monkeypatch):
        user = tmp_path / "user-site"
        _isolated_scope_env(monkeypatch, "/opt/py", user_site=user)
        scope = ProjectModuleScope(str(tmp_path))
        assert scope.contains(str(user / "requests.py")) is False

    def it_excludes_any_site_packages_segment_below_cwd(tmp_path, monkeypatch):
        _isolated_scope_env(monkeypatch, "/opt/py")
        scope = ProjectModuleScope(str(tmp_path))
        path = tmp_path / "other-venv" / "lib" / "site-packages" / "np.py"
        assert scope.contains(str(path)) is False

    def it_excludes_any_dist_packages_segment_below_cwd(tmp_path, monkeypatch):
        _isolated_scope_env(monkeypatch, "/opt/py")
        scope = ProjectModuleScope(str(tmp_path))
        path = tmp_path / "usr" / "lib" / "dist-packages" / "np.py"
        assert scope.contains(str(path)) is False

    def it_ignores_package_segments_above_cwd(tmp_path, monkeypatch):
        """Only the part of the path below cwd is checked for segments."""
        _isolated_scope_env(monkeypatch, "/opt/py")
        cwd = tmp_path / "site-packages" / "proj"
        scope = ProjectModuleScope(str(cwd))
        assert scope.contains(str(cwd / "app.py")) is True

    def it_ignores_an_environment_root_that_contains_cwd(tmp_path, monkeypatch):
        """A system Python at /usr with the project in /usr/src/app (or
        ``python -m venv .``) must not exclude every project file."""
        _isolated_scope_env(
            monkeypatch, tmp_path, site_packages=[tmp_path], user_site=tmp_path
        )
        scope = ProjectModuleScope(str(tmp_path / "src" / "app"))
        assert scope.environment_roots == ()
        assert scope.contains(str(tmp_path / "src" / "app" / "views.py")) is True

    def it_keeps_environment_roots_that_do_not_contain_cwd(tmp_path, monkeypatch):
        venv = tmp_path / ".venv"
        _isolated_scope_env(monkeypatch, venv, site_packages=[venv / "sp"])
        scope = ProjectModuleScope(str(tmp_path))
        # Sorted, so the order depends on where tmp_path lives.
        assert scope.environment_roots == tuple(
            sorted(["/nowhere" + os.sep, str(venv) + os.sep, str(venv / "sp") + os.sep])
        )

    def describe_module_names():
        def it_lists_loaded_project_modules(tmp_path, monkeypatch):
            mod = types.ModuleType("_scope_project_mod")
            mod.__file__ = str(tmp_path / "proj_mod.py")
            monkeypatch.setitem(sys.modules, "_scope_project_mod", mod)
            names = ProjectModuleScope(str(tmp_path)).module_names()
            assert "_scope_project_mod" in names
            assert isinstance(names, frozenset)

        def it_skips_installed_packages_under_cwd(tmp_path, monkeypatch):
            mod = types.ModuleType("_scope_venv_mod")
            mod.__file__ = str(tmp_path / ".venv" / "site-packages" / "m.py")
            monkeypatch.setitem(sys.modules, "_scope_venv_mod", mod)
            names = ProjectModuleScope(str(tmp_path)).module_names()
            assert "_scope_venv_mod" not in names

        def it_skips_keep_prefix_modules(tmp_path, monkeypatch):
            mod = types.ModuleType("pytest_leela._scope_kept")
            mod.__file__ = str(tmp_path / "kept.py")
            monkeypatch.setitem(sys.modules, "pytest_leela._scope_kept", mod)
            names = ProjectModuleScope(str(tmp_path)).module_names()
            assert "pytest_leela._scope_kept" not in names

        def it_skips_none_and_fileless_modules(tmp_path, monkeypatch):
            fileless = types.ModuleType("_scope_fileless")
            fileless.__file__ = None
            monkeypatch.setitem(sys.modules, "_scope_fileless", fileless)
            monkeypatch.setitem(sys.modules, "_scope_none", None)
            names = ProjectModuleScope(str(tmp_path)).module_names()
            assert "_scope_fileless" not in names
            assert "_scope_none" not in names


def describe_clear_user_modules_with_venv_inside_cwd():
    def it_keeps_installed_packages_under_cwd(tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mod = types.ModuleType("_venv_pkg_mod")
        mod.__file__ = str(tmp_path / ".venv" / "lib" / "site-packages" / "p.py")
        monkeypatch.setitem(sys.modules, "_venv_pkg_mod", mod)

        _clear_user_modules()

        assert "_venv_pkg_mod" in sys.modules

    def it_keeps_installed_packages_out_of_the_precomputed_set(tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mod = types.ModuleType("_venv_pkg_pre")
        mod.__file__ = str(tmp_path / ".venv" / "lib" / "site-packages" / "p.py")
        monkeypatch.setitem(sys.modules, "_venv_pkg_pre", mod)

        assert "_venv_pkg_pre" not in precompute_user_modules()


def describe_ResultCollector_collection_errors():
    def it_records_failed_collection_reports_with_their_summary():
        collector = _ResultCollector()
        collector.pytest_collectreport(
            _FakeCollectReport(
                "tests/test_x.py",
                failed=True,
                longreprtext="trace\nE   ImportError: nope\n",
            )
        )
        assert collector.collection_errors == ["tests/test_x.py: E   ImportError: nope"]
        assert collector.collection_error_ids == ["tests/test_x.py"]

    def it_records_just_the_nodeid_when_the_report_has_no_text():
        collector = _ResultCollector()
        collector.pytest_collectreport(
            _FakeCollectReport("tests/test_y.py", failed=True, longreprtext="")
        )
        assert collector.collection_errors == ["tests/test_y.py"]

    def it_ignores_successful_collection_reports():
        collector = _ResultCollector()
        collector.pytest_collectreport(_FakeCollectReport("tests/test_x.py", False))
        assert collector.collection_errors == []


def describe_last_line():
    def it_returns_the_last_non_blank_line_stripped():
        assert _last_line("first\n  second  \n\n   \n") == "second"

    def it_returns_none_for_empty_text():
        assert _last_line("") is None

    def it_returns_none_for_whitespace_only_text():
        assert _last_line("  \n \n") is None


def describe_inner_run_error():
    def _collector(total=0, collection_errors=()):
        collector = _ResultCollector()
        collector.total = total
        collector.collection_errors = list(collection_errors)
        return collector

    def it_is_none_when_tests_ran_and_passed():
        assert _inner_run_error(pytest.ExitCode.OK, _collector(total=2)) is None

    def it_is_none_for_tests_failed_exit_code_with_tests_run():
        assert _inner_run_error(pytest.ExitCode.TESTS_FAILED, _collector(1)) is None

    def it_names_an_abnormal_exit_code():
        reason = _inner_run_error(pytest.ExitCode.INTERRUPTED, _collector(total=1))
        assert reason == "pytest exited with INTERRUPTED"

    def it_accepts_a_plain_int_exit_code():
        reason = _inner_run_error(4, _collector(total=1))
        assert reason == "pytest exited with USAGE_ERROR"

    def it_labels_an_exit_code_pytest_does_not_define():
        """A plugin can set any int as the session exit status."""
        reason = _inner_run_error(42, _collector(total=1))
        assert reason == "pytest exited with exit code 42"

    def it_appends_every_collection_error():
        reason = _inner_run_error(
            pytest.ExitCode.INTERRUPTED,
            _collector(collection_errors=["a.py: E1", "b.py: E2"]),
        )
        assert reason == "pytest exited with INTERRUPTED (a.py: E1; b.py: E2)"

    def it_reports_zero_tests_run_on_a_clean_exit():
        assert _inner_run_error(pytest.ExitCode.OK, _collector(total=0)) == (
            "no tests ran"
        )


def describe_run_tests_for_mutant_classification():
    """Each branch of the kill / survive / error decision, driven by a fake
    inner ``pytest.main`` that feeds the result collector directly."""

    def _mutant(tmp_path, monkeypatch, name):
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / f"{name}.py"
        target.write_text(source)
        points = find_mutation_points(source, str(target), name)
        point = next(p for p in points if p.node_type == "BinOp")
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
        return mutant, {name: source}, {name: str(target)}

    def _fake_main(reports, exit_code):
        def fake(args, plugins=None):
            for report in reports:
                plugins[0].pytest_runtest_logreport(report)
            return exit_code

        return fake

    def _run(tmp_path, monkeypatch, name, reports, exit_code):
        mutant, sources, files = _mutant(tmp_path, monkeypatch, name)
        with patch(
            "pytest_leela.runner.pytest.main",
            side_effect=_fake_main(reports, exit_code),
        ):
            return run_tests_for_mutant(mutant, sources, files, test_dir=str(tmp_path))

    def it_survives_when_tests_ran_and_passed(tmp_path, monkeypatch):
        result = _run(
            tmp_path,
            monkeypatch,
            "cls_survive",
            [_FakeReport("t::a", "call", passed=True, failed=False)],
            pytest.ExitCode.OK,
        )
        assert result.status == "survived"
        assert result.error is None
        assert result.tests_run == 1
        assert result.test_ids_run == ["t::a"]

    def it_kills_on_a_failing_test(tmp_path, monkeypatch):
        result = _run(
            tmp_path,
            monkeypatch,
            "cls_fail",
            [
                _FakeReport("t::a", "call", passed=True, failed=False),
                _FakeReport("t::b", "call", passed=False, failed=True),
            ],
            pytest.ExitCode.TESTS_FAILED,
        )
        assert result.status == "killed"
        assert result.killing_test == "t::b"
        assert result.killing_tests == ["t::b"]
        assert result.test_ids_run == ["t::a", "t::b"]

    def it_kills_on_a_setup_error_even_with_zero_calls(tmp_path, monkeypatch):
        """A fixture broken by the mutant is a test catching it."""
        result = _run(
            tmp_path,
            monkeypatch,
            "cls_setup",
            [_FakeReport("t::c", "setup", passed=False, failed=True)],
            pytest.ExitCode.TESTS_FAILED,
        )
        assert result.status == "killed"
        assert result.tests_run == 0
        assert result.killing_test == "t::c"

    def it_lists_failures_before_setup_errors(tmp_path, monkeypatch):
        result = _run(
            tmp_path,
            monkeypatch,
            "cls_order",
            [
                _FakeReport("t::e", "setup", passed=False, failed=True),
                _FakeReport("t::f", "call", passed=False, failed=True),
            ],
            pytest.ExitCode.TESTS_FAILED,
        )
        assert result.killing_test == "t::f"
        assert result.killing_tests == ["t::f", "t::e"]

    def it_errors_when_no_tests_ran(tmp_path, monkeypatch):
        result = _run(tmp_path, monkeypatch, "cls_none", [], pytest.ExitCode.OK)
        assert result.status == "error"
        assert result.killed is False
        assert result.error == "no tests ran"

    def it_errors_on_no_tests_collected(tmp_path, monkeypatch):
        result = _run(
            tmp_path, monkeypatch, "cls_nocoll", [], pytest.ExitCode.NO_TESTS_COLLECTED
        )
        assert result.status == "error"
        assert result.error == "pytest exited with NO_TESTS_COLLECTED"

    def it_errors_on_an_interrupted_run_even_after_passes(tmp_path, monkeypatch):
        result = _run(
            tmp_path,
            monkeypatch,
            "cls_intr",
            [_FakeReport("t::a", "call", passed=True, failed=False)],
            pytest.ExitCode.INTERRUPTED,
        )
        assert result.status == "error"
        assert result.error == "pytest exited with INTERRUPTED"

    def it_errors_when_the_runner_raises_system_exit_without_timeout(
        tmp_path, monkeypatch
    ):
        mutant, sources, files = _mutant(tmp_path, monkeypatch, "cls_sysexit")
        with patch("pytest_leela.runner.pytest.main", side_effect=SystemExit(3)):
            result = run_tests_for_mutant(
                mutant, sources, files, test_dir=str(tmp_path)
            )
        assert result.status == "error"
        assert result.error == "pytest crashed: SystemExit: 3"
        assert result.killing_tests == []

    def it_keeps_packages_the_inner_run_imported_from_a_venv_in_cwd(
        tmp_path, monkeypatch
    ):
        mutant, sources, files = _mutant(tmp_path, monkeypatch, "cls_venv_new")
        pkg_file = str(tmp_path / ".venv" / "lib" / "site-packages" / "fresh.py")

        def fake(args, plugins=None):
            mod = types.ModuleType("_fresh_venv_pkg")
            mod.__file__ = pkg_file
            sys.modules["_fresh_venv_pkg"] = mod
            return 0

        try:
            with patch("pytest_leela.runner.pytest.main", side_effect=fake):
                run_tests_for_mutant(mutant, sources, files, test_dir=str(tmp_path))
            assert "_fresh_venv_pkg" in sys.modules
        finally:
            sys.modules.pop("_fresh_venv_pkg", None)


class _FakeAppConfig:
    def __init__(self, name, models_module):
        self.name = name
        self.models_module = models_module


class _FakeApps:
    """Stand-in for ``django.apps.apps`` (the app registry)."""

    def __init__(self, ready, app_models):
        # app_models: {app name: models module name, or None}
        self.ready = ready
        self._configs = [
            _FakeAppConfig(app, types.ModuleType(models) if models else None)
            for app, models in app_models.items()
        ]

    def get_app_configs(self):
        return self._configs


def describe_django_registry_module_names():
    def it_is_empty_without_django(monkeypatch):
        monkeypatch.setattr("pytest_leela.runner._django_apps", None)
        assert _django_registry_module_names() == frozenset()

    def it_is_empty_before_django_is_set_up(monkeypatch):
        monkeypatch.setattr(
            "pytest_leela.runner._django_apps",
            _FakeApps(False, {"shop": "shop.models"}),
        )
        monkeypatch.setitem(sys.modules, "shop.models", types.ModuleType("x"))
        assert _django_registry_module_names() == frozenset()

    def it_lists_loaded_model_and_admin_modules_with_submodules(monkeypatch):
        monkeypatch.setattr(
            "pytest_leela.runner._django_apps",
            _FakeApps(
                True,
                {"shop": "shop.models", "pages": None, "blog": "blog.models"},
            ),
        )
        for name in (
            "shop.models",
            "shop.models.order",
            "shop.models_extra",
            "shop.admin",
            "shop.admin.inlines",
            "shop.admin_utils",
            "shop.views",
            "pages.admin",
            "blog.models",
        ):
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

        names = _django_registry_module_names()

        assert {
            "shop.models",
            "shop.models.order",
            "shop.admin",
            "shop.admin.inlines",
            "pages.admin",
            "blog.models",
        } <= names
        assert names.isdisjoint({"shop.models_extra", "shop.admin_utils", "shop.views"})

    def it_lists_only_modules_that_are_loaded(monkeypatch):
        monkeypatch.setattr(
            "pytest_leela.runner._django_apps",
            _FakeApps(True, {"_unloaded_app": "_unloaded_app.models"}),
        )
        assert _django_registry_module_names() == frozenset()

    def it_keeps_registry_modules_out_of_the_eviction_set(tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pytest_leela.runner._django_apps",
            _FakeApps(True, {"_shop": "_shop.models"}),
        )
        for name in ("_shop.models", "_shop.admin", "_shop.views"):
            mod = types.ModuleType(name)
            mod.__file__ = str(tmp_path / f"{name}.py")
            monkeypatch.setitem(sys.modules, name, mod)

        names = ProjectModuleScope(str(tmp_path)).module_names()

        assert "_shop.models" not in names
        assert "_shop.admin" not in names
        assert "_shop.views" in names


def describe_referenced_closure():
    def _install(monkeypatch, name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    def it_pins_modules_referenced_through_classes_and_module_objects(monkeypatch):
        """models -> (class from) mixins -> (module object) helpers."""
        helpers = _install(monkeypatch, "_rc_helpers")
        mixin = type("Mixin", (), {"__module__": "_rc_mixins"})
        _install(monkeypatch, "_rc_mixins", helpers=helpers)
        _install(monkeypatch, "_rc_models", Mixin=mixin)
        _install(monkeypatch, "_rc_views")

        pinned = _referenced_closure(
            frozenset({"_rc_models"}),
            {"_rc_mixins", "_rc_helpers", "_rc_views"},
        )

        assert pinned == {"_rc_models", "_rc_mixins", "_rc_helpers"}

    def it_ignores_references_outside_the_candidates(monkeypatch):
        _install(monkeypatch, "_rc_only_root", path=os.path, ref=os.path.join)

        pinned = _referenced_closure(frozenset({"_rc_only_root"}), {"_rc_other"})

        assert pinned == {"_rc_only_root"}

    def it_terminates_on_reference_cycles(monkeypatch):
        a = _install(monkeypatch, "_rc_cycle_a")
        b = _install(monkeypatch, "_rc_cycle_b", a=a)
        a.b = b

        pinned = _referenced_closure(frozenset({"_rc_cycle_a"}), {"_rc_cycle_b"})

        assert pinned == {"_rc_cycle_a", "_rc_cycle_b"}

    def it_keeps_a_registry_modules_dependency_out_of_the_eviction_set(
        tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "pytest_leela.runner._django_apps",
            _FakeApps(True, {"_dep": "_dep.models"}),
        )
        mixin = type("Mixin", (), {"__module__": "_dep.mixins"})
        for name, attrs in (
            ("_dep.models", {"Mixin": mixin}),
            ("_dep.mixins", {}),
            ("_dep.views", {}),
        ):
            mod = _install(monkeypatch, name, **attrs)
            mod.__file__ = str(tmp_path / f"{name}.py")

        names = ProjectModuleScope(str(tmp_path)).module_names()

        assert "_dep.mixins" not in names
        assert "_dep.views" in names


def describe_run_tests_for_mutant_real_inner_outcomes():
    """The kill / error branches, each driven by a real inner pytest run."""

    def _mutant(tmp_path, monkeypatch, name):
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / f"{name}.py"
        target.write_text(source)
        points = find_mutation_points(source, str(target), name)
        point = next(p for p in points if p.node_type == "BinOp")
        return (
            Mutant(point=point, replacement_op="Sub", mutant_id=0),
            {name: source},
            {name: str(target)},
        )

    def it_errors_when_every_test_is_skipped(tmp_path, monkeypatch):
        mutant, sources, files = _mutant(tmp_path, monkeypatch, "real_skip")
        test_dir = tmp_path / "real_skip_tests"
        test_dir.mkdir()
        (test_dir / "test_skip.py").write_text(
            "import pytest\n\n"
            "@pytest.mark.skip(reason='off')\n"
            "def test_off():\n"
            "    pass\n"
        )

        result = run_tests_for_mutant(mutant, sources, files, test_dir=str(test_dir))

        assert result.status == "error"
        assert result.error == "no tests ran"

    def it_errors_when_nothing_is_collected(tmp_path, monkeypatch):
        mutant, sources, files = _mutant(tmp_path, monkeypatch, "real_empty")
        test_dir = tmp_path / "real_empty_tests"
        test_dir.mkdir()

        result = run_tests_for_mutant(mutant, sources, files, test_dir=str(test_dir))

        assert result.status == "error"
        assert result.error == "pytest exited with NO_TESTS_COLLECTED"

    def it_errors_when_a_test_id_does_not_exist(tmp_path, monkeypatch):
        mutant, sources, files = _mutant(tmp_path, monkeypatch, "real_nope")

        result = run_tests_for_mutant(mutant, sources, files, test_ids=["nope.py::x"])

        assert result.status == "error"
        assert result.error == "pytest exited with USAGE_ERROR"

    def it_keeps_a_green_run_survived_when_an_import_error_was_caught(
        tmp_path, monkeypatch
    ):
        """Regression: the mutated module raised on import, but the test caught
        it and the run completed green, so nothing detected the mutant."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source = "DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n"
        (tmp_path / "caught_calc.py").write_text(source)
        test_dir = tmp_path / "caught_tests"
        test_dir.mkdir()
        (test_dir / "test_caught.py").write_text(
            "try:\n"
            "    import caught_calc\n"
            "except Exception:\n"
            "    caught_calc = None\n\n\n"
            "def test_ratio_if_available():\n"
            "    if caught_calc is None:\n"
            "        return\n"
            "    assert caught_calc.RATIO in (5, 10, 5.0, 10.0, 0, 20)\n"
        )
        points = find_mutation_points(
            source, str(tmp_path / "caught_calc.py"), "caught_calc"
        )
        point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)

        result = run_tests_for_mutant(
            mutant,
            {"caught_calc": source},
            {"caught_calc": str(tmp_path / "caught_calc.py")},
            test_dir=str(test_dir),
        )

        assert result.status == "survived"
        assert result.tests_run == 1
        assert result.killing_tests == []

    def _import_skip_run(tmp_path, monkeypatch, name, test_source, by_id=False):
        """Run Add -> Sub on ``DIVISOR = 1 + 1``, whose import then raises.

        *by_id* passes the test's node id, as coverage-based selection does.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source = "DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n"
        (tmp_path / f"{name}.py").write_text(source)
        test_dir = tmp_path / f"{name}_tests"
        test_dir.mkdir()
        (test_dir / f"test_{name}.py").write_text(test_source)
        points = find_mutation_points(source, str(tmp_path / f"{name}.py"), name)
        point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
        if by_id:
            return run_tests_for_mutant(
                mutant,
                {name: source},
                {name: str(tmp_path / f"{name}.py")},
                test_ids=[f"{name}_tests/test_{name}.py::test_ratio"],
            )
        return run_tests_for_mutant(
            mutant,
            {name: source},
            {name: str(tmp_path / f"{name}.py")},
            test_dir=str(test_dir),
        )

    def it_errors_when_a_module_level_skip_hides_an_import_error(tmp_path, monkeypatch):
        """Regression: the import error turns into a module-level skip, pytest
        exits NO_TESTS_COLLECTED, a green outcome: no kill, nothing tested."""
        result = _import_skip_run(
            tmp_path,
            monkeypatch,
            "modskip_calc",
            "import pytest\n\n"
            "try:\n"
            "    import modskip_calc\n"
            "except Exception:\n"
            "    pytest.skip('unavailable', allow_module_level=True)\n\n\n"
            "def test_ratio():\n"
            "    assert modskip_calc.RATIO == 5\n",
        )

        assert result.status == "error"
        assert result.error == "pytest exited with NO_TESTS_COLLECTED"
        assert result.killing_tests == []
        assert result.tests_run == 0

    def it_errors_when_a_module_level_skip_hides_a_selected_test(tmp_path, monkeypatch):
        """Regression: with the test selected by node id, the skipped module
        leaves the id unmatched and pytest exits USAGE_ERROR after the session
        started.  Still green: no kill."""
        result = _import_skip_run(
            tmp_path,
            monkeypatch,
            "idskip_calc",
            "import pytest\n\n"
            "try:\n"
            "    import idskip_calc\n"
            "except Exception:\n"
            "    pytest.skip('unavailable', allow_module_level=True)\n\n\n"
            "def test_ratio():\n"
            "    assert idskip_calc.RATIO == 5\n",
            by_id=True,
        )

        assert result.status == "error"
        assert result.error == "pytest exited with USAGE_ERROR"
        assert result.killing_tests == []
        assert result.tests_run == 0

    def it_errors_when_a_skip_marker_hides_an_import_error(tmp_path, monkeypatch):
        """Regression: every test skips at setup, pytest exits OK with zero
        tests run, a green outcome: no kill, nothing tested."""
        result = _import_skip_run(
            tmp_path,
            monkeypatch,
            "markskip_calc",
            "import pytest\n\n"
            "try:\n"
            "    import markskip_calc\n"
            "except Exception:\n"
            "    markskip_calc = None\n\n\n"
            "@pytest.mark.skipif(markskip_calc is None, reason='unavailable')\n"
            "def test_ratio():\n"
            "    assert markskip_calc.RATIO == 5\n",
        )

        assert result.status == "error"
        assert result.error == "no tests ran"
        assert result.killing_tests == []
        assert result.tests_run == 0

    def it_kills_on_a_real_setup_error(tmp_path, monkeypatch):
        mutant, sources, files = _mutant(tmp_path, monkeypatch, "real_setup")
        test_dir = tmp_path / "real_setup_tests"
        test_dir.mkdir()
        (test_dir / "test_setup.py").write_text(
            "import pytest\n"
            "from real_setup import add\n\n"
            "@pytest.fixture\n"
            "def three():\n"
            "    assert add(1, 2) == 3\n"
            "    return 3\n\n"
            "def test_uses_fixture(three):\n"
            "    assert three\n"
        )

        result = run_tests_for_mutant(mutant, sources, files, test_dir=str(test_dir))

        assert result.status == "killed"
        assert result.tests_run == 0
        assert result.killing_test == (
            "real_setup_tests/test_setup.py::test_uses_fixture"
        )


def describe_scope_is_passed_down():
    def it_uses_the_given_scope_instead_of_building_one(tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source = "def add(a, b):\n    return a + b\n"
        target = tmp_path / "scope_down.py"
        target.write_text(source)
        points = find_mutation_points(source, str(target), "scope_down")
        mutant = Mutant(point=points[0], replacement_op="Sub", mutant_id=0)
        scope = ProjectModuleScope(str(tmp_path))

        with (
            patch("pytest_leela.runner.ProjectModuleScope", side_effect=AssertionError),
            patch("pytest_leela.runner.pytest.main", return_value=0),
        ):
            result = run_tests_for_mutant(
                mutant,
                {"scope_down": source},
                {"scope_down": str(target)},
                test_dir=str(tmp_path),
                scope=scope,
            )

        assert result.status == "error"


def describe_InnerSession():
    def it_has_no_import_errors_without_a_finder():
        session = InnerSession(None, None, None, ProjectModuleScope("/p"))
        assert session.import_errors == []

    def it_reports_the_finders_import_errors():
        finder = MutatingFinder({"m": "x = 1"}, _probe_mutant())
        finder.import_errors.append("m: ValueError: boom")
        session = InnerSession(finder, None, None, ProjectModuleScope("/p"))
        assert session.import_errors == ["m: ValueError: boom"]

    def it_stops_at_the_first_failure_by_default():
        session = InnerSession(None, ["t::a"], None, ProjectModuleScope("/p"))
        assert "-x" in session._args()

    def it_runs_everything_when_exitfirst_is_off():
        session = InnerSession(
            None, ["t::a"], None, ProjectModuleScope("/p"), exitfirst=False
        )
        assert "-x" not in session._args()
        assert session._args()[-1] == "t::a"


def _probe_mutant():
    point = find_mutation_points("x = 1 + 1\n", "/p/m.py", "m")[0]
    return Mutant(point=point, replacement_op="Sub", mutant_id=0)


class _FakeAdminSite:
    def __init__(self, admins):
        self._registry = {object(): admin for admin in admins}


def describe_django_registry_admin_site_walk():
    def it_pins_the_module_of_every_registered_model_admin(monkeypatch):
        monkeypatch.setattr(
            "pytest_leela.runner._django_apps", _FakeApps(True, {"shop": None})
        )
        admin_cls = type("ItemAdmin", (), {"__module__": "_shop.site_admin"})
        other_cls = type("PageAdmin", (), {"__module__": "_pages.custom"})
        sites = types.ModuleType("django.contrib.admin.sites")
        sites.all_sites = [_FakeAdminSite([admin_cls()]), _FakeAdminSite([other_cls()])]
        monkeypatch.setitem(sys.modules, "django.contrib.admin.sites", sites)
        for name in ("_shop.site_admin", "_pages.custom", "_shop.views"):
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

        names = _django_registry_module_names()

        assert {"_shop.site_admin", "_pages.custom"} <= names
        assert "_shop.views" not in names

    def it_skips_the_admin_walk_when_admin_was_never_imported(monkeypatch):
        monkeypatch.setattr(
            "pytest_leela.runner._django_apps", _FakeApps(True, {"_noadmin": None})
        )
        monkeypatch.delitem(sys.modules, "django.contrib.admin.sites", raising=False)
        monkeypatch.setitem(
            sys.modules, "_noadmin.admin", types.ModuleType("_noadmin.admin")
        )

        assert _django_registry_module_names() == frozenset({"_noadmin.admin"})


def describe_import_error_kill_rule():
    """The mutated module raised on import: a kill only if pytest reported it
    as a failure, for every exit code an inner run can return."""

    _EC = pytest.ExitCode

    @pytest.mark.parametrize(
        ("exit_code", "session_started", "collection_failed", "status", "error"),
        [
            (_EC.OK, True, False, "error", "no tests ran"),
            (_EC.TESTS_FAILED, True, True, "killed", None),
            (_EC.INTERRUPTED, True, True, "killed", None),
            (_EC.INTERRUPTED, True, False, "error", "pytest exited with INTERRUPTED"),
            (
                _EC.INTERNAL_ERROR,
                True,
                False,
                "error",
                "pytest exited with INTERNAL_ERROR",
            ),
            (_EC.USAGE_ERROR, False, False, "killed", None),
            (_EC.USAGE_ERROR, True, True, "killed", None),
            (_EC.USAGE_ERROR, True, False, "error", "pytest exited with USAGE_ERROR"),
            (
                _EC.NO_TESTS_COLLECTED,
                True,
                False,
                "error",
                "pytest exited with NO_TESTS_COLLECTED",
            ),
            (99, True, False, "error", "pytest exited with exit code 99"),
        ],
    )
    def it_applies_the_rule(
        tmp_path,
        monkeypatch,
        exit_code,
        session_started,
        collection_failed,
        status,
        error,
    ):
        name = f"rule_calc_{int(exit_code)}_{session_started}_{collection_failed}"
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source = "DIVISOR = 1 + 1\nRATIO = 10 // DIVISOR\n"
        (tmp_path / f"{name}.py").write_text(source)
        points = find_mutation_points(source, str(tmp_path / f"{name}.py"), name)
        point = next(
            p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)

        def fake_main(args, plugins=None):
            collector = plugins[0]
            if session_started:
                collector.pytest_sessionstart(None)
            with pytest.raises(ZeroDivisionError):
                importlib.import_module(name)
            if collection_failed:
                collector.pytest_collectreport(
                    _FakeCollectReport("test_x.py", failed=True, longreprtext="E")
                )
            return exit_code

        with patch("pytest_leela.runner.pytest.main", side_effect=fake_main):
            result = run_tests_for_mutant(
                mutant,
                {name: source},
                {name: str(tmp_path / f"{name}.py")},
                test_dir=str(tmp_path),
            )

        assert result.status == status
        assert result.error == error
        if status == "killed":
            expected = ["test_x.py"] if collection_failed else []
            assert result.killing_tests[: len(expected)] == expected
            if not collection_failed:
                [killing] = result.killing_tests
                assert killing.startswith(f"<import of {name}: ZeroDivisionError:")
