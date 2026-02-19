"""Tests for pytest_leela.engine."""

import os
import sys
import tempfile
import types
from unittest.mock import MagicMock, patch

import pytest

from pytest_leela.engine import Engine, _clean_process_state, _module_name_from_path
from pytest_leela.import_hook import MutatingFinder
from pytest_leela.models import CoverageMap, Mutant, MutantResult, MutationPoint, RunResult
from pytest_leela.resources import ResourceLimits


def describe_engine():
    def it_is_importable():
        from pytest_leela.engine import Engine

        assert Engine is not None


def _make_dummy_finder() -> MutatingFinder:
    """Create a MutatingFinder with minimal dummy data."""
    point = MutationPoint(
        file_path="dummy.py",
        module_name="dummy",
        lineno=1,
        col_offset=0,
        node_type="BinOp",
        original_op="Add",
        inferred_type=None,
    )
    mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
    return MutatingFinder(target_modules={"dummy": "x = 1"}, mutant=mutant)


def describe_clean_process_state():
    def it_removes_stale_mutating_finders_from_meta_path():
        """Kills engine.py line 67: not isinstance(f, MutatingFinder) → isinstance(...)."""
        stale_finder = _make_dummy_finder()
        original_meta_path = sys.meta_path[:]
        try:
            sys.meta_path.insert(0, stale_finder)
            assert stale_finder in sys.meta_path

            _clean_process_state()

            assert stale_finder not in sys.meta_path
        finally:
            sys.meta_path[:] = original_meta_path

    def it_preserves_non_mutating_finders_in_meta_path():
        original_meta_path = sys.meta_path[:]
        original_non_mutating = [
            f for f in sys.meta_path if not isinstance(f, MutatingFinder)
        ]
        stale_finder = _make_dummy_finder()
        try:
            sys.meta_path.insert(0, stale_finder)

            _clean_process_state()

            remaining = [
                f for f in sys.meta_path if not isinstance(f, MutatingFinder)
            ]
            assert remaining == original_non_mutating
        finally:
            sys.meta_path[:] = original_meta_path

    def it_removes_modules_loaded_from_temp_directories():
        tmp_dir = tempfile.gettempdir()
        fake_mod = types.ModuleType("_stale_tmp_fixture_mod")
        fake_mod.__file__ = os.path.join(tmp_dir, "stale_target.py")
        try:
            sys.modules["_stale_tmp_fixture_mod"] = fake_mod
            assert "_stale_tmp_fixture_mod" in sys.modules

            _clean_process_state()

            assert "_stale_tmp_fixture_mod" not in sys.modules
        finally:
            sys.modules.pop("_stale_tmp_fixture_mod", None)

    def it_keeps_non_temp_modules():
        original_keys = set(sys.modules.keys())

        _clean_process_state()

        # All non-temp modules should still be present
        for key in original_keys:
            mod = sys.modules.get(key)
            if mod is None:
                continue
            mod_file = getattr(mod, "__file__", None)
            if mod_file is None:
                assert key in sys.modules
            elif not mod_file.startswith(tempfile.gettempdir() + os.sep):
                assert key in sys.modules


def describe_module_name_from_path():
    def it_converts_nested_path_to_dotted_name(tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        file_path = str(tmp_path / "src" / "foo" / "bar.py")
        result = _module_name_from_path(file_path)
        assert result == "src.foo.bar"

    def it_converts_single_file_to_module_name(tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        file_path = str(tmp_path / "mymodule.py")
        result = _module_name_from_path(file_path)
        assert result == "mymodule"

    def it_preserves_path_without_py_suffix(tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        file_path = str(tmp_path / "pkg" / "data")
        result = _module_name_from_path(file_path)
        assert result == "pkg.data"

    def it_prefers_longest_sys_path_match(tmp_path, monkeypatch):
        """When multiple sys.path entries match, pick the longest (most specific)."""
        src_dir = tmp_path / "src"
        pkg_dir = src_dir / "mypkg"
        pkg_dir.mkdir(parents=True)
        target = pkg_dir / "mod.py"
        target.write_text("x = 1\n")

        # Both tmp_path and src_dir are on sys.path
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(src_dir))
        monkeypatch.syspath_prepend(str(tmp_path))

        result = _module_name_from_path(str(target))
        # src_dir is longer and more specific, so module is mypkg.mod
        assert result == "mypkg.mod"

    def it_falls_back_to_cwd_when_no_sys_path_match(tmp_path, monkeypatch):
        """When file is not under any sys.path entry, use CWD-relative."""
        monkeypatch.chdir(tmp_path)
        # Clear sys.path of anything that matches
        original_path = sys.path.copy()
        monkeypatch.setattr("sys.path", ["/nonexistent"])

        file_path = str(tmp_path / "standalone.py")
        result = _module_name_from_path(file_path)
        assert result == "standalone"


def describe_Engine_run():
    def it_finds_and_kills_mutants_in_a_tiny_module(tmp_path, monkeypatch):
        target = tmp_path / "eng_target.py"
        target.write_text("def add(a, b):\n    return a + b\n")

        test_dir = tmp_path / "eng_tests"
        test_dir.mkdir()
        (test_dir / "test_eng_target.py").write_text(
            "from eng_target import add\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n\n"
            "def test_add_zero():\n"
            "    assert add(0, 0) == 0\n"
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        engine = Engine(use_types=False, use_coverage=False)
        result = engine.run([str(target)], str(test_dir))

        assert isinstance(result, RunResult)
        # BinOp Add -> [Sub, Mult] and Return expr -> [None] = 3 mutants
        assert result.total_mutants >= 2
        assert result.mutants_tested >= 2
        assert result.killed >= 1
        assert result.wall_time_seconds > 0

    def it_reports_wall_time_as_positive(tmp_path, monkeypatch):
        target = tmp_path / "eng_t2.py"
        target.write_text("def noop():\n    return 1\n")
        test_dir = tmp_path / "eng_t2_tests"
        test_dir.mkdir()
        (test_dir / "test_noop.py").write_text(
            "from eng_t2 import noop\n\n"
            "def test_noop():\n"
            "    assert noop() == 1\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        engine = Engine(use_types=False, use_coverage=False)
        result = engine.run([str(target)], str(test_dir))
        # wall_time = time.monotonic() - start; start < end, so positive
        assert result.wall_time_seconds > 0

    def it_counts_total_mutants_including_pruned(tmp_path, monkeypatch):
        """total_mutants = len(all_mutants) + total_pruned."""
        target = tmp_path / "eng_t3.py"
        target.write_text("def add(a, b):\n    return a + b\n")
        test_dir = tmp_path / "eng_t3_tests"
        test_dir.mkdir()
        (test_dir / "test_t3.py").write_text(
            "from eng_t3 import add\ndef test_add():\n    assert add(1, 2) == 3\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        engine = Engine(use_types=False, use_coverage=False)
        result = engine.run([str(target)], str(test_dir))
        # total_mutants should be at least mutants_tested + mutants_pruned
        assert result.total_mutants == result.mutants_tested + result.mutants_pruned

    def it_adds_pruned_count_to_total_mutants(tmp_path, monkeypatch):
        """total_mutants = len(all_mutants) + total_pruned (not minus).

        Kills line 108: + → -
        """
        target = tmp_path / "t_pruned.py"
        target.write_text("def add(a, b):\n    return a + b\n")
        test_dir = tmp_path / "t_pruned_tests"
        test_dir.mkdir()
        (test_dir / "test_pruned.py").write_text(
            "from t_pruned import add\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        # Mock count_pruned to return a non-zero value so + vs - matters
        with patch("pytest_leela.engine.count_pruned", return_value=5):
            engine = Engine(use_types=False, use_coverage=False)
            result = engine.run([str(target)], str(test_dir))

        assert result.mutants_pruned == 5
        assert result.total_mutants == result.mutants_tested + 5

    def it_tests_only_mutants_on_diff_changed_lines(tmp_path, monkeypatch):
        """diff_base filters mutants to only changed lines.

        Kills line 116: and → or, in → not in
        Kills line 117: in → not in
        """
        target = tmp_path / "t_diff.py"
        target.write_text(
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def sub(a, b):\n"
            "    return a - b\n"
        )
        abs_target = os.path.abspath(str(target))
        test_dir = tmp_path / "t_diff_tests"
        test_dir.mkdir()
        (test_dir / "test_diff.py").write_text(
            "from t_diff import add, sub\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n\n"
            "def test_sub():\n"
            "    assert sub(3, 1) == 2\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        engine = Engine(use_types=False, use_coverage=False)

        # Baseline: all mutants tested (no diff filter)
        result_all = engine.run([str(target)], str(test_dir))
        all_lines = {r.mutant.point.lineno for r in result_all.results}
        assert len(all_lines) > 1, "need mutants on multiple lines"

        # With diff_base: only line 2 changed
        with patch("pytest_leela.engine.changed_lines") as mock_cl:
            mock_cl.return_value = {abs_target: {2}}
            result_diff = engine.run(
                [str(target)], str(test_dir), diff_base="main"
            )

        # Fewer mutants tested (only line 2), and all on line 2
        assert 0 < result_diff.mutants_tested < result_all.mutants_tested
        tested_lines = {r.mutant.point.lineno for r in result_diff.results}
        assert tested_lines == {2}

    def it_stops_testing_when_memory_limit_exceeded(tmp_path, monkeypatch):
        """Engine breaks when is_memory_ok returns False.

        Kills line 129: not x → x
        """
        target = tmp_path / "t_mem.py"
        target.write_text("def add(a, b):\n    return a + b\n")
        test_dir = tmp_path / "t_mem_tests"
        test_dir.mkdir()
        (test_dir / "test_mem.py").write_text(
            "from t_mem import add\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        limits = ResourceLimits(max_memory_percent=90)

        # is_memory_ok returns False → engine should break immediately
        with patch("pytest_leela.engine.is_memory_ok", return_value=False), \
             patch("pytest_leela.engine.apply_limits"):
            engine = Engine(use_types=False, use_coverage=False)
            result = engine.run([str(target)], str(test_dir), limits=limits)

        assert result.total_mutants > 0
        assert result.mutants_tested == 0

    def it_computes_wall_time_as_monotonic_difference(tmp_path, monkeypatch):
        """wall_time = end - start, not end + start or end * start.

        Kills line 151: - → + and - → *
        """
        target = tmp_path / "t_time.py"
        # Assignment only — no mutation points, so no mutant runs
        target.write_text("x = 1\n")
        test_dir = tmp_path / "t_time_tests"
        test_dir.mkdir()
        (test_dir / "test_time.py").write_text(
            "def test_pass():\n"
            "    pass\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        mock_time = MagicMock()
        mock_time.monotonic.side_effect = [1000.0, 1000.5]

        with patch("pytest_leela.engine.time", mock_time):
            engine = Engine(use_types=False, use_coverage=False)
            result = engine.run([str(target)], str(test_dir))

        # 1000.5 - 1000.0 = 0.5 (not 2000.5 from + or 1000500.0 from *)
        assert result.wall_time_seconds == pytest.approx(0.5)


def _make_fake_runner(captured_test_ids: list) -> callable:
    """Factory for a fake ``run_tests_for_mutant`` that records test_ids."""

    def fake_run(mutant, target_sources, module_to_file,
                 test_ids=None, test_dir=None):
        captured_test_ids.append(test_ids)
        return MutantResult(
            mutant=mutant,
            killed=True,
            tests_run=1,
            killing_test="fake::test",
            time_seconds=0.01,
        )

    return fake_run


def describe_Engine_run_test_id_fallback():
    """Tests for line 183: if test_ids is None and test_node_ids is not None."""

    def it_uses_coverage_mapped_tests_not_session_fallback(tmp_path, monkeypatch):
        """Kills `and → or` and `is → is not` on line 183.

        When coverage maps a mutant line to specific tests, those tests
        should be used — NOT the session test_node_ids fallback.

        Mutant `and → or`: condition becomes ``test_ids is None or
        test_node_ids is not None``, which fires even when test_ids is
        already set by coverage.
        Mutant `is → is not`: condition becomes ``test_ids is not None
        and test_node_ids is not None``, which also fires when test_ids
        is already set.
        """
        target = tmp_path / "fb_cov.py"
        target.write_text("def add(a, b):\n    return a + b\n")
        abs_target = os.path.abspath(str(target))
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        # Coverage maps every line to a specific test
        cov_map = CoverageMap()
        for lineno in range(1, 10):
            cov_map.add(abs_target, lineno, "tests/test_specific.py::test_mapped")

        session_tests = [
            "tests/test_all.py::test_one",
            "tests/test_all.py::test_two",
        ]
        captured: list[list[str] | None] = []

        with patch("pytest_leela.engine.collect_coverage", return_value=cov_map), \
             patch("pytest_leela.engine.run_tests_for_mutant",
                   side_effect=_make_fake_runner(captured)):
            engine = Engine(use_types=False, use_coverage=True)
            result = engine.run(
                [str(target)], test_node_ids=session_tests,
            )

        assert result.mutants_tested > 0
        for test_ids in captured:
            # Coverage found tests → use those, not the session fallback
            assert test_ids == ["tests/test_specific.py::test_mapped"]

    def it_falls_back_to_session_tests_when_no_coverage_match(
        tmp_path, monkeypatch
    ):
        """Kills `is → is not` and `is not → is` on line 183.

        When coverage returns no match for a mutant line but
        test_node_ids is provided, the engine should fall back to
        session tests.

        Mutant `is → is not`: condition becomes ``test_ids is not None
        and test_node_ids is not None`` — first clause is False (test_ids
        is None from empty coverage), so fallback never fires.
        Mutant `is not → is`: condition becomes ``test_ids is None and
        test_node_ids is None`` — second clause is False (test_node_ids
        is provided), so fallback never fires.
        """
        target = tmp_path / "fb_no_cov.py"
        target.write_text("def add(a, b):\n    return a + b\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        # Empty coverage map — no tests match any mutant line
        cov_map = CoverageMap()

        session_tests = [
            "tests/test_session.py::test_a",
            "tests/test_session.py::test_b",
        ]
        captured: list[list[str] | None] = []

        with patch("pytest_leela.engine.collect_coverage", return_value=cov_map), \
             patch("pytest_leela.engine.run_tests_for_mutant",
                   side_effect=_make_fake_runner(captured)):
            engine = Engine(use_types=False, use_coverage=True)
            result = engine.run(
                [str(target)], test_node_ids=session_tests,
            )

        assert result.mutants_tested > 0
        for test_ids in captured:
            # No coverage match → should fall back to session tests
            assert test_ids == session_tests

    def it_preserves_coverage_tests_when_no_session_tests_available(
        tmp_path, monkeypatch
    ):
        """Validates coverage-mapped tests survive when test_node_ids is None.

        Coverage returns specific tests, test_node_ids is None.
        Verify the coverage-mapped tests are passed to the runner
        without being overwritten by the fallback.
        """
        target = tmp_path / "fb_covonly.py"
        target.write_text("def add(a, b):\n    return a + b\n")
        abs_target = os.path.abspath(str(target))
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))

        # Coverage maps every line to a specific test
        cov_map = CoverageMap()
        for lineno in range(1, 10):
            cov_map.add(abs_target, lineno, "tests/test_cov.py::test_one")

        captured: list[list[str] | None] = []

        with patch("pytest_leela.engine.collect_coverage", return_value=cov_map), \
             patch("pytest_leela.engine.run_tests_for_mutant",
                   side_effect=_make_fake_runner(captured)):
            engine = Engine(use_types=False, use_coverage=True)
            # No session tests available
            result = engine.run([str(target)], test_node_ids=None)

        assert result.mutants_tested > 0
        for test_ids in captured:
            # Coverage found tests → use those, no fallback to overwrite
            assert test_ids == ["tests/test_cov.py::test_one"]
