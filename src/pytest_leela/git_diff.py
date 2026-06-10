"""Parse git diff to find changed files and lines."""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess


def _get_repo_root() -> str | None:
    """Return the absolute path of the git repository root, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _run_git_diff_names(args: list[str]) -> str | None:
    """Run `git diff --name-only --diff-filter=ACMR <args>` and return stdout, or None on error."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR"] + args,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _run_git_diff_hunks(args: list[str]) -> str | None:
    """Run `git diff -U0 <args>` and return stdout, or None on error."""
    try:
        result = subprocess.run(
            ["git", "diff", "-U0"] + args,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def changed_files(
    base: str = "main", test_file_patterns: list[str] | None = None
) -> list[str]:
    """Get list of Python files changed since the base ref.

    Returns the union of files changed in the committed range (``base...HEAD``)
    and files changed in the working tree / index (``base``).  This ensures that
    uncommitted changes are included when ``base`` is ``HEAD`` or any other ref
    that the working tree diverges from.

    Paths returned by git are repo-root-relative; they are resolved against the
    git repository root so that the caller receives correct absolute paths even
    when pytest's rootdir (``cwd``) is a subdirectory of the repo (monorepo).
    """
    repo_root = _get_repo_root()

    # Collect names from both committed range and working tree/index
    raw_names: set[str] = set()

    committed = _run_git_diff_names([f"{base}...HEAD"])
    if committed is not None:
        raw_names.update(committed.strip().splitlines())

    working_tree = _run_git_diff_names([base])
    if working_tree is not None:
        raw_names.update(working_tree.strip().splitlines())

    if not raw_names and committed is None and working_tree is None:
        return []

    patterns = (
        test_file_patterns
        if test_file_patterns is not None
        else ["test_*.py", "*_test.py"]
    )
    files = []
    for name in raw_names:
        name = name.strip()
        if not name.endswith(".py"):
            continue
        # Resolve repo-root-relative path to absolute; fall back to cwd-relative
        if repo_root is not None and not os.path.isabs(name):
            abs_path = os.path.normpath(os.path.join(repo_root, name))
        else:
            abs_path = os.path.abspath(name)
        basename = os.path.basename(abs_path)
        if (
            os.path.exists(abs_path)
            and basename != "conftest.py"
            and not any(fnmatch.fnmatch(basename, p) for p in patterns)
        ):
            files.append(abs_path)
    return sorted(files)


def changed_lines(base: str = "main") -> dict[str, set[int]]:
    """Get changed line numbers per file since the base ref.

    Returns the union of line changes from the committed range
    (``base...HEAD``) and from the working tree / index (``base``).  Using
    the union means that uncommitted edits are included even when ``base``
    is ``HEAD``, which would otherwise produce an empty three-dot diff.
    """
    repo_root = _get_repo_root()

    committed_output = _run_git_diff_hunks([f"{base}...HEAD"])
    working_tree_output = _run_git_diff_hunks([base])

    if committed_output is None and working_tree_output is None:
        return {}

    # Merge both diffs: union of all changed lines per file
    merged: dict[str, set[int]] = {}
    for output in (committed_output, working_tree_output):
        if output is None:
            continue
        for file_path, lines in _parse_diff_hunks(output, repo_root=repo_root).items():
            if file_path not in merged:
                merged[file_path] = set()
            merged[file_path].update(lines)
    return merged


def _parse_diff_hunks(
    diff_output: str, repo_root: str | None = None
) -> dict[str, set[int]]:
    """Parse unified diff output to extract changed line numbers.

    ``repo_root`` is used to resolve repo-root-relative paths produced by git
    into absolute paths.  When ``None``, paths are resolved relative to cwd
    (the original behaviour, preserved for callers that pass diff text
    directly without a live git repo).
    """
    file_lines: dict[str, set[int]] = {}
    current_file: str | None = None

    hunk_pattern = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if path.endswith(".py"):
                # Resolve against repo root when available; fall back to cwd
                if repo_root is not None and not os.path.isabs(path):
                    current_file = os.path.normpath(os.path.join(repo_root, path))
                else:
                    current_file = os.path.abspath(path)
                if current_file not in file_lines:
                    file_lines[current_file] = set()
            else:
                current_file = None
        elif line.startswith("@@") and current_file is not None:
            match = hunk_pattern.match(line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2)) if match.group(2) else 1
                for lineno in range(start, start + count):
                    file_lines[current_file].add(lineno)

    return file_lines
