"""Tests for pytest_leela.plugin — target file discovery and plugin behavior."""

import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from pytest_leela.plugin import (
    _SKIP_DIRS,
    _apply_excludes,
    _find_default_targets,
    _find_target_files,
    _is_test_file,
    pytest_addoption,
    pytest_configure,
)


def describe_is_test_file():
    DEFAULT_PATTERNS = ["test_*.py", "describe_*.py"]

    def it_detects_conftest_regardless_of_patterns():
        """conftest.py must always be excluded, even with empty patterns."""
        assert _is_test_file("conftest.py", []) is True
        assert _is_test_file("conftest.py", DEFAULT_PATTERNS) is True

    def it_matches_fnmatch_patterns():
        """Patterns are matched using fnmatch semantics."""
        assert _is_test_file("test_foo.py", DEFAULT_PATTERNS) is True
        assert _is_test_file("describe_utils.py", DEFAULT_PATTERNS) is True
        assert _is_test_file("foo_test.py", DEFAULT_PATTERNS) is False

    def it_respects_custom_patterns():
        """Custom patterns are applied instead of defaults."""
        custom = ["describe_*.py"]
        assert _is_test_file("describe_utils.py", custom) is True
        assert _is_test_file("test_foo.py", custom) is False

    def it_allows_non_test_modules():
        """Regular source files are not flagged."""
        assert _is_test_file("models.py", DEFAULT_PATTERNS) is False
        assert _is_test_file("app.py", DEFAULT_PATTERNS) is False

    def it_requires_exact_fnmatch_match():
        """fnmatch must match the full basename."""
        assert _is_test_file("testing_utils.py", ["test_*.py"]) is False


def describe_find_target_files():
    def it_returns_single_file_for_python_file(tmp_path):
        target = tmp_path / "module.py"
        target.write_text("x = 1\n")
        result = _find_target_files(str(target), ["test_*.py", "describe_*.py"])
        assert result == [str(target.resolve())]

    def it_returns_empty_for_nonexistent_path():
        result = _find_target_files(
            "/nonexistent/path/nope.py", ["test_*.py", "describe_*.py"]
        )
        assert result == []

    def it_returns_empty_for_non_python_file(tmp_path):
        target = tmp_path / "data.txt"
        target.write_text("hello\n")
        result = _find_target_files(str(target), ["test_*.py", "describe_*.py"])
        assert result == []

    def it_finds_all_python_files_in_directory(tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        result = _find_target_files(str(tmp_path), ["test_*.py", "describe_*.py"])
        basenames = sorted(os.path.basename(f) for f in result)
        assert basenames == ["a.py", "b.py"]

    def it_excludes_dunder_files_from_directory(tmp_path):
        (tmp_path / "__init__.py").write_text("")
        (tmp_path / "real.py").write_text("x = 1\n")
        result = _find_target_files(str(tmp_path), ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert "__init__.py" not in basenames
        assert "real.py" in basenames

    def it_finds_files_recursively(tmp_path):
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "deep.py").write_text("z = 3\n")
        (tmp_path / "top.py").write_text("a = 1\n")
        result = _find_target_files(str(tmp_path), ["test_*.py", "describe_*.py"])
        basenames = sorted(os.path.basename(f) for f in result)
        assert "deep.py" in basenames
        assert "top.py" in basenames

    def it_requires_both_is_file_and_py_extension(tmp_path):
        """The `and` condition: file must be an actual file AND end with .py."""
        # A directory ending with .py should NOT be returned as a single file
        weird_dir = tmp_path / "notafile.py"
        weird_dir.mkdir()
        result = _find_target_files(str(weird_dir), ["test_*.py", "describe_*.py"])
        # It's a dir, so it falls through to isdir branch and returns its contents
        assert isinstance(result, list)
        # Crucially, the single-file return path was NOT taken
        assert str(weird_dir.resolve()) not in result

    def it_returns_list_not_none_for_directory(tmp_path):
        """Return value from directory branch must be a list, not None."""
        (tmp_path / "mod.py").write_text("x = 1\n")
        result = _find_target_files(str(tmp_path), ["test_*.py", "describe_*.py"])
        assert isinstance(result, list)
        assert len(result) > 0

    def it_returns_list_not_none_for_file(tmp_path):
        """Return value from file branch must be a list, not None."""
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        result = _find_target_files(str(f), ["test_*.py", "describe_*.py"])
        assert isinstance(result, list)
        assert len(result) == 1

    def it_returns_empty_list_not_none_for_unknown(tmp_path):
        """Fallback return must be an empty list, not None."""
        result = _find_target_files(
            str(tmp_path / "nonexistent"), ["test_*.py", "describe_*.py"]
        )
        assert result == []
        assert result is not None

    def it_excludes_test_files_from_directory(tmp_path):
        (tmp_path / "models.py").write_text("x = 1\n")
        (tmp_path / "test_models.py").write_text("def test(): pass\n")
        (tmp_path / "describe_utils.py").write_text("def it_works(): pass\n")
        (tmp_path / "conftest.py").write_text("import pytest\n")
        result = _find_target_files(str(tmp_path), ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert "models.py" in basenames
        assert "test_models.py" not in basenames
        assert "describe_utils.py" not in basenames
        assert "conftest.py" not in basenames


def describe_find_default_targets():
    def it_finds_files_in_target_directory(tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "app.py").write_text("x = 1\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        assert len(result) == 1
        assert "app.py" in result[0]

    def it_finds_files_in_src_directory(tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "lib.py").write_text("y = 2\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
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
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert "from_target.py" in basenames
        assert "from_src.py" not in basenames

    def it_excludes_dunder_files(tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "__init__.py").write_text("")
        (src_dir / "real.py").write_text("x = 1\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert "__init__.py" not in basenames
        assert "real.py" in basenames

    def it_falls_back_to_rootpath_when_no_standard_dirs(tmp_path):
        """When neither src/ nor target/ exists, scan rootpath itself."""
        (tmp_path / "app.py").write_text("x = 1\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        assert len(result) == 1
        assert "app.py" in result[0]

    def it_excludes_dunder_and_test_files_in_fallback_path(tmp_path):
        """Fallback rootpath scan excludes __init__.py and test files."""
        (tmp_path / "app.py").write_text("x = 1\n")
        (tmp_path / "__init__.py").write_text("")
        (tmp_path / "test_app.py").write_text("def test(): pass\n")
        (tmp_path / "conftest.py").write_text("import pytest\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert basenames == ["app.py"]

    def it_returns_empty_when_no_standard_dirs_and_no_py_files(tmp_path):
        """Empty rootpath with no .py files returns empty list."""
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        assert result == []

    def it_returns_list_not_none_when_dir_exists(tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "mod.py").write_text("x = 1\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        assert isinstance(result, list)
        assert len(result) > 0

    def it_finds_nested_files(tmp_path):
        """rglob finds .py files in subdirectories."""
        src_dir = tmp_path / "src"
        pkg = src_dir / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "nested.py").write_text("z = 3\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        assert len(result) >= 1
        assert any("nested.py" in f for f in result)

    def it_excludes_test_files(tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("x = 1\n")
        (src_dir / "test_app.py").write_text("def test(): pass\n")
        (src_dir / "conftest.py").write_text("import pytest\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert "app.py" in basenames
        assert "test_app.py" not in basenames
        assert "conftest.py" not in basenames

    def it_skips_venv_in_fallback(tmp_path):
        """Rootpath fallback must not recurse into .venv/."""
        venv_dir = tmp_path / ".venv" / "lib"
        venv_dir.mkdir(parents=True)
        (venv_dir / "six.py").write_text("x = 1\n")
        (tmp_path / "app.py").write_text("y = 2\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert "app.py" in basenames
        assert "six.py" not in basenames

    def it_skips_all_common_non_source_dirs_in_fallback(tmp_path):
        """Every directory in _SKIP_DIRS (except glob patterns) is skipped."""
        exact_dirs = {d for d in _SKIP_DIRS if "*" not in d}
        for dirname in exact_dirs:
            d = tmp_path / dirname
            d.mkdir(exist_ok=True)
            (d / "mod.py").write_text("x = 1\n")
        (tmp_path / "real.py").write_text("y = 2\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert basenames == ["real.py"]

    def it_skips_egg_info_dirs_in_fallback(tmp_path):
        """Directories matching *.egg-info are skipped in fallback."""
        egg_dir = tmp_path / "mypkg.egg-info"
        egg_dir.mkdir()
        (egg_dir / "PKG-INFO.py").write_text("x = 1\n")
        (tmp_path / "app.py").write_text("y = 2\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert "app.py" in basenames
        assert "PKG-INFO.py" not in basenames

    def it_skips_nested_skip_dirs_in_fallback(tmp_path):
        """Skip dirs nested deeper than top-level are also excluded."""
        nested = tmp_path / "pkg" / "node_modules" / "dep"
        nested.mkdir(parents=True)
        (nested / "index.py").write_text("x = 1\n")
        (tmp_path / "app.py").write_text("y = 2\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert "app.py" in basenames
        assert "index.py" not in basenames

    def it_does_not_skip_dirs_when_using_src_candidate(tmp_path):
        """The skip-dirs filter only applies to the fallback path, not src/."""
        src_dir = tmp_path / "src" / "build"
        src_dir.mkdir(parents=True)
        (src_dir / "builder.py").write_text("x = 1\n")
        result = _find_default_targets(tmp_path, ["test_*.py", "describe_*.py"])
        basenames = [os.path.basename(f) for f in result]
        assert "builder.py" in basenames


def describe_LeelaPlugin():
    def it_skips_mutation_when_exit_status_nonzero():
        """exitstatus != 0 should cause early return."""
        from pytest_leela.plugin import LeelaPlugin

        config = MagicMock()
        plugin = LeelaPlugin(config)
        session = MagicMock()
        # Should not crash, just return — load_config is never reached
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
        with patch("pytest_leela.plugin.load_config") as mock_load:
            from pytest_leela.config import LeelaConfig

            mock_load.return_value = LeelaConfig()
            plugin.pytest_sessionfinish(session, exitstatus=0)
        config.getoption.assert_called()

    def it_skips_when_no_target_files_found():
        """If target_files is empty, should return early."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig

        config = MagicMock()
        config.getoption.return_value = None
        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/nonexistent_root")

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch("pytest_leela.plugin._find_default_targets", return_value=[]),
        ):
            # Should not crash — just returns when no target files
            plugin.pytest_sessionfinish(session, exitstatus=0)

    def it_runs_engine_when_target_files_found():
        """When target_files is non-empty, engine must run (line 95 guard).

        The `not target_files` → `target_files` mutation would cause early
        return when files ARE found, skipping the engine entirely.
        """
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/target.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]

        mock_engine = MagicMock()
        mock_result = MagicMock()
        mock_engine.return_value.run.return_value = mock_result

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files",
                return_value=["/fake/target.py"],
            ),
            patch("pytest_leela.plugin.Engine", mock_engine),
            patch("pytest_leela.plugin.format_terminal_report", return_value="report"),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Engine.run MUST have been called — the `not target_files` guard
        # should NOT have triggered early return
        mock_engine.return_value.run.assert_called_once()

    def it_collects_test_node_ids_from_session():
        """test_node_ids are collected from session.items (line 116).

        This replaced the old hardcoded ``rootpath / 'tests'`` approach,
        letting pytest-leela work with any test layout.
        """
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/myproject")
        session.items = [
            MagicMock(nodeid="tests/test_a.py::test_one"),
            MagicMock(nodeid="tests/test_b.py::test_two"),
        ]

        mock_engine_cls = MagicMock()
        mock_engine = mock_engine_cls.return_value
        mock_engine.run.return_value = MagicMock()

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Verify engine.run was called with test_node_ids from session.items
        call_kwargs = mock_engine.run.call_args.kwargs
        assert "test_node_ids" in call_kwargs
        assert call_kwargs["test_node_ids"] == [
            "tests/test_a.py::test_one",
            "tests/test_b.py::test_two",
        ]

    def it_sets_exitstatus_to_1_when_mutants_survived():
        """When result.survived is non-empty, exitstatus should be 1."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig
        from pytest_leela.models import RunResult, MutantResult, Mutant, MutationPoint

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]

        # Create a RunResult with one survived mutant
        point = MutationPoint(
            file_path="/fake/mod.py",
            module_name="mod",
            lineno=10,
            col_offset=0,
            node_type="BinOp",
            original_op="Add",
            inferred_type="int",
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=1)
        survived_result = MutantResult(
            mutant=mutant,
            killed=False,
            tests_run=5,
            killing_test=None,
            time_seconds=0.1,
        )
        run_result = RunResult(
            target_files=["/fake/mod.py"],
            total_mutants=1,
            mutants_tested=1,
            mutants_pruned=0,
            results=[survived_result],
            wall_time_seconds=0.5,
        )

        mock_engine_cls = MagicMock()
        mock_engine = mock_engine_cls.return_value
        mock_engine.run.return_value = run_result

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Verify exitstatus was set to 1
        assert session.exitstatus == 1

    def it_keeps_exitstatus_0_when_all_mutants_killed():
        """When result.survived is empty, exitstatus should remain 0."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig
        from pytest_leela.models import RunResult, MutantResult, Mutant, MutationPoint

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]
        session.exitstatus = 0  # Start with 0

        # Create a RunResult with no survived mutants (all killed)
        point = MutationPoint(
            file_path="/fake/mod.py",
            module_name="mod",
            lineno=10,
            col_offset=0,
            node_type="BinOp",
            original_op="Add",
            inferred_type="int",
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=1)
        killed_result = MutantResult(
            mutant=mutant,
            killed=True,
            tests_run=5,
            killing_test="test_a.py::test_one",
            time_seconds=0.1,
        )
        run_result = RunResult(
            target_files=["/fake/mod.py"],
            total_mutants=1,
            mutants_tested=1,
            mutants_pruned=0,
            results=[killed_result],
            wall_time_seconds=0.5,
        )

        mock_engine_cls = MagicMock()
        mock_engine = mock_engine_cls.return_value
        mock_engine.run.return_value = run_result

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Verify exitstatus remained 0
        assert session.exitstatus == 0

    def it_keeps_exitstatus_0_when_no_mutants_found():
        """When total_mutants is 0, exitstatus should remain 0."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig
        from pytest_leela.models import RunResult

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]
        session.exitstatus = 0  # Start with 0

        # Create a RunResult with no mutants found
        run_result = RunResult(
            target_files=["/fake/mod.py"],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.1,
        )

        mock_engine_cls = MagicMock()
        mock_engine = mock_engine_cls.return_value
        mock_engine.run.return_value = run_result

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Verify exitstatus remained 0
        assert session.exitstatus == 0

    def it_installs_coverage_plugin_during_sessionstart_when_targets_exist():
        """pytest_sessionstart should register a CoveragePlugin when targets resolve."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig
        from pytest_leela.coverage_tracker import CoveragePlugin

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files",
                return_value=["/fake/mod.py"],
            ),
        ):
            plugin.pytest_sessionstart(session)

        # CoveragePlugin should have been registered
        session.config.pluginmanager.register.assert_called_once()
        registered_args = session.config.pluginmanager.register.call_args
        assert isinstance(registered_args[0][0], CoveragePlugin)
        assert registered_args[0][1] == "leela-coverage"
        assert plugin._coverage_plugin is not None

    def it_does_not_install_coverage_plugin_when_no_targets():
        """pytest_sessionstart should skip CoveragePlugin when no targets resolve."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": [],
            "diff": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/empty_project")

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch("pytest_leela.plugin._find_default_targets", return_value=[]),
        ):
            plugin.pytest_sessionstart(session)

        session.config.pluginmanager.register.assert_not_called()
        assert plugin._coverage_plugin is None

    def it_resolve_target_files_uses_explicit_targets():
        """_resolve_target_files should use --target when provided."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files",
                return_value=["/fake/mod.py"],
            ),
        ):
            result = plugin._resolve_target_files(session)

        assert result == ["/fake/mod.py"]

    def it_resolve_target_files_uses_diff_base_when_no_targets():
        """_resolve_target_files should use --diff when --target is not given."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": [],
            "diff": "main",
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin.changed_files",
                return_value=["/fake/changed.py"],
            ),
        ):
            result = plugin._resolve_target_files(session)

        assert result == ["/fake/changed.py"]

    def it_passes_pre_coverage_map_to_engine():
        """When CoveragePlugin collected coverage, it should be passed to engine."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig
        from pytest_leela.models import CoverageMap

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
            "leela_html": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)

        # Simulate sessionstart having installed a CoveragePlugin
        from pytest_leela.coverage_tracker import CoveragePlugin as CovPlugin

        cov_plugin = MagicMock(spec=CovPlugin)
        fake_coverage = CoverageMap()
        fake_coverage.add("/fake/mod.py", 1, "test_a")
        cov_plugin.coverage_map = fake_coverage
        cov_plugin.test_times = {"test_a": 0.01}
        plugin._coverage_plugin = cov_plugin

        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]

        mock_engine_cls = MagicMock()
        mock_engine = mock_engine_cls.return_value
        mock_engine.run.return_value = MagicMock(survived=[])

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files",
                return_value=["/fake/mod.py"],
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Verify pre_coverage_map was passed to engine.run
        call_kwargs = mock_engine.run.call_args.kwargs
        assert "pre_coverage_map" in call_kwargs
        assert call_kwargs["pre_coverage_map"] is fake_coverage

    def it_passes_none_pre_coverage_map_when_no_coverage_plugin():
        """When no CoveragePlugin was installed, pre_coverage_map should be None."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
            "leela_html": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        assert plugin._coverage_plugin is None

        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]

        mock_engine_cls = MagicMock()
        mock_engine = mock_engine_cls.return_value
        mock_engine.run.return_value = MagicMock(survived=[])

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files",
                return_value=["/fake/mod.py"],
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        call_kwargs = mock_engine.run.call_args.kwargs
        assert "pre_coverage_map" in call_kwargs
        assert call_kwargs["pre_coverage_map"] is None

    def it_registers_leela_html_option():
        """--leela-html should be registered as a plugin option."""
        parser = MagicMock()
        group = MagicMock()
        parser.getgroup.return_value = group

        pytest_addoption(parser)

        # Collect all addoption calls and find the --leela-html one
        leela_html_calls = [
            c
            for c in group.addoption.call_args_list
            if c.args and c.args[0] == "--leela-html"
        ]
        assert len(leela_html_calls) == 1
        kwargs = leela_html_calls[0].kwargs
        assert kwargs["default"] is None
        assert kwargs["metavar"] == "PATH"

    def it_activates_plugin_with_leela_html_only():
        """Plugin should register even without --leela when --leela-html is set."""
        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "leela": False,
            "leela_html": "/tmp/report.html",
            "leela_benchmark": False,
        }.get(key, default)

        pytest_configure(config)

        config.pluginmanager.register.assert_called_once()
        args = config.pluginmanager.register.call_args
        assert args[1] == {} or args.kwargs == {}
        # Second positional arg is the name
        assert args[0][1] == "leela-plugin"

    def it_calls_generate_html_report_when_flag_set():
        """generate_html_report should be called with result and path."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig
        from pytest_leela.models import RunResult

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
            "leela_html": "/tmp/report.html",
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]
        session.exitstatus = 0

        run_result = RunResult(
            target_files=["/fake/mod.py"],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.1,
        )

        mock_engine_cls = MagicMock()
        mock_engine_cls.return_value.run.return_value = run_result

        mock_generate = MagicMock()

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
            patch("pytest_leela.html_report.generate_html_report", mock_generate),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        mock_generate.assert_called_once_with(run_result, "/tmp/report.html")

    def it_does_not_generate_html_report_without_flag():
        """No HTML report when --leela-html is not set."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig
        from pytest_leela.models import RunResult

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
            "leela_html": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]
        session.exitstatus = 0

        run_result = RunResult(
            target_files=["/fake/mod.py"],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.1,
        )

        mock_engine_cls = MagicMock()
        mock_engine_cls.return_value.run.return_value = run_result

        mock_generate = MagicMock()

        with (
            patch("pytest_leela.plugin.load_config", return_value=LeelaConfig()),
            patch(
                "pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
            patch("pytest_leela.html_report.generate_html_report", mock_generate),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        mock_generate.assert_not_called()

    def it_loads_config_and_passes_operators_to_engine():
        """Config operators are passed through as enabled_categories to Engine."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
            "leela_html": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]
        session.exitstatus = 0

        custom_operators = ("arithmetic", "comparison")
        leela_cfg = LeelaConfig(operators=custom_operators)

        mock_engine_cls = MagicMock()
        mock_engine_cls.return_value.run.return_value = MagicMock(survived=[])

        with (
            patch("pytest_leela.plugin.load_config", return_value=leela_cfg),
            patch(
                "pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Engine was constructed with enabled_categories from config
        mock_engine_cls.assert_called_once_with(enabled_categories=custom_operators)

    def it_resolves_all_keyword_in_operators():
        """When config contains 'all', it should expand to ALL_OPERATORS."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import ALL_OPERATORS, LeelaConfig

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": ["/fake/mod.py"],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
            "leela_html": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]
        session.exitstatus = 0

        leela_cfg = LeelaConfig(operators=("all",))

        mock_engine_cls = MagicMock()
        mock_engine_cls.return_value.run.return_value = MagicMock(survived=[])

        with (
            patch("pytest_leela.plugin.load_config", return_value=leela_cfg),
            patch(
                "pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Engine was constructed with ALL_OPERATORS (expanded from "all")
        mock_engine_cls.assert_called_once_with(enabled_categories=ALL_OPERATORS)

    def it_applies_exclude_patterns_from_config():
        """Files matching exclude patterns should be filtered out before engine."""
        from pytest_leela.plugin import LeelaPlugin
        from pytest_leela.config import LeelaConfig

        config = MagicMock()
        config.getoption.side_effect = lambda key, default=None: {
            "target": [],
            "diff": None,
            "max_cores": None,
            "max_memory": None,
            "leela_html": None,
        }.get(key, default)

        plugin = LeelaPlugin(config)
        session = MagicMock()
        session.config = config
        session.config.rootpath = Path("/tmp/project")
        session.items = [MagicMock(nodeid="tests/test_a.py::test_one")]
        session.exitstatus = 0

        leela_cfg = LeelaConfig(exclude=("migrations/*.py",))

        mock_engine_cls = MagicMock()
        mock_engine_cls.return_value.run.return_value = MagicMock(survived=[])

        # _find_default_targets returns files including one in migrations/
        default_files = [
            "/tmp/project/models.py",
            "/tmp/project/migrations/0001_initial.py",
        ]

        with (
            patch("pytest_leela.plugin.load_config", return_value=leela_cfg),
            patch(
                "pytest_leela.plugin._find_default_targets", return_value=default_files
            ),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Engine.run should have been called with only the non-excluded file
        call_args = mock_engine_cls.return_value.run.call_args
        target_files_passed = call_args[0][0]
        assert "/tmp/project/models.py" in target_files_passed
        assert "/tmp/project/migrations/0001_initial.py" not in target_files_passed


def describe_apply_excludes():
    def it_returns_all_files_when_no_excludes():
        files = ["/root/a.py", "/root/b.py"]
        result = _apply_excludes(files, [], Path("/root"))
        assert result == files

    def it_filters_files_matching_pattern():
        files = [
            "/root/app.py",
            "/root/migrations/0001.py",
            "/root/migrations/0002.py",
        ]
        result = _apply_excludes(files, ["migrations/*.py"], Path("/root"))
        assert result == ["/root/app.py"]

    def it_supports_multiple_exclude_patterns():
        files = [
            "/root/app.py",
            "/root/migrations/0001.py",
            "/root/vendor/lib.py",
        ]
        result = _apply_excludes(
            files, ["migrations/*.py", "vendor/*.py"], Path("/root")
        )
        assert result == ["/root/app.py"]

    def it_uses_relative_paths_for_matching():
        """Patterns match against paths relative to rootpath, not absolute."""
        files = ["/project/src/models.py"]
        # Pattern "src/models.py" should match relative path
        result = _apply_excludes(files, ["src/models.py"], Path("/project"))
        assert result == []

    def it_preserves_order():
        files = ["/root/c.py", "/root/a.py", "/root/b.py"]
        result = _apply_excludes(files, ["nonexistent*.py"], Path("/root"))
        assert result == ["/root/c.py", "/root/a.py", "/root/b.py"]

    def it_handles_wildcard_patterns():
        files = ["/root/foo.py", "/root/bar.py", "/root/baz.txt"]
        result = _apply_excludes(files, ["b*.py"], Path("/root"))
        assert result == ["/root/foo.py", "/root/baz.txt"]

    def it_returns_empty_when_all_excluded():
        files = ["/root/a.py", "/root/b.py"]
        result = _apply_excludes(files, ["*.py"], Path("/root"))
        assert result == []

    def it_handles_nested_directory_patterns():
        files = [
            "/root/pkg/sub/deep.py",
            "/root/pkg/top.py",
        ]
        result = _apply_excludes(files, ["pkg/sub/*.py"], Path("/root"))
        assert result == ["/root/pkg/top.py"]

    def it_normalizes_path_separators_for_matching():
        """Paths are normalized to forward slashes before fnmatch."""
        # Simulate what would happen with backslash separators by
        # monkeypatching os.sep temporarily — on Unix os.sep is already '/'
        # so we verify the .replace(os.sep, "/") call is present by
        # checking the result is correct on all platforms.
        files = ["/root/pkg/sub/deep.py"]
        result = _apply_excludes(files, ["pkg/sub/*.py"], Path("/root"))
        assert result == []

    def it_normalizes_backslash_paths(monkeypatch):
        """When os.sep is backslash, relpath uses backslashes but
        patterns use forward slashes — normalization handles this."""
        import pytest_leela.plugin as plugin_mod

        original_relpath = os.path.relpath

        def fake_relpath(path, start):
            """Return a path with backslashes to simulate Windows."""
            return original_relpath(path, start).replace("/", "\\")

        monkeypatch.setattr(os.path, "relpath", fake_relpath)
        # Ensure os.sep is backslash for the .replace() call
        monkeypatch.setattr(os, "sep", "\\")

        files = ["/root/migrations/0001.py"]
        result = _apply_excludes(files, ["migrations/*.py"], Path("/root"))
        assert result == []
