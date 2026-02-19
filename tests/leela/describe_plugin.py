"""Tests for pytest_leela.plugin — target file discovery and plugin behavior."""

import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from pytest_leela.plugin import (
    _find_default_targets,
    _find_target_files,
    _is_test_file,
    pytest_addoption,
    pytest_configure,
)


def describe_is_test_file():
    def it_detects_test_prefix():
        assert _is_test_file("test_foo.py") is True

    def it_detects_test_suffix():
        assert _is_test_file("foo_test.py") is True

    def it_detects_conftest():
        assert _is_test_file("conftest.py") is True

    def it_detects_tests_py():
        assert _is_test_file("tests.py") is True

    def it_allows_regular_modules():
        assert _is_test_file("models.py") is False

    def it_allows_modules_with_test_in_name():
        """A module like 'contest.py' should not be flagged."""
        assert _is_test_file("contest.py") is False

    def it_detects_tests_prefix():
        """Django projects often use 'tests_mailerlite.py' etc."""
        assert _is_test_file("tests_mailerlite.py") is True

    def it_requires_exact_prefix_match():
        """'testing_utils.py' starts with 'test' but not 'test_'."""
        assert _is_test_file("testing_utils.py") is False


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

    def it_excludes_test_files_from_directory(tmp_path):
        (tmp_path / "models.py").write_text("x = 1\n")
        (tmp_path / "test_models.py").write_text("def test(): pass\n")
        (tmp_path / "views_test.py").write_text("def test(): pass\n")
        (tmp_path / "conftest.py").write_text("import pytest\n")
        (tmp_path / "tests.py").write_text("from django.test import TestCase\n")
        (tmp_path / "tests_mailerlite.py").write_text("def test(): pass\n")
        result = _find_target_files(str(tmp_path))
        basenames = [os.path.basename(f) for f in result]
        assert "models.py" in basenames
        assert "test_models.py" not in basenames
        assert "views_test.py" not in basenames
        assert "conftest.py" not in basenames
        assert "tests.py" not in basenames
        assert "tests_mailerlite.py" not in basenames


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

    def it_excludes_test_files(tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("x = 1\n")
        (src_dir / "test_app.py").write_text("def test(): pass\n")
        (src_dir / "conftest.py").write_text("import pytest\n")
        (src_dir / "tests.py").write_text("from django.test import TestCase\n")
        result = _find_default_targets(tmp_path)
        basenames = [os.path.basename(f) for f in result]
        assert "app.py" in basenames
        assert "test_app.py" not in basenames
        assert "conftest.py" not in basenames
        assert "tests.py" not in basenames


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
            patch("pytest_leela.plugin._find_target_files", return_value=["/fake/target.py"]),
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
            patch("pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]),
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
            mutant=mutant, killed=False, tests_run=5, killing_test=None, time_seconds=0.1
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
            patch("pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Verify exitstatus was set to 1
        assert session.exitstatus == 1

    def it_keeps_exitstatus_0_when_all_mutants_killed():
        """When result.survived is empty, exitstatus should remain 0."""
        from pytest_leela.plugin import LeelaPlugin
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
            mutant=mutant, killed=True, tests_run=5, killing_test="test_a.py::test_one", time_seconds=0.1
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
            patch("pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Verify exitstatus remained 0
        assert session.exitstatus == 0

    def it_keeps_exitstatus_0_when_no_mutants_found():
        """When total_mutants is 0, exitstatus should remain 0."""
        from pytest_leela.plugin import LeelaPlugin
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
            patch("pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        # Verify exitstatus remained 0
        assert session.exitstatus == 0

    def it_registers_leela_html_option():
        """--leela-html should be registered as a plugin option."""
        parser = MagicMock()
        group = MagicMock()
        parser.getgroup.return_value = group

        pytest_addoption(parser)

        # Collect all addoption calls and find the --leela-html one
        leela_html_calls = [
            c for c in group.addoption.call_args_list
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
            patch("pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
            patch("pytest_leela.html_report.generate_html_report", mock_generate),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        mock_generate.assert_called_once_with(run_result, "/tmp/report.html")

    def it_does_not_generate_html_report_without_flag():
        """No HTML report when --leela-html is not set."""
        from pytest_leela.plugin import LeelaPlugin
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
            patch("pytest_leela.plugin._find_target_files", return_value=["/fake/mod.py"]),
            patch("pytest_leela.plugin.Engine", mock_engine_cls),
            patch("pytest_leela.plugin.format_terminal_report", return_value=""),
            patch("pytest_leela.html_report.generate_html_report", mock_generate),
        ):
            plugin.pytest_sessionfinish(session, exitstatus=0)

        mock_generate.assert_not_called()
