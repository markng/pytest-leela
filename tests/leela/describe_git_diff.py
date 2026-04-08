"""Tests for pytest_leela.git_diff — parsing unified diffs into changed line maps."""

import os

from pytest_leela.git_diff import _parse_diff_hunks


def _abs(path: str) -> str:
    """Get the absolute path for a relative path, matching _parse_diff_hunks behavior."""
    return os.path.abspath(path)


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


def describe_changed_files():
    def context_test_file_exclusion_with_patterns():
        def it_excludes_conftest_always_regardless_of_patterns():
            """conftest.py must always be excluded, even if patterns would allow it."""
            import tempfile
            from unittest.mock import MagicMock, patch

            with tempfile.TemporaryDirectory() as tmpdir:
                conftest_path = os.path.join(tmpdir, "conftest.py")
                open(conftest_path, "w").close()

                mock_result = MagicMock()
                mock_result.stdout = "conftest.py\n"

                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    with patch(
                        "pytest_leela.git_diff.subprocess.run",
                        return_value=mock_result,
                    ):
                        from pytest_leela.git_diff import changed_files

                        # Even with empty patterns, conftest.py must be excluded
                        result = changed_files("main", test_file_patterns=[])
                finally:
                    os.chdir(old_cwd)

                assert "conftest.py" not in {os.path.basename(f) for f in result}

        def it_excludes_default_test_patterns_when_test_file_patterns_is_none():
            """When test_file_patterns=None, default patterns (test_*.py, *_test.py) apply."""
            import tempfile
            from unittest.mock import MagicMock, patch

            with tempfile.TemporaryDirectory() as tmpdir:
                test_path = os.path.join(tmpdir, "test_foo.py")
                source_path = os.path.join(tmpdir, "app_main.py")
                open(test_path, "w").close()
                open(source_path, "w").close()

                mock_result = MagicMock()
                mock_result.stdout = "test_foo.py\napp_main.py\n"

                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    with patch(
                        "pytest_leela.git_diff.subprocess.run",
                        return_value=mock_result,
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
            import tempfile
            from unittest.mock import MagicMock, patch

            with tempfile.TemporaryDirectory() as tmpdir:
                describe_path = os.path.join(tmpdir, "describe_utils.py")
                app_path = os.path.join(tmpdir, "app.py")
                open(describe_path, "w").close()
                open(app_path, "w").close()

                mock_result = MagicMock()
                mock_result.stdout = "describe_utils.py\napp.py\n"

                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    with patch(
                        "pytest_leela.git_diff.subprocess.run",
                        return_value=mock_result,
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
        import tempfile
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create real files with exact names so they exist on disk
            test_path = os.path.join(tmpdir, "test_foo.py")
            source_path = os.path.join(tmpdir, "app_main.py")
            conftest_path = os.path.join(tmpdir, "conftest.py")

            open(test_path, "w").close()
            open(source_path, "w").close()
            open(conftest_path, "w").close()

            mock_result = MagicMock()
            mock_result.stdout = "test_foo.py\napp_main.py\nconftest.py\n"

            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with patch(
                    "pytest_leela.git_diff.subprocess.run",
                    return_value=mock_result,
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
        import tempfile
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            path1 = os.path.join(tmpdir, "app.py")
            path2 = os.path.join(tmpdir, "utils.py")

            open(path1, "w").close()
            open(path2, "w").close()

            mock_result = MagicMock()
            mock_result.stdout = "app.py\nutils.py\n"

            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with patch(
                    "pytest_leela.git_diff.subprocess.run",
                    return_value=mock_result,
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
        from unittest.mock import patch
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
        import tempfile
        from unittest.mock import MagicMock, patch
        from pytest_leela.git_diff import changed_files

        # Create a real temp .py file so os.path.exists passes
        with tempfile.TemporaryDirectory() as tmpdir:
            f = tempfile.NamedTemporaryFile(dir=tmpdir, suffix=".py", delete=False)
            tmp_path = f.name
            basename = os.path.basename(tmp_path)
            f.close()

            try:
                mock_result = MagicMock()
                mock_result.stdout = basename + "\n"

                old_cwd = os.getcwd()
                os.chdir(tmpdir)
                try:
                    with patch(
                        "pytest_leela.git_diff.subprocess.run",
                        return_value=mock_result,
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


def describe_changed_lines():
    def it_returns_dict_type():
        """changed_lines must return a dict, not None."""
        from unittest.mock import patch
        from pytest_leela.git_diff import changed_lines

        with patch(
            "pytest_leela.git_diff.subprocess.run", side_effect=FileNotFoundError
        ):
            result = changed_lines("main")
        assert isinstance(result, dict)
        assert result == {}

    def it_returns_parsed_hunks_on_success():
        """changed_lines must return parsed data, not None (line 61 guard).

        Mocking subprocess.run to return valid unified diff output exercises
        the `return _parse_diff_hunks(result.stdout)` path at line 61.
        """
        from unittest.mock import MagicMock, patch
        from pytest_leela.git_diff import changed_lines

        diff_output = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -10,0 +10,2 @@\n"
            "+new_line_1\n"
            "+new_line_2\n"
        )
        mock_result = MagicMock()
        mock_result.stdout = diff_output

        with patch("pytest_leela.git_diff.subprocess.run", return_value=mock_result):
            result = changed_lines("main")

        assert result is not None
        assert isinstance(result, dict)
        assert len(result) > 0
        # Verify actual parsed data is present
        abs_foo = os.path.abspath("foo.py")
        assert abs_foo in result
        assert 10 in result[abs_foo]
        assert 11 in result[abs_foo]
