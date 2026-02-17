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
        diff = (
            "+++ b/foo.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+a\n+b\n"
        )
        result = _parse_diff_hunks(diff)
        for key, val in result.items():
            assert isinstance(val, set)
            for item in val:
                assert isinstance(item, int)


def describe_changed_files():
    def it_returns_list_type():
        """changed_files must return a list, not None."""
        from unittest.mock import patch
        from pytest_leela.git_diff import changed_files

        with patch("pytest_leela.git_diff.subprocess.run", side_effect=FileNotFoundError):
            result = changed_files("main")
        assert isinstance(result, list)
        assert result == []


def describe_changed_lines():
    def it_returns_dict_type():
        """changed_lines must return a dict, not None."""
        from unittest.mock import patch
        from pytest_leela.git_diff import changed_lines

        with patch("pytest_leela.git_diff.subprocess.run", side_effect=FileNotFoundError):
            result = changed_lines("main")
        assert isinstance(result, dict)
        assert result == {}
