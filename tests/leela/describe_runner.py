"""Tests for pytest_leela.runner — test execution against mutants."""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.import_hook import MutatingFinder
from pytest_leela.models import Mutant, MutantResult
from pytest_leela.runner import (
    _ResultCollector,
    _clear_framework_caches,
    _clear_user_modules,
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
            p for p in points
            if p.node_type == "Compare" and p.original_op == "Gt"
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
