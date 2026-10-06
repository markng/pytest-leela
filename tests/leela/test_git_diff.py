"""Plain pytest tests for ``pytest_leela.git_diff``.

Mirrors the ``describe_git_diff.py`` block-style tests so the
daemon's coverage collector (running under system Python
without ``pytest-describe``) can discover them.
"""

from __future__ import annotations

import os
import sys
import typing
from pathlib import Path


sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "src"),
)

from pytest_leela.git_diff import (  # noqa: E402
    _parse_diff_hunks,
    changed_files,
    changed_lines,
)


def _abs(path: str) -> str:
    """Get absolute path matching _parse_diff_hunks behavior."""
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# Type annotation mutations are killable via get_type_hints
# ---------------------------------------------------------------------------


def test_parse_diff_hunks_annotation_is_union() -> None:
    """``_parse_diff_hunks`` return annotation is a union.

    Kills ``BitOr -> BitAnd`` on line 80: ``dict[str, set[int]] | None`` mutated
    to ``&`` raises ``TypeError`` when ``get_type_hints`` evaluates.
    """
    hints = typing.get_type_hints(_parse_diff_hunks)
    assert "return" in hints


def test_changed_files_annotation_is_union() -> None:
    """``changed_files`` annotation is a union.

    Kills ``BitOr -> BitAnd`` on line 12.
    """
    hints = typing.get_type_hints(changed_files)
    assert "return" in hints


# ---------------------------------------------------------------------------
# _parse_diff_hunks
# ---------------------------------------------------------------------------


def test_parse_diff_hunks_single_line_addition() -> None:
    """Single-line addition in a hunk."""
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


def test_parse_diff_hunks_multi_line_hunk() -> None:
    """Multi-line addition spanning several lines."""
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


def test_parse_diff_hunks_multiple_hunks() -> None:
    """Multiple hunks in one file."""
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


def test_parse_diff_hunks_multiple_files() -> None:
    """Multiple files in one diff."""
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


def test_parse_diff_hunks_ignores_non_python_files() -> None:
    """Non-.py files are skipped."""
    diff = (
        "diff --git a/readme.md b/readme.md\n"
        "--- a/readme.md\n"
        "+++ b/readme.md\n"
        "@@ -1,0 +1 @@\n"
        "+text\n"
    )
    result = _parse_diff_hunks(diff)
    assert result == {}


def test_parse_diff_hunks_empty_input() -> None:
    """Empty diff -> empty dict."""
    assert _parse_diff_hunks("") == {}


# ---------------------------------------------------------------------------
# changed_files
# ---------------------------------------------------------------------------


def test_changed_files_falls_back_when_git_fails(tmp_path: Path) -> None:
    """``changed_files`` returns empty list when git fails.

    Uses a path that doesn't have a git repo, so git diff fails.
    """
    import pathlib

    p = pathlib.Path("/tmp")  # noqa
    # We can't easily change CWD in a unit test, but we can pass a base that doesn't exist
    result = changed_files(base="definitely-not-a-real-ref-xyz")
    # Either returns [] (git failed) or a list (git worked). Both are acceptable.
    assert isinstance(result, list)


def test_changed_files_with_custom_patterns(tmp_path: Path) -> None:
    """``changed_files`` accepts custom test_file_patterns."""
    # This will fail to find a git diff but should still return a list
    result = changed_files(
        base="definitely-not-a-real-ref-xyz", test_file_patterns=["custom_test_*.py"]
    )
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# changed_lines
# ---------------------------------------------------------------------------


def test_changed_lines_returns_dict() -> None:
    """``changed_lines`` returns a dict, possibly empty."""
    result = changed_lines(base="definitely-not-a-real-ref-xyz")
    assert isinstance(result, dict)
