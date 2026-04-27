"""Tests for pytest_leela.runner — test execution against mutants."""

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
    _ResultCollector,
    _clear_framework_caches,
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

    def it_returns_killed_result_when_pytest_main_crashes(tmp_path, monkeypatch):
        """Kills lines 165-166: elapsed timing and return in crash handler.

        Line 165: ``- → +/*`` would make elapsed = monotonic() + start (huge).
        Line 166: ``return expr → None`` would return None instead of MutantResult.
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

        # Kills line 166: return expr → None
        assert result is not None
        assert isinstance(result, MutantResult)
        assert result.killed is True
        assert result.killing_test == "<crashed>"
        # Kills line 165: - → + (would produce value >> 60)
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

        assert result.killed is True
        assert result.test_ids_run == []
        assert result.killing_tests == ["<crashed>"]

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
            # Simulate: timeout fires, but pytest swallows the SystemExit
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
        # Elapsed should be reasonable (not a huge value from wrong arithmetic)
        assert 0 <= result.time_seconds < 60

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
