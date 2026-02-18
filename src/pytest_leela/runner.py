"""Execute tests against a single mutant in-process."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import time
from typing import Any

import pytest

from pytest_leela.import_hook import (
    MutatingFinder,
    clear_target_modules,
    install_hook,
    remove_hook,
)
from pytest_leela.models import Mutant, MutantResult

# Prefixes for modules that should never be evicted between mutation runs.
_KEEP_PREFIXES = (
    "pytest_leela.",
    "_pytest",
    "pytest",
    "pluggy",
    "py.",
    "_py",
)


def _clear_framework_caches() -> None:
    """Clear framework-specific caches that may hold references to user modules.

    Frameworks like Django cache view function references (via URL resolver),
    so mutations won't take effect unless these caches are cleared between
    mutant runs.
    """
    # Django URL resolver caches view function references via @functools.cache
    try:
        from django.urls import clear_url_caches
        clear_url_caches()
    except ImportError:
        pass


def _clear_user_modules() -> None:
    """Remove project-local modules (tests + targets) from sys.modules.

    Keeps stdlib, site-packages, and pytest-leela internals intact.
    This forces pytest to reimport test files on every mutation run so
    they pick up the current mutant's code via the import hook.
    """
    cwd = os.getcwd() + os.sep
    to_remove = [
        name for name, mod in sys.modules.items()
        if mod is not None
        and getattr(mod, "__file__", None) is not None
        and mod.__file__.startswith(cwd)
        and not name.startswith(_KEEP_PREFIXES)
    ]
    for name in to_remove:
        sys.modules.pop(name, None)


class _ResultCollector:
    """Minimal pytest plugin to collect test results."""

    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.errors: list[str] = []
        self.total = 0

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when == "call":
            self.total += 1
            if report.passed:
                self.passed.append(report.nodeid)
            elif report.failed:
                self.failed.append(report.nodeid)
        elif report.when in ("setup", "teardown") and report.failed:
            self.errors.append(report.nodeid)


def run_tests_for_mutant(
    mutant: Mutant,
    target_sources: dict[str, str],
    module_to_file: dict[str, str],
    test_ids: list[str] | None = None,
    test_dir: str | None = None,
) -> MutantResult:
    """Run tests against a single mutant, return the result."""
    start = time.monotonic()

    module_names = list(target_sources.keys())

    # Install mutating import hook
    finder = install_hook(target_sources, mutant, module_to_file)

    # Clear target modules by name (they may lack __file__ when loaded
    # through the mutating import hook) and test modules by file path
    # (they cache direct references to target functions via
    # ``from target.X import func``).
    clear_target_modules(module_names)
    _clear_user_modules()
    _clear_framework_caches()

    try:
        collector = _ResultCollector()

        # Build pytest args — disable leela plugin to prevent recursion
        args: list[str] = [
            "--tb=no", "-q", "--no-header", "-x",
            "--override-ini=addopts=",
            "-p", "no:leela",
            "-p", "no:leela-benchmark",
            "--capture=sys",
        ]

        if test_ids:
            args.extend(test_ids)
        elif test_dir:
            args.append(test_dir)

        # Snapshot sys.meta_path and sys.modules right BEFORE the inner
        # pytest.main() call.  Each inner run adds its own hooks
        # (AssertionRewritingHook, etc.) and imports modules.  Without
        # restoring after each run, hooks accumulate across 300+ mutant
        # runs and break test collection/execution.
        saved_meta_path = sys.meta_path[:]
        saved_module_keys = set(sys.modules.keys())

        # Run pytest in-process (suppress noisy output)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                pytest.main(args, plugins=[collector])
        except Exception:
            # A mutation that crashes the test runner counts as killed
            elapsed = time.monotonic() - start
            return MutantResult(
                mutant=mutant,
                killed=True,
                tests_run=collector.total,
                killing_test="<crashed>",
                time_seconds=elapsed,
            )
        finally:
            # Restore meta_path: removes hooks that inner pytest.main()
            # added (AssertionRewritingHook etc.).  The saved snapshot
            # includes our MutatingFinder + the outer session's hooks,
            # so outer state is preserved.
            sys.meta_path[:] = saved_meta_path

            # Remove CWD-local modules added by the inner run.  We must
            # NOT evict stdlib/frozen modules (ntpath, posixpath etc.)
            # as they are needed by pytest teardown.
            cwd_prefix = os.getcwd() + os.sep
            for key in list(sys.modules.keys()):
                if key not in saved_module_keys:
                    mod = sys.modules.get(key)
                    mod_file = getattr(mod, "__file__", None) if mod is not None else None
                    if mod_file is not None and mod_file.startswith(cwd_prefix):
                        sys.modules.pop(key, None)

        killed = len(collector.failed) > 0 or len(collector.errors) > 0
        killing_test = None
        if collector.failed:
            killing_test = collector.failed[0]
        elif collector.errors:
            killing_test = collector.errors[0]

        elapsed = time.monotonic() - start

        return MutantResult(
            mutant=mutant,
            killed=killed,
            tests_run=collector.total,
            killing_test=killing_test,
            time_seconds=elapsed,
        )
    finally:
        # Cleanup: remove hook and clear cached modules
        remove_hook(finder)
        clear_target_modules(module_names)
        _clear_user_modules()
        _clear_framework_caches()

        # Safety net: remove any stale MutatingFinders left on
        # sys.meta_path from crashed previous runs.
        sys.meta_path[:] = [
            f for f in sys.meta_path
            if not isinstance(f, MutatingFinder)
        ]
