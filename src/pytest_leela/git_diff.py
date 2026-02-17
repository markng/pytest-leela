"""Parse git diff to find changed files and lines."""

from __future__ import annotations

import os
import re
import subprocess


def changed_files(base: str = "main") -> list[str]:
    """Get list of Python files changed since the base ref."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fall back to diff against working tree
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=ACMR", base],
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []

    files = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.endswith(".py"):
            abs_path = os.path.abspath(line)
            if os.path.exists(abs_path):
                files.append(abs_path)
    return files


def changed_lines(base: str = "main") -> dict[str, set[int]]:
    """Get changed line numbers per file since the base ref."""
    try:
        result = subprocess.run(
            ["git", "diff", "-U0", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            result = subprocess.run(
                ["git", "diff", "-U0", base],
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return {}

    return _parse_diff_hunks(result.stdout)


def _parse_diff_hunks(diff_output: str) -> dict[str, set[int]]:
    """Parse unified diff output to extract changed line numbers."""
    file_lines: dict[str, set[int]] = {}
    current_file: str | None = None

    hunk_pattern = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if path.endswith(".py"):
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
