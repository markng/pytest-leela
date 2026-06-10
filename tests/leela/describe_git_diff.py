"""Tests for pytest_leela.git_diff — parsing unified diffs into changed line maps."""

import os
import tempfile
from unittest.mock import MagicMock, call, patch

from pytest_leela.git_diff import _parse_diff_hunks


def _abs(path: str) -> str:
    """Get the absolute path for a relative path, matching _parse_diff_hunks behavior."""
    return os.path.abspath(path)


def _mock_repo_root(tmpdir: str):
    """Patch _get_repo_root to return tmpdir (simulates a single-project repo)."""
    return patch("pytest_leela.git_diff._get_repo_root", return_value=tmpdir)


def _make_run_mock(stdout: str) -> MagicMock:
    """Return a MagicMock for subprocess.run whose .stdout is *stdout*."""
    m = MagicMock()
    m.stdout = stdout
    return m


# ---------------------------------------------------------------------------
# _parse_diff_hunks — pure parser, no subprocess calls
# ---------------------------------------------------------------------------


def describe_parse_diff_hunks():
    def it_parses_single_line_addition():
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -0,0 +5 @@\n"
            "+new_line = True\n"
        )
        result = _parse_diff_hunks(diff)
        assert _abs("foo.py") in result
        assert result[_abs("foo.py")] == {5}

    def it_parses_multi_line_hunk():
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -10,3 +10,5 @@\n"
            "+line1\n"
            "+line2\n"
            "+line3\n"
            "+line4\n"
            "+line5\n"
        )
        result = _parse_diff_hunks(diff)
        assert result[_abs("foo.py")] == {10, 11, 12, 13, 14}

    def it_parses_multiple_hunks_in_one_file():
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -5,0 +5,2 @@\n"
            "+a\n"
            "+b\n"
            "@@ -20,0 +22,3 @@\n"
            "+c\n"
            "+d\n"
            "+e\n"
        )
        result = _parse_diff_hunks(diff)
        expected = {5, 6, 22, 23, 24}
        assert result[_abs("foo.py")] == expected

    def it_parses_multiple_files():
        diff = (
            "diff --git a/alpha.py b/alpha.py\n"
            "--- a/alpha.py\n"
            "+++ b/alpha.py\n"
            "@@ -1,0 +1,2 @@\n"
            "+x\n"
            "+y\n"
            "diff --git a/beta.py b/beta.py\n"
            "--- a/beta.py\n"
            "+++ b/beta.py\n"
            "@@ -10,0 +10 @@\n"
            "+z\n"
        )
        result = _parse_diff_hunks(diff)
        assert _abs("alpha.py") in result
        assert _abs("beta.py") in result
        assert result[_abs("alpha.py")] == {1, 2}
        assert result[_abs("beta.py")] == {10}

    def it_ignores_non_python_files():
        diff = (
            "diff --git a/readme.md b/readme.md\n"
            "--- a/readme.md\n"
            "+++ b/readme.md\n"
            "@@ -1,0 +1,3 @@\n"
            "+# Title\n"
            "+some text\n"
            "+more text\n"
        )
        result = _parse_diff_hunks(diff)
        assert result == {}

    def it_returns_empty_for_empty_diff():
        result = _parse_diff_hunks("")
        assert result == {}

    def it_handles_mixed_python_and_non_python_files():
        diff = (
            "diff --git a/config.yaml b/config.yaml\n"
            "--- a/config.yaml\n"
            "+++ b/config.yaml\n"
            "@@ -1,0 +1,2 @@\n"
            "+key: value\n"
            "+other: data\n"
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -7,0 +7,1 @@\n"
            "+import os\n"
        )
        result = _parse_diff_hunks(diff)
        # Only the .py file should be included
        assert len(result) == 1
        assert _abs("app.py") in result
        assert result[_abs("app.py")] == {7}

    def it_returns_dict_not_none():
        """Return value must be a dict, not None."""
        result = _parse_diff_hunks("")
        assert isinstance(result, dict)

    def it_returns_set_of_ints_per_file():
        diff = "+++ b/foo.py\n@@ -0,0 +1,2 @@\n+a\n+b\n"
        result = _parse_diff_hunks(diff)
        for key, val in result.items():
            assert isinstance(val, set)
            for item in val:
                assert isinstance(item, int)

    def it_resolves_paths_relative_to_repo_root_when_provided():
        """When repo_root is given, +++ b/ paths are resolved against it."""
        with tempfile.TemporaryDirectory() as repo_root:
            diff = (
                "diff --git a/pkg/app.py b/pkg/app.py\n"
                "--- a/pkg/app.py\n"
                "+++ b/pkg/app.py\n"
                "@@ -1,0 +1,1 @@\n"
                "+x = 1\n"
            )
            result = _parse_diff_hunks(diff, repo_root=repo_root)
            expected_path = os.path.normpath(os.path.join(repo_root, "pkg/app.py"))
            assert expected_path in result
            assert result[expected_path] == {1}

    def it_falls_back_to_abspath_when_repo_root_is_none():
        """Without repo_root, paths are resolved relative to cwd (original behaviour)."""
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -0,0 +3 @@\n"
            "+z = 3\n"
        )
        result = _parse_diff_hunks(diff, repo_root=None)
        assert os.path.abspath("foo.py") in result


# ---------------------------------------------------------------------------
# changed_files
# ---------------------------------------------------------------------------


def describe_changed_files():
    def context_test_file_exclusion_with_patterns():
        def it_excludes_conftest_always_regardless_of_patterns():
            """conftest.py must always be excluded, even if patterns would allow it."""
            with tempfile.TemporaryDirectory() as tmpdir:
                conftest_path = os.path.join(tmpdir, "conftest.py")
                open(conftest_path, "w").close()

                names_mock = _make_run_mock("conftest.py\n")

                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    with _mock_repo_root(tmpdir), patch(
                        "pytest_leela.git_diff.subprocess.run",
                        return_value=names_mock,
                    ):
                        from pytest_leela.git_diff import changed_files

                        # Even with empty patterns, conftest.py must be excluded
                        result = changed_files("main", test_file_patterns=[])
                finally:
                    os.chdir(old_cwd)

                assert "conftest.py" not in {os.path.basename(f) for f in result}

        def it_excludes_default_test_patterns_when_test_file_patterns_is_none():
            """When test_file_patterns=None, default patterns (test_*.py, *_test.py) apply."""
            with tempfile.TemporaryDirectory() as tmpdir:
                test_path = os.path.join(tmpdir, "test_foo.py")
                source_path = os.path.join(tmpdir, "app_main.py")
                open(test_path, "w").close()
                open(source_path, "w").close()

                names_mock = _make_run_mock("test_foo.py\napp_main.py\n")

                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    with _mock_repo_root(tmpdir), patch(
                        "pytest_leela.git_diff.subprocess.run",
                        return_value=names_mock,
                    ):
                        from pytest_leela.git_diff import changed_files

                        result = changed_files("main", test_file_patterns=None)
                finally:
                    os.chdir(old_cwd)

                basenames = {os.path.basename(f) for f in result}
                assert "test_foo.py" not in basenames
                assert "app_main.py" in basenames

        def it_excludes_custom_describe_pattern():
            """Custom patterns like describe_*.py must be excluded when specified."""
            with tempfile.TemporaryDirectory() as tmpdir:
                describe_path = os.path.join(tmpdir, "describe_utils.py")
                app_path = os.path.join(tmpdir, "app.py")
                open(describe_path, "w").close()
                open(app_path, "w").close()

                names_mock = _make_run_mock("describe_utils.py\napp.py\n")

                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    with _mock_repo_root(tmpdir), patch(
                        "pytest_leela.git_diff.subprocess.run",
                        return_value=names_mock,
                    ):
                        from pytest_leela.git_diff import changed_files

                        result = changed_files(
                            "main", test_file_patterns=["describe_*.py"]
                        )
                finally:
                    os.chdir(old_cwd)

                basenames = {os.path.basename(f) for f in result}
                assert "describe_utils.py" not in basenames
                assert "app.py" in basenames

    def it_excludes_test_files():
        """changed_files must filter out test files from git output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create real files with exact names so they exist on disk
            open(os.path.join(tmpdir, "test_foo.py"), "w").close()
            open(os.path.join(tmpdir, "app_main.py"), "w").close()
            open(os.path.join(tmpdir, "conftest.py"), "w").close()

            names_mock = _make_run_mock("test_foo.py\napp_main.py\nconftest.py\n")

            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with _mock_repo_root(tmpdir), patch(
                    "pytest_leela.git_diff.subprocess.run",
                    return_value=names_mock,
                ):
                    from pytest_leela.git_diff import changed_files

                    result = changed_files("main")
            finally:
                os.chdir(old_cwd)

            basenames = {os.path.basename(f) for f in result}
            assert "test_foo.py" not in basenames, (
                f"test file should be excluded, got {basenames}"
            )
            assert "conftest.py" not in basenames, (
                f"conftest.py should be excluded, got {basenames}"
            )
            assert "app_main.py" in basenames, (
                f"source file should be included, got {basenames}"
            )

    def it_includes_non_test_files():
        """changed_files must pass through source files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "app.py"), "w").close()
            open(os.path.join(tmpdir, "utils.py"), "w").close()

            names_mock = _make_run_mock("app.py\nutils.py\n")

            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with _mock_repo_root(tmpdir), patch(
                    "pytest_leela.git_diff.subprocess.run",
                    return_value=names_mock,
                ):
                    from pytest_leela.git_diff import changed_files

                    result = changed_files("main")
            finally:
                os.chdir(old_cwd)

            basenames = {os.path.basename(f) for f in result}
            assert "app.py" in basenames
            assert "utils.py" in basenames

    def it_returns_list_type():
        """changed_files must return a list, not None."""
        from pytest_leela.git_diff import changed_files

        with patch(
            "pytest_leela.git_diff.subprocess.run", side_effect=FileNotFoundError
        ):
            result = changed_files("main")
        assert isinstance(result, list)
        assert result == []

    def it_returns_actual_files_on_success():
        """changed_files must return the file list, not None (line 38 guard).

        Mocking subprocess.run to return valid git output with an existing
        .py file ensures we exercise the `return files` path at line 38.
        """
        from pytest_leela.git_diff import changed_files

        # Create a real temp .py file so os.path.exists passes
        with tempfile.TemporaryDirectory() as tmpdir:
            f = tempfile.NamedTemporaryFile(dir=tmpdir, suffix=".py", delete=False)
            tmp_path = f.name
            basename = os.path.basename(tmp_path)
            f.close()

            try:
                names_mock = _make_run_mock(basename + "\n")

                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    with _mock_repo_root(tmpdir), patch(
                        "pytest_leela.git_diff.subprocess.run",
                        return_value=names_mock,
                    ):
                        result = changed_files("main")
                finally:
                    os.chdir(old_cwd)

                assert result is not None
                assert isinstance(result, list)
                assert len(result) == 1
                assert os.path.basename(result[0]) == basename
            finally:
                os.unlink(tmp_path)

    def it_unions_committed_and_working_tree_names():
        """changed_files returns the union of committed-range and working-tree names.

        This is the core fix for --diff HEAD: the three-dot range yields nothing
        when HEAD has no commits ahead of itself, while the two-dot range
        (working tree vs ref) captures uncommitted changes.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "committed.py"), "w").close()
            open(os.path.join(tmpdir, "staged.py"), "w").close()

            # Call sequence (subprocess.run is called twice by changed_files):
            # 1st call → committed-range names (base...HEAD)
            # 2nd call → working-tree names (base, two-dot)
            committed_mock = _make_run_mock("committed.py\n")
            working_mock = _make_run_mock("staged.py\n")

            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with _mock_repo_root(tmpdir), patch(
                    "pytest_leela.git_diff.subprocess.run",
                    side_effect=[committed_mock, working_mock],
                ):
                    from pytest_leela.git_diff import changed_files

                    result = changed_files("main")
            finally:
                os.chdir(old_cwd)

            basenames = {os.path.basename(f) for f in result}
            assert "committed.py" in basenames
            assert "staged.py" in basenames

    def it_resolves_subdir_paths_against_repo_root():
        """Paths from git (repo-root-relative) are resolved via repo_root, not cwd.

        Monorepo scenario: cwd is <repo>/services/api but git reports paths as
        services/api/models.py. Without normalisation os.path.abspath would
        produce <repo>/services/api/services/api/models.py (wrong).
        """
        with tempfile.TemporaryDirectory() as repo_root:
            # Simulate a subdirectory inside the repo
            subdir = os.path.join(repo_root, "services", "api")
            os.makedirs(subdir, exist_ok=True)
            models_path = os.path.join(subdir, "models.py")
            open(models_path, "w").close()

            # git diff --name-only returns repo-root-relative path
            names_mock = _make_run_mock("services/api/models.py\n")

            old_cwd = os.getcwd()
            os.chdir(subdir)  # cwd is a subdirectory
            try:
                with _mock_repo_root(repo_root), patch(
                    "pytest_leela.git_diff.subprocess.run",
                    return_value=names_mock,
                ):
                    from pytest_leela.git_diff import changed_files

                    result = changed_files("main")
            finally:
                os.chdir(old_cwd)

            assert len(result) == 1
            assert result[0] == models_path


# ---------------------------------------------------------------------------
# changed_lines
# ---------------------------------------------------------------------------


def describe_changed_lines():
    def it_returns_dict_type():
        """changed_lines must return a dict, not None."""
        from pytest_leela.git_diff import changed_lines

        with patch(
            "pytest_leela.git_diff.subprocess.run", side_effect=FileNotFoundError
        ):
            result = changed_lines("main")
        assert isinstance(result, dict)
        assert result == {}

    def it_returns_parsed_hunks_on_success():
        """changed_lines must return parsed data, not None.

        Mocking subprocess.run to return valid unified diff output exercises
        the merge-and-return path.
        """
        from pytest_leela.git_diff import changed_lines

        diff_output = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -10,0 +10,2 @@\n"
            "+new_line_1\n"
            "+new_line_2\n"
        )
        diff_mock = _make_run_mock(diff_output)
        empty_mock = _make_run_mock("")

        with tempfile.TemporaryDirectory() as repo_root:
            with _mock_repo_root(repo_root), patch(
                "pytest_leela.git_diff.subprocess.run",
                side_effect=[diff_mock, empty_mock],
            ):
                result = changed_lines("main")

        assert result is not None
        assert isinstance(result, dict)
        assert len(result) > 0
        # Path is repo-root-relative, resolved via repo_root
        expected_path = os.path.normpath(os.path.join(repo_root, "foo.py"))
        assert expected_path in result
        assert 10 in result[expected_path]
        assert 11 in result[expected_path]

    def it_unions_committed_and_working_tree_lines():
        """changed_lines merges line sets from both committed range and working tree.

        Scenario: line 5 changed in a committed commit (base...HEAD), and
        line 20 changed in an uncommitted edit (working tree, base).
        Both must appear in the merged result.
        """
        from pytest_leela.git_diff import changed_lines

        committed_diff = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -4,0 +5 @@\n"
            "+committed_change = True\n"
        )
        working_diff = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -19,0 +20 @@\n"
            "+working_change = True\n"
        )

        committed_mock = _make_run_mock(committed_diff)
        working_mock = _make_run_mock(working_diff)

        with tempfile.TemporaryDirectory() as repo_root:
            with _mock_repo_root(repo_root), patch(
                "pytest_leela.git_diff.subprocess.run",
                side_effect=[committed_mock, working_mock],
            ):
                result = changed_lines("main")

        expected_path = os.path.normpath(os.path.join(repo_root, "app.py"))
        assert expected_path in result
        assert 5 in result[expected_path], "committed line missing from union"
        assert 20 in result[expected_path], "working-tree line missing from union"

    def it_handles_head_ref_with_only_working_tree_changes():
        """--diff HEAD: three-dot range is empty; two-dot captures uncommitted lines.

        This is the hollow-pass regression: `git diff HEAD...HEAD` always returns
        empty, so 0 mutants and a silent exit 0.  The two-dot diff
        (`git diff -U0 HEAD`) returns working-tree changes.
        """
        from pytest_leela.git_diff import changed_lines

        working_diff = (
            "diff --git a/service.py b/service.py\n"
            "--- a/service.py\n"
            "+++ b/service.py\n"
            "@@ -7,0 +8 @@\n"
            "+return value * 2\n"
        )
        empty_committed = _make_run_mock("")
        working_mock = _make_run_mock(working_diff)

        with tempfile.TemporaryDirectory() as repo_root:
            with _mock_repo_root(repo_root), patch(
                "pytest_leela.git_diff.subprocess.run",
                side_effect=[empty_committed, working_mock],
            ):
                result = changed_lines("HEAD")

        expected_path = os.path.normpath(os.path.join(repo_root, "service.py"))
        assert expected_path in result
        assert 8 in result[expected_path], (
            "working-tree line should be present when committed diff is empty"
        )

    def it_resolves_monorepo_subdirectory_paths():
        """Line paths from git are resolved against repo root, not pytest cwd.

        When pytest rootdir is <repo>/mylib/ but git reports paths as
        mylib/models.py, the resolved absolute path must be
        <repo>/mylib/models.py — not <repo>/mylib/mylib/models.py.
        """
        from pytest_leela.git_diff import changed_lines

        diff_output = (
            "diff --git a/mylib/models.py b/mylib/models.py\n"
            "--- a/mylib/models.py\n"
            "+++ b/mylib/models.py\n"
            "@@ -1,0 +3 @@\n"
            "+new_field = True\n"
        )
        diff_mock = _make_run_mock(diff_output)
        empty_mock = _make_run_mock("")

        with tempfile.TemporaryDirectory() as repo_root:
            subdir = os.path.join(repo_root, "mylib")
            os.makedirs(subdir, exist_ok=True)

            old_cwd = os.getcwd()
            os.chdir(subdir)  # cwd is a subdirectory of the repo
            try:
                with _mock_repo_root(repo_root), patch(
                    "pytest_leela.git_diff.subprocess.run",
                    side_effect=[diff_mock, empty_mock],
                ):
                    result = changed_lines("main")
            finally:
                os.chdir(old_cwd)

        expected_path = os.path.normpath(os.path.join(repo_root, "mylib/models.py"))
        # Must NOT be os.path.join(subdir, "mylib/models.py")
        wrong_path = os.path.normpath(os.path.join(subdir, "mylib/models.py"))
        assert expected_path in result, (
            f"expected {expected_path} in result, got {list(result.keys())}"
        )
        assert wrong_path not in result, (
            f"wrong double-subdir path {wrong_path} must not appear"
        )


# ---------------------------------------------------------------------------
# Zero-mutant warning (output layer)
# ---------------------------------------------------------------------------


def describe_zero_mutant_warning():
    def it_warns_when_diff_is_active_and_zero_mutants_tested():
        """format_terminal_report emits a prominent WARNING line when --diff
        produced 0 mutants, guarding against the silent hollow-pass failure."""
        from pytest_leela.models import RunResult
        from pytest_leela.output import format_terminal_report

        result = RunResult(
            target_files=["app.py"],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.1,
            diff_base="HEAD",
        )
        report = format_terminal_report(result)
        assert "WARNING" in report
        assert "HEAD" in report
        assert "0 mutants" in report

    def it_does_not_warn_when_diff_is_not_active():
        """No WARNING line when diff_base is None (full run with zero mutants)."""
        from pytest_leela.models import RunResult
        from pytest_leela.output import format_terminal_report

        result = RunResult(
            target_files=[],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.1,
            diff_base=None,
        )
        report = format_terminal_report(result)
        assert "WARNING" not in report

    def it_does_not_warn_when_diff_active_but_mutants_were_found():
        """No WARNING when --diff is active and mutants were actually generated."""
        import dataclasses

        from pytest_leela.models import MutantResult, Mutant, MutationPoint, RunResult
        from pytest_leela.output import format_terminal_report

        point = MutationPoint(
            file_path="/tmp/app.py",
            module_name="app",
            lineno=5,
            col_offset=0,
            node_type="BinOp",
            original_op="Add",
            inferred_type=None,
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
        mr = MutantResult(
            mutant=mutant,
            killed=True,
            tests_run=1,
            killing_test="test_foo",
            time_seconds=0.01,
        )
        result = RunResult(
            target_files=["/tmp/app.py"],
            total_mutants=1,
            mutants_tested=1,
            mutants_pruned=0,
            results=[mr],
            wall_time_seconds=0.5,
            diff_base="main",
        )
        report = format_terminal_report(result)
        assert "WARNING" not in report
