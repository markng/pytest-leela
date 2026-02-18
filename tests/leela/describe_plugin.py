"""Tests for pytest_leela.plugin — target file discovery and plugin behavior."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from pytest_leela.plugin import _find_default_targets, _find_target_files


def describe_find_target_files():
    def it_returns_single_file_for_python_file(tmp_path):
        target = tmp_path / "module.py"
        target.write_text("x = 1\n")
        result = _find_target_files(str(target))
        assert result == [str(target.resolve())]

    def it_returns_empty_for_nonexistent_path():
        result = _find_target_files("/nonexistent/path/nope.py")
        assert result == []

    def it_returns_empty_for_non_python_file(tmp_path):
        target = tmp_path / "data.txt"
        target.write_text("hello\n")
        result = _find_target_files(str(target))
        assert result == []

    def it_finds_all_python_files_in_directory(tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        result = _find_target_files(str(tmp_path))
        basenames = sorted(os.path.basename(f) for f in result)
        assert basenames == ["a.py", "b.py"]

    def it_excludes_dunder_files_from_directory(tmp_path):
        (tmp_path / "__init__.py").write_text("")
        (tmp_path / "real.py").write_text("x = 1\n")
        result = _find_target_files(str(tmp_path))
        basenames = [os.path.basename(f) for f in result]
        assert "__init__.py" not in basenames
        assert "real.py" in basenames

    def it_finds_files_recursively(tmp_path):
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "deep.py").write_text("z = 3\n")
        (tmp_path / "top.py").write_text("a = 1\n")
        result = _find_target_files(str(tmp_path))
        basenames = sorted(os.path.basename(f) for f in result)
        assert "deep.py" in basenames
        assert "top.py" in basenames

    def it_requires_both_is_file_and_py_extension(tmp_path):
        """The `and` condition: file must be an actual file AND end with .py."""
        # A directory ending with .py should NOT be returned as a single file
        weird_dir = tmp_path / "notafile.py"
        weird_dir.mkdir()
        result = _find_target_files(str(weird_dir))
        # It's a dir, so it falls through to isdir branch and returns its contents
        assert isinstance(result, list)
        # Crucially, the single-file return path was NOT taken
        assert str(weird_dir.resolve()) not in result

    def it_returns_list_not_none_for_directory(tmp_path):
        """Return value from directory branch must be a list, not None."""
        (tmp_path / "mod.py").write_text("x = 1\n")
        result = _find_target_files(str(tmp_path))
        assert isinstance(result, list)
        assert len(result) > 0

    def it_returns_list_not_none_for_file(tmp_path):
        """Return value from file branch must be a list, not None."""
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        result = _find_target_files(str(f))
        assert isinstance(result, list)
        assert len(result) == 1

    def it_returns_empty_list_not_none_for_unknown(tmp_path):
        """Fallback return must be an empty list, not None."""
        result = _find_target_files(str(tmp_path / "nonexistent"))
        assert result == []
        assert result is not None


def describe_find_default_targets():
    def it_finds_files_in_target_directory(tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "app.py").write_text("x = 1\n")
        result = _find_default_targets(tmp_path)
        assert len(result) == 1
        assert "app.py" in result[0]

    def it_finds_files_in_src_directory(tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "lib.py").write_text("y = 2\n")
        result = _find_default_targets(tmp_path)
        assert len(result) == 1
        assert "lib.py" in result[0]

    def it_prefers_target_over_src(tmp_path):
        """'target' is checked before 'src'."""
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "from_target.py").write_text("a = 1\n")
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "from_src.py").write_text("b = 2\n")
        result = _find_default_targets(tmp_path)
        basenames = [os.path.basename(f) for f in result]
        assert "from_target.py" in basenames
        assert "from_src.py" not in basenames

    def it_excludes_dunder_files(tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "__init__.py").write_text("")
        (src_dir / "real.py").write_text("x = 1\n")
        result = _find_default_targets(tmp_path)
        basenames = [os.path.basename(f) for f in result]
        assert "__init__.py" not in basenames
        assert "real.py" in basenames

    def it_returns_empty_when_no_standard_dirs(tmp_path):
        result = _find_default_targets(tmp_path)
        assert result == []
        assert result is not None

    def it_returns_list_not_none_when_dir_exists(tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "mod.py").write_text("x = 1\n")
        result = _find_default_targets(tmp_path)
        assert isinstance(result, list)
        assert len(result) > 0

    def it_finds_nested_files(tmp_path):
        """rglob finds .py files in subdirectories."""
        src_dir = tmp_path / "src"
        pkg = src_dir / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "nested.py").write_text("z = 3\n")
        result = _find_default_targets(tmp_path)
        assert len(result) >= 1
        assert any("nested.py" in f for f in result)


def describe_LeelaPlugin():
    def it_skips_mutation_when_exit_status_nonzero():
        """exitstatus != 0 should cause early return."""
        from pytest_leela.plugin import LeelaPlugin

        config = MagicMock()
        plugin = LeelaPlugin(config)
        session = MagicMock()
        # Should not crash, just return
        result = plugin.pytest_sessionfinish(session, exitstatus=1)
        assert result is None
        # Engine should NOT have been called
        config.getoption.assert_not_called()

    def it_does_not_skip_when_exit_status_zero():
        """exitstatus == 0 should proceed (the != mutation would skip it)."""
        from pytest_leela.plugin import LeelaPlugin

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: default
        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/fake")

        # When exitstatus is 0, getoption WILL be called
        plugin.pytest_sessionfinish(session, exitstatus=0)
        config.getoption.assert_called()

    def it_skips_when_no_target_files_found():
        """If target_files is empty, should return early."""
        from pytest_leela.plugin import LeelaPlugin

        config = MagicMock()
        config.getoption.return_value = None
        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/nonexistent_root")

        with patch("pytest_leela.plugin._find_default_targets", return_value=[]):
            # Should not crash — just returns when no target files
            plugin.pytest_sessionfinish(session, exitstatus=0)

    def it_runs_engine_when_target_files_found():
        """When target_files is non-empty, engine must run (line 95 guard).

        The `not target_files` → `target_files` mutation would cause early
        return when files ARE found, skipping the engine entirely.
        """
        from pytest_leela.plugin import LeelaPlugin

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": "/fake/target.py",
            "diff": None,
            "max_cores": None,
            "max_memory": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")

        mock_engine = MagicMock()
        mock_result = MagicMock()
        mock_engine.return_value.run.return_value = mock_result

        with (
            patch("pytest_leela.plugin._find_target_files", return_value=["/fake/target.py"]),
            patch("pytest_leela.plugin.Engine", mock_engine),
            patch("pytest_leela.plugin.format_terminal_report", return_value="report"),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Engine.run MUST have been called — the `not target_files` guard
        # should NOT have triggered early return
        mock_engine.return_value.run.assert_called_once()

    def it_constructs_test_dir_with_path_join():
        """test_dir uses Path / operator (line 98): rootpath / 'tests'.

        The `/` → `*` and `/` → `//` mutations would crash or produce
        wrong paths. Verify the constructed test_dir is correct.
        """
        from pytest_leela.plugin import LeelaPlugin

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": "/fake/mod.py",
            "diff": None,
            "max_cores": None,
            "max_memory": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/myproject")

        mock_engine_cls = MagicMock()
        mock_engine = mock_engine_cls.return_value
        mock_engine.run.return_value = MagicMock()

        with (
            patch("pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Verify engine.run was called with the correct test_dir
        call_args = mock_engine.run.call_args
        test_dir = call_args.kwargs.get("test_dir") or call_args[0][1]
        assert test_dir == str(Path("/tmp/myproject") / "tests")
        assert test_dir == "/tmp/myproject/tests"
