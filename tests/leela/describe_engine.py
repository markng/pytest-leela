"""Tests for pytest_leela.engine."""

import sys
from unittest.mock import patch

from pytest_leela.engine import Engine, _module_name_from_path
from pytest_leela.models import RunResult


def describe_engine():
    def it_is_importable():
        from pytest_leela.engine import Engine

        assert Engine is not None


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
