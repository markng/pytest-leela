"""Execute tests against a single mutant in-process."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# On Python <=3.13, this lets an invalid ``BitOr -> BitAnd`` annotation
# mutation fail while the definition is executed. Python 3.14 uses PEP 649
# lazy annotations instead; the annotation-policy regression reads supported
# hints to exercise that failure. See ``pytest_leela.import_hook`` for why
# its ``compile()`` call must also remain unflagged.

import contextlib
import importlib.util
import io
import ntpath  # noqa: F401 — keep in sys.modules (see engine.py comment)
import os
import posixpath  # noqa: F401 — same as ntpath
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Save references to stdlib path modules.  During self-mutation the inner
# pytest.main() may evict these from sys.modules; we need to restore them
# *without* going through the import machinery (which would trigger
# pytest's assertion rewriter → PurePath → import ntpath → recursion).
_STDLIB_PATH_MODULES = {
    "ntpath": sys.modules["ntpath"],
    "posixpath": sys.modules["posixpath"],
}

import pytest  # noqa: E402 — intentionally below the stdlib-path snapshot

from pytest_leela.import_hook import (  # noqa: E402 — see comment above
    MutatingFinder,
    clear_target_modules,
    install_hook,
    remove_hook,
)
from pytest_leela.models import Mutant, MutantResult  # noqa: E402 — see comment above

# Pre-cache Django's clear_url_caches at import time.  Doing the import
# inside _clear_framework_caches() is fragile: during self-mutation the
# import machinery may be in a degraded state (pytest's assertion rewriter
# creates PurePath objects which lazily ``import ntpath`` on Python 3.13,
# causing infinite recursion through find_spec when ntpath is absent from
# sys.modules).  Capturing the reference once at module load avoids the
# problem entirely.
try:
    from django.urls import clear_url_caches as _django_clear_url_caches
except ImportError:
    _django_clear_url_caches = None

# Prefixes for modules that should never be evicted between mutation runs.
_KEEP_PREFIXES = (
    "pytest_leela.",
    "_pytest",
    "pytest",
    "pluggy",
    "py.",
    "_py",
)


def precompute_user_modules() -> frozenset[str]:
    """Scan sys.modules once and return CWD-local, non-KEEP_PREFIXES module names.

    This precomputes the set of user modules so that the mutation loop can
    use targeted O(K) operations instead of scanning all ~500-1000 entries
    in sys.modules on every mutant run.
    """
    cwd = os.getcwd() + os.sep
    return frozenset(
        name
        for name, mod in sys.modules.items()
        if mod is not None
        and (f := getattr(mod, "__file__", None)) is not None
        and f.startswith(cwd)
        and not name.startswith(_KEEP_PREFIXES)
    )


def _clear_user_modules_fast(known_user_modules: frozenset[str]) -> None:
    """Remove only the precomputed set of user modules from sys.modules.

    O(K) where K is the size of the known set, instead of O(N) where N is
    the total number of modules in sys.modules.
    """
    for name in known_user_modules:
        sys.modules.pop(name, None)


def _clear_framework_caches() -> None:
    """Clear framework-specific caches that may hold references to user modules.

    Frameworks like Django cache view function references (via URL resolver),
    so mutations won't take effect unless these caches are cleared between
    mutant runs.
    """
    if _django_clear_url_caches is not None:
        _django_clear_url_caches()


def _pytest_rewrite_tag() -> str:
    """Return the installed pytest assertion-rewrite cache tag (``cpython-3x-pytest-<ver>``)."""
    from _pytest.assertion.rewrite import PYTEST_TAG

    return PYTEST_TAG


def _bytecode_artifacts(source: Path) -> list[Path]:
    """Exact cached-bytecode artifacts derived from *source* by the active loaders.

    Two loaders can cache bytecode for a local source: CPython's regular
    ``.pyc`` (via :func:`importlib.util.cache_from_source`, which honours
    ``sys.pycache_prefix``) and pytest's assertion rewriter (which writes a
    version-tagged ``.<cache_tag>-pytest-<ver>.pyc`` sibling).  Both exact names
    are returned so removal stays bound to *this* origin and never matches a
    sibling module's cache (which lives under a different cache dir, or has a
    different stem).
    """
    cpython_pyc = Path(importlib.util.cache_from_source(str(source)))
    stem = source.name[:-3] if source.name.endswith(".py") else source.stem
    pytest_pyc = cpython_pyc.parent / f"{stem}.{_pytest_rewrite_tag()}.pyc"
    artifacts = []
    for path in (cpython_pyc, pytest_pyc):
        if path.exists():
            artifacts.append(path)
    return artifacts


def _prepare_fresh_dependencies(files: set[Path], prepared: set[Path]) -> None:
    """Remove *files*' exact cached bytecode once per run so imports recompile.

    CPython and pytest's assertion rewriter both reuse a cached ``.pyc`` while
    its recorded ``(int(source_mtime), size)`` still match, so a same-size edit
    inside one integer second — or an edit that leaves the precise source mtime
    unchanged or moves it backward — executes pre-edit bytecode.  The ``.pyc``
    header stores no content hash, so an origin whose current content cannot be
    positively confirmed is not trustworthy: its derived artifacts are removed
    and the next import rebuilds from the live source.  This is scoped to the
    selected tests' local inputs, is idempotent per run via *prepared*, and
    raises on genuine removal failure (e.g. read-only cache) rather than running
    known-stale code.
    """
    for source in files:
        if source in prepared:
            continue
        for artifact in _bytecode_artifacts(source):
            try:
                artifact.unlink()
            except FileNotFoundError:
                continue  # already gone (race); nothing stale can be reused
        prepared.add(source)


def _selection_source_files(test_ids: list[str] | None) -> set[Path]:
    """Local test files and ancestor config for an explicit node-id selection.

    Used when the engine has no static dependency graph (the ``--leela`` plugin
    passes node ids but no ``test_dir``).  Scope stays on the executed test
    files plus the conftest/package files pytest loads above them.
    """
    files: set[Path] = set()
    for test_id in test_ids or []:
        path = Path(test_id.split("::", 1)[0])
        if path.suffix != ".py":
            continue
        path = path.resolve()
        if not path.is_file():
            continue
        files.add(path)
        for parent in path.parents:
            for name in ("conftest.py", "__init__.py"):
                candidate = parent / name
                if candidate.is_file():
                    files.add(candidate)
    return files


def _clear_user_modules() -> None:
    """Remove project-local modules (tests + targets) from sys.modules.

    Keeps stdlib, site-packages, and pytest-leela internals intact.
    This forces pytest to reimport test files on every mutation run so
    they pick up the current mutant's code via the import hook.
    """
    cwd = os.getcwd() + os.sep
    to_remove = [
        name
        for name, mod in sys.modules.items()
        if mod is not None
        and (f := getattr(mod, "__file__", None)) is not None
        and f.startswith(cwd)
        and not name.startswith(_KEEP_PREFIXES)
    ]
    for name in to_remove:
        sys.modules.pop(name, None)


class _TimeoutPlugin:
    """Pytest plugin that aborts the run when a timeout event fires."""

    def __init__(self, event: threading.Event) -> None:
        self.event = event

    def pytest_runtest_protocol(self, item: Any, nextitem: Any) -> None:
        if self.event.is_set():
            raise SystemExit("leela: mutant timeout")


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

    def pytest_collectreport(self, report: Any) -> None:
        # A collection error means the test file couldn't even be
        # imported. For a mutation-testing run, that almost always
        # means the mutation broke the import (e.g. ``int | None``
        # → ``int & None`` raising ``TypeError`` at function def
        # time). Without this hook, the mutant is misreported as
        # SURVIVED with ``tests_run=0``.
        if report.outcome == "failed":
            self.errors.append(report.nodeid)
            # ``tests_run`` measures collected runnable items; a
            # collection failure contributes one to the count so
            # downstream logic sees a non-empty test set and the
            # post-loop kill check (``len(collector.failed) >
            # 0 or len(collector.errors) > 0``) flips to True.
            self.total += 1


def run_tests_for_mutant(
    mutant: Mutant,
    target_sources: dict[str, str],
    module_to_file: dict[str, str],
    test_ids: list[str] | None = None,
    test_dir: str | None = None,
    known_user_modules: frozenset[str] | None = None,
    test_times: dict[str, float] | None = None,
    dependency_files: set[Path] | None = None,
    prepared_dependencies: set[Path] | None = None,
) -> MutantResult:
    """Run tests against a single mutant, return the result."""
    start = time.monotonic()

    module_names = list(target_sources.keys())

    killing_test: str | None = None

    # Install mutating import hook
    finder = install_hook(target_sources, mutant, module_to_file)

    try:
        # Clear target modules by name (they may lack __file__ when loaded
        # through the mutating import hook) and test modules by file path
        # (they cache direct references to target functions via
        # ``from target.X import func``).
        clear_target_modules(module_names)
        if known_user_modules is not None:
            _clear_user_modules_fast(known_user_modules)
        else:
            _clear_user_modules()
        _clear_framework_caches()
        _prepare_fresh_dependencies(
            dependency_files if dependency_files is not None
            else _selection_source_files(test_ids),
            prepared_dependencies if prepared_dependencies is not None else set(),
        )

        collector = _ResultCollector()

        # Build pytest args — disable leela plugin to prevent recursion
        args: list[str] = [
            "--tb=no",
            "-q",
            "--no-header",
            "-x",
            "--override-ini=addopts=",
            "-p",
            "no:leela",
            "-p",
            "no:leela-benchmark",
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
        #
        # IMPORTANT: save full sys.modules snapshot (not just keys).
        # During self-mutation, mutated cleanup code (e.g. _clear_user_modules
        # with ``and`` → ``or``) can mass-evict stdlib modules.  We must
        # restore them after each inner run.
        saved_meta_path = sys.meta_path[:]
        saved_modules = dict(sys.modules)

        # Set up per-mutant timeout to prevent infinite loops from
        # control-flow mutations (e.g. break→continue).
        timed_out = threading.Event()
        timer: threading.Timer | None = None
        if test_times is not None and test_ids:
            total_expected = sum(test_times.get(t, 1.0) for t in test_ids)
            timeout_seconds = max(2 * total_expected + 1.0, 5.0)
            timer = threading.Timer(timeout_seconds, timed_out.set)
            timer.daemon = True
            timer.start()

        plugins: list[Any] = [collector]
        if timer is not None:
            plugins.append(_TimeoutPlugin(timed_out))

        # Run pytest in-process (suppress noisy output)
        try:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                pytest.main(args, plugins=plugins)
        except (Exception, SystemExit):
            # A mutation that crashes the test runner (or times out)
            # counts as killed.
            elapsed = time.monotonic() - start
            killing_test = "<timeout>" if timed_out.is_set() else "<crashed>"
            return MutantResult(
                mutant=mutant,
                killed=True,
                tests_run=collector.total,
                killing_test=killing_test,
                time_seconds=elapsed,
                test_ids_run=[],
                killing_tests=[killing_test],
            )
        finally:
            if timer is not None:
                timer.cancel()

            # Restore meta_path: removes hooks that inner pytest.main()
            # added (AssertionRewritingHook etc.).  The saved snapshot
            # includes our MutatingFinder + the outer session's hooks,
            # so outer state is preserved.
            sys.meta_path[:] = saved_meta_path

            # Restore any modules evicted during the inner run (e.g. by
            # mutated cleanup code).  Then remove CWD-local modules that
            # the inner run added.
            sys.modules.update(saved_modules)
            cwd_prefix = os.getcwd() + os.sep
            for key in list(sys.modules.keys()):
                if key not in saved_modules:
                    mod = sys.modules.get(key)
                    mod_file = (
                        getattr(mod, "__file__", None) if mod is not None else None
                    )
                    if mod_file is not None and mod_file.startswith(cwd_prefix):
                        sys.modules.pop(key, None)

        # If the timeout fired but pytest caught the SystemExit internally,
        # treat it as killed.
        if timed_out.is_set():
            elapsed = time.monotonic() - start
            return MutantResult(
                mutant=mutant,
                killed=True,
                tests_run=collector.total,
                killing_test="<timeout>",
                time_seconds=elapsed,
                test_ids_run=collector.passed + collector.failed + collector.errors,
                killing_tests=["<timeout>"],
            )

        killed = len(collector.failed) > 0 or len(collector.errors) > 0
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
            test_ids_run=collector.passed + collector.failed + collector.errors,
            killing_tests=collector.failed + collector.errors,
        )
    finally:
        # Cleanup: remove hook and clear cached modules
        remove_hook(finder)
        clear_target_modules(module_names)
        if known_user_modules is not None:
            _clear_user_modules_fast(known_user_modules)
        else:
            _clear_user_modules()
        _clear_framework_caches()

        # Safety net: remove any stale MutatingFinders left on
        # sys.meta_path from crashed previous runs.
        sys.meta_path[:] = [
            f for f in sys.meta_path if not isinstance(f, MutatingFinder)
        ]

        # Restore stdlib path modules that may have been evicted during
        # cleanup.  Must use direct dict assignment — ``import ntpath``
        # would go through the import machinery, hitting pytest's
        # assertion rewriter → PurePath → import ntpath → recursion.
        for mod_name, mod_obj in _STDLIB_PATH_MODULES.items():
            sys.modules.setdefault(mod_name, mod_obj)
