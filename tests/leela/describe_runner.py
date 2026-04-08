"""Tests for pytest_leela.runner — test execution against mutants."""

import signal
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.import_hook import MutatingFinder
from pytest_leela.models import Mutant, MutantResult
from pytest_leela.runner import (
    _KEEP_PREFIXES,
    _clear_framework_caches,
    _clear_user_modules,
    _clear_user_modules_fast,
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


def describe_run_tests_for_mutant_timeout():
    """Tests for the SIGALRM-based timeout in run_tests_for_mutant()."""

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

    def it_returns_killed_when_signal_handler_fires(tmp_path, monkeypatch):
        """When SIGALRM handler fires, result.killing_test must be '<timeout>'.

        The old test mocked pytest.main to raise SystemExit but timed_out
        stayed False, so killing_test was '<crashed>'. This test directly
        invokes the _timeout_handler closure to actually set timed_out=True.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        # Track whether the handler was called and timed_out was set
        timed_out_state = {"was_set": False}

        def patched_timeout_handler(_signum: int, _frame: object) -> None:
            timed_out_state["was_set"] = True
            raise SystemExit("leela: mutant timeout")

        # Replace the _timeout_handler closure with our version that
        # sets timed_out=True (by patching signal.signal so our handler
        # gets registered instead of the real one)
        registered_handler_ref = {"handler": None}

        def fake_signal(signum: int, handler: object) -> object:
            if signum == signal.SIGALRM and handler != signal.SIG_DFL:
                registered_handler_ref["handler"] = handler
            return signal.SIG_DFL

        with (
            patch("pytest_leela.runner.signal.alarm", lambda s: 0),
            patch("pytest_leela.runner.signal.signal", fake_signal),
            patch("pytest_leela.runner.pytest.main", side_effect=SystemExit("timeout")),
        ):
            # Run once to register the handler, but pytest.main will raise
            # and the except block will fire before we can call the handler.
            # So instead, let's directly invoke the registered handler
            # by simulating it was called.
            result = run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        # Patch signal.signal so our handler replaces the real one.
        # When run_tests_for_mutant registers _timeout_handler via
        # signal.signal(signal.SIGALRM, _timeout_handler), our fake returns
        # the real handler so we can call it. When the finally block tries to
        # restore the old handler, we return SIG_DFL.
        handler_closure = {"func": None}

        def fake_signal(signum: int, handler: object) -> object:
            if signum == signal.SIGALRM:
                if handler == signal.SIG_DFL:
                    # Restore call in finally
                    return signal.SIG_DFL
                # Registration call — capture the closure
                handler_closure["func"] = handler
                return signal.SIG_DFL
            return signal.SIG_DFL

        def fake_alarm(seconds: int) -> int:
            return 0

        # Simulate pytest.main raising from the timeout handler:
        # we call the handler (which sets timed_out=True and raises
        # SystemExit). pytest.main catches it internally and returns 0
        # (pytest's own exception handling). This is the "py catches the
        # signal's SystemExit" path → post-run check sees timed_out=True.
        def pytest_main_that_simulates_timeout(*args, **kwargs):
            if handler_closure["func"] is not None:
                handler_closure["func"](signal.SIGALRM, None)
            return 0  # pytest caught the SystemExit

        with (
            patch("pytest_leela.runner.signal.alarm", fake_alarm),
            patch("pytest_leela.runner.signal.signal", fake_signal),
            patch(
                "pytest_leela.runner.pytest.main", pytest_main_that_simulates_timeout
            ),
        ):
            result = run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        # timed_out was set to True by the handler → killing_test == "<timeout>"
        assert result.killed is True
        assert result.killing_test == "<timeout>"

    def it_returns_killed_on_general_exception_from_pytest_main(tmp_path, monkeypatch):
        """Any exception from pytest.main causes killed result."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        with patch("pytest_leela.runner.pytest.main", side_effect=RuntimeError("boom")):
            result = run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        assert result.killed is True
        assert result.killing_test == "<crashed>"

    def it_invokes_timeout_handler_to_set_timed_out(tmp_path, monkeypatch):
        """Verify timed_out=True causes killing_test to be '<timeout>'.

        The old test mocked pytest.main to raise SystemExit but timed_out
        stayed False, so killing_test was '<crashed>'. This test captures
        the registered _timeout_handler and invokes it immediately within
        fake_signal — setting timed_out=True in the runner's closure.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        source, mutant = _make_mutant_fixture(tmp_path)

        def fake_signal(signum: int, handler: object) -> object:
            if signum == signal.SIGALRM and handler != signal.SIG_DFL:
                # Invoke the handler immediately (simulates real SIGALRM firing).
                # This sets timed_out=True in the runner's closure AND raises
                # SystemExit. We catch the exit so execution continues.
                try:
                    handler(signal.SIGALRM, None)
                except SystemExit:
                    pass
            return signal.SIG_DFL

        with (
            patch("pytest_leela.runner.signal.alarm", lambda s: 0),
            patch("pytest_leela.runner.signal.signal", fake_signal),
            patch("pytest_leela.runner.pytest.main", return_value=0),
        ):
            result = run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        # timed_out was set by invoking the handler → killing_test == "<timeout>"
        assert result.killed is True
        assert result.killing_test == "<timeout>"


def _make_mutant_fixture_for_timeout(tmp_path):
    """Shared fixture for timeout-related tests."""
    source = "def add(a, b):\n    return a + b\n"
    target = tmp_path / "timeout_target.py"
    target.write_text(source)
    points = find_mutation_points(source, str(target), "timeout_target")
    binop_point = next(
        p for p in points if p.node_type == "BinOp" and p.original_op == "Add"
    )
    mutant = Mutant(point=binop_point, replacement_op="Sub", mutant_id=0)
    return source, mutant


def describe_timeout_computation():
    """Tests for the timeout computation formula in run_tests_for_mutant()."""

    def it_uses_max_5_second_floor():
        """Timeout must be at least 5.0 seconds regardless of test times."""
        import inspect

        from pytest_leela.runner import run_tests_for_mutant

        src = inspect.getsource(run_tests_for_mutant)
        assert "max(2 * total_expected + 1.0, 5.0)" in src

    def it_ceils_timeout_value(tmp_path, monkeypatch):
        """signal.alarm() receives a ceil'd value to avoid premature firing."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant_fixture_for_timeout(tmp_path)

        call_args: list[int] = []

        def fake_alarm(seconds: int) -> int:
            call_args.append(seconds)
            return 0

        with (
            patch("pytest_leela.runner.signal.alarm", fake_alarm),
            patch("pytest_leela.runner.signal.signal", return_value=signal.SIG_DFL),
            patch("pytest_leela.runner.pytest.main", return_value=0),
        ):
            # test_times sum = 2.1, 2*2.1+1 = 5.2 → ceil = 6 (int would be 5)
            # This distinction proves math.ceil, not int(), is used.
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 2.1},
            )

        # Two calls: signal.alarm(ceil) to set, signal.alarm(0) to cancel in finally
        assert len(call_args) == 2
        assert call_args[0] == 6  # math.ceil(5.2) == 6, not int(5.2) == 5
        assert call_args[1] == 0  # cancellation in finally block

    def it_cancels_alarm_in_except_block(tmp_path, monkeypatch):
        """signal.alarm(0) is called in the except block to cancel pending alarm."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant_fixture_for_timeout(tmp_path)

        alarm_calls: list[int] = []

        def fake_alarm(seconds: int) -> int:
            alarm_calls.append(seconds)
            return 0

        with (
            patch("pytest_leela.runner.signal.alarm", fake_alarm),
            patch("pytest_leela.runner.signal.signal", return_value=signal.SIG_DFL),
            patch("pytest_leela.runner.pytest.main", side_effect=RuntimeError("boom")),
        ):
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        # First call: set alarm; second call (in except): cancel it with alarm(0)
        assert 0 in alarm_calls

    def it_restores_old_handler_in_finally(tmp_path, monkeypatch):
        """Old signal handler is restored in the finally block."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant_fixture_for_timeout(tmp_path)

        restore_calls: list[int] = []

        def fake_signal(signum: int, handler: object) -> object:
            if signum == signal.SIGALRM:
                restore_calls.append(signum)
            return signal.SIG_DFL

        with (
            patch("pytest_leela.runner.signal.alarm", lambda s: 0),
            patch("pytest_leela.runner.signal.signal", fake_signal),
            patch("pytest_leela.runner.pytest.main", return_value=0),
        ):
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times={"test_a": 1.0},
            )

        # Two calls: register handler and restore old handler
        assert len(restore_calls) == 2

    def it_has_post_run_timed_out_check(tmp_path, monkeypatch):
        """Post-run check for timed_out flag sets killing_test to <timeout>."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant_fixture_for_timeout(tmp_path)

        import inspect

        from pytest_leela.runner import run_tests_for_mutant

        src = inspect.getsource(run_tests_for_mutant)
        # The post-run check exists and uses timed_out to set killing_test
        assert "if timed_out:" in src
        # killing_test is set to "<timeout>" in the post-run path
        assert 'killing_test="<timeout>"' in src


def describe_timeout_guard_conditions():
    """Tests for the guard conditions around timeout setup."""

    def it_skips_timeout_when_test_times_is_none(tmp_path, monkeypatch):
        """No timeout is set when test_times is None."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant_fixture_for_timeout(tmp_path)

        with patch("pytest_leela.runner.signal.alarm") as mock_alarm:
            # test_times=None → no alarm should be set
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a"],
                test_times=None,
            )

        mock_alarm.assert_not_called()

    def it_skips_timeout_when_test_ids_is_empty(tmp_path, monkeypatch):
        """No timeout is set when test_ids is an empty list."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant_fixture_for_timeout(tmp_path)

        with patch("pytest_leela.runner.signal.alarm") as mock_alarm:
            # test_ids=[] (empty) → no alarm should be set
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=[],
                test_times={"test_a": 1.0},
            )

        mock_alarm.assert_not_called()

    def it_uses_default_1_0_for_unknown_test_ids(tmp_path, monkeypatch):
        """test_times.get(t, 1.0) falls back to 1.0 for unknown test IDs."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant_fixture_for_timeout(tmp_path)

        alarm_values: list[int] = []

        def fake_alarm(seconds: int) -> int:
            alarm_values.append(seconds)
            return 0

        with (
            patch("pytest_leela.runner.signal.alarm", fake_alarm),
            patch("pytest_leela.runner.signal.signal", return_value=signal.SIG_DFL),
            patch("pytest_leela.runner.pytest.main", return_value=0),
        ):
            # test_ids contains "test_unknown" which is not in test_times
            # Should fall back to 1.0 for unknown test
            # timeout = max(2 * 1.0 + 1.0, 5.0) = 5.0 → ceil = 5
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_unknown"],
                test_times={},
            )

        # Two calls: set alarm (ceil) + cancel in finally
        assert len(alarm_values) == 2
        assert alarm_values[0] == 5  # math.ceil(5.0) == 5
        assert alarm_values[1] == 0  # cancellation in finally

    def it_calculates_timeout_from_multiple_test_times(tmp_path, monkeypatch):
        """Timeout is computed from the sum of all test_times values."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        source, mutant = _make_mutant_fixture_for_timeout(tmp_path)

        alarm_values: list[int] = []

        def fake_alarm(seconds: int) -> int:
            alarm_values.append(seconds)
            return 0

        with (
            patch("pytest_leela.runner.signal.alarm", fake_alarm),
            patch("pytest_leela.runner.signal.signal", return_value=signal.SIG_DFL),
            patch("pytest_leela.runner.pytest.main", return_value=0),
        ):
            # 3 tests with known times
            # total_expected = 2.0 + 3.0 + 0.5 = 5.5
            # timeout = max(2 * 5.5 + 1.0, 5.0) = 12.0 → ceil = 12
            run_tests_for_mutant(
                mutant,
                {"timeout_target": source},
                {"timeout_target": str(tmp_path / "timeout_target.py")},
                test_ids=["test_a", "test_b", "test_c"],
                test_times={"test_a": 2.0, "test_b": 3.0, "test_c": 0.5},
            )

        # Two calls: set alarm (ceil) + cancel in finally
        assert len(alarm_values) == 2
        assert alarm_values[0] == 12
        assert alarm_values[1] == 0  # cancellation in finally


def describe_timeout_signal_handler():
    """Tests for the _timeout_handler signal handler behavior."""

    def it_calls_timed_out_and_raises_system_exit(tmp_path, monkeypatch):
        """Signal handler sets timed_out=True and raises SystemExit."""
        import inspect

        from pytest_leela.runner import run_tests_for_mutant

        src = inspect.getsource(run_tests_for_mutant)
        # Verify the handler sets timed_out and raises SystemExit
        assert "timed_out = True" in src
        assert 'raise SystemExit("leela: mutant timeout")' in src

    def it_has_both_except_and_finally_alarm_cancellation(tmp_path, monkeypatch):
        """signal.alarm(0) appears in both except and finally blocks."""
        import inspect

        from pytest_leela.runner import run_tests_for_mutant

        src = inspect.getsource(run_tests_for_mutant)
        # Count occurrences of signal.alarm(0)
        count = src.count("signal.alarm(0)")
        # Should be in except block AND finally block = 2 times
        assert count == 2


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
