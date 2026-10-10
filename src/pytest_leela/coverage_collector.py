"""Standalone entry point for subprocess-based coverage collection.

Invoked by ``coverage_tracker.collect_coverage_subprocess`` from a
fresh Python interpreter (not the one hosting the daemon) so pytest
discovers its rootdir from ``cwd`` and finds the project's
``pyproject.toml`` / ``conftest.py`` instead of inheriting the
outer interpreter's config.

Usage:
    python -m pytest_leela.coverage_collector \
        --output /tmp/out.json \
        --target src/calc.py \
        --test tests/test_sub.py

Writes a JSON document of the form:
    {
      "line_to_tests": {"src/calc.py:5": ["tests/test_sub.py::test_sub"]},
      "test_times": {"tests/test_sub.py::test_sub": 0.012}
    }
"""

import argparse
import json
from pathlib import Path

import pytest

from pytest_leela.coverage_tracker import CoveragePlugin


class _DiscoveryCheck:
    """Do not silently ignore describe suites in the wrong dev environment."""

    def pytest_collect_file(self, file_path: Path, parent: pytest.Collector) -> None:
        if file_path.name.startswith("describe_") and file_path.suffix == ".py":
            plugins = parent.config.pluginmanager.get_plugins()
            if not any(
                getattr(plugin, "__name__", "").startswith("pytest_describe")
                for plugin in plugins
            ):
                raise pytest.UsageError(
                    "describe_* suites require pytest-describe. Run python -m "
                    "pytest_leela.daemon from the project's dev environment; "
                    "install its configured dev dependencies and enable plugin autoload."
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the JSON coverage document.",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Source file to track (repeat the flag for each file).",
    )
    parser.add_argument(
        "--test",
        action="append",
        default=[],
        help="Test node id (repeat the flag for each id).",
    )
    args = parser.parse_args()

    plugin = CoveragePlugin({str(Path(p).resolve()) for p in args.target})
    # Fresh interpreter: retain normal project plugin discovery, but never
    # recursively run mutation testing from project addopts.
    status = pytest.main(
        ["--override-ini=addopts=", "-p", "no:leela", "-q", *args.test],
        plugins=[plugin, _DiscoveryCheck()],
    )
    if status not in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
        return int(status)
    cov = plugin.coverage_map
    cov.test_times = plugin.test_times

    line_to_tests: dict[str, list[str]] = {}
    for (file_path, lineno), tests in cov.line_to_tests.items():
        key = f"{file_path}:{lineno}"
        line_to_tests[key] = sorted(tests)

    payload = {
        "line_to_tests": line_to_tests,
        "test_times": cov.test_times,
    }

    with open(args.output, "w") as f:
        json.dump(payload, f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
