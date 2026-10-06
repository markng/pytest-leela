"""Execute tests against a single mutant in-process."""

from __future__ import annotations

import contextlib
import io
import ntpath  # noqa: F401 — keep in sys.modules (see engine.py comment)
import os
import posixpath  # noqa: F401 — same as ntpath
import site
import sys
import threading
import time
import types
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from weakref import WeakSet

    from django.apps.registry import Apps
    from django.contrib.admin.sites import AdminSite

# Save references to stdlib path modules.  During self-mutation the inner
# pytest.main() may evict these from sys.modules; we need to restore them
# *without* going through the import machinery (which would trigger
# pytest's assertion rewriter → PurePath → import ntpath → recursion).
_STDLIB_PATH_MODULES = {
    "ntpath": sys.modules["ntpath"],
    "posixpath": sys.modules["posixpath"],
}

import pytest

from pytest_leela.import_hook import (
    MutatingFinder,
    clear_target_modules,
    install_hook,
    remove_hook,
)
from pytest_leela.models import Mutant, MutantResult

# Pre-cache Django's clear_url_caches at import time.  Doing the import
# inside _clear_framework_caches() is fragile: during self-mutation the
# import machinery may be in a degraded state (pytest's assertion rewriter
# creates PurePath objects which lazily ``import ntpath`` on Python 3.13,
# causing infinite recursion through find_spec when ntpath is absent from
# sys.modules).  Capturing the reference once at module load avoids the
# problem entirely.
try:
    from django.urls import clear_url_caches

    _django_clear_url_caches: Callable[[], None] | None = clear_url_caches
except ImportError:
    # Django is optional.
    _django_clear_url_caches = None

try:
    from django.apps import apps

    _django_apps: Apps | None = apps
except ImportError:
    # Django is optional.
    _django_apps = None

# Prefixes for modules that should never be evicted between mutation runs.
_KEEP_PREFIXES = (
    "pytest_leela.",
    "_pytest",
    "pytest",
    "pluggy",
    "py.",
    "_py",
)


# Directory names that only ever hold installed (third-party) packages.
_PACKAGE_DIR_NAMES = frozenset({"site-packages", "dist-packages"})


class ProjectModuleScope:
    """Decides which loaded modules are the project's own source.

    Only the project's own modules may be evicted between mutants: they must
    re-import so that mutated code is picked up.  A file counts as project
    source when it lives under *cwd* but not inside the Python environment —
    a virtualenv inside the project (uv's ``.venv``) is under *cwd* too, and
    evicting its packages forces C extensions such as numpy to re-import,
    which CPython refuses ("cannot load module more than once per process").
    """

    def __init__(self, cwd: str | None = None) -> None:
        self.cwd = os.path.join(os.path.abspath(cwd or os.getcwd()), "")
        candidates = {
            sys.prefix,
            sys.base_prefix,
            sys.exec_prefix,
            sys.base_exec_prefix,
            *site.getsitepackages(),
            site.getusersitepackages(),
        }
        roots = {os.path.join(os.path.abspath(c), "") for c in candidates}
        # A root that contains the project itself (a system Python under
        # /usr with the project in /usr/src/app, or ``python -m venv .``)
        # would exclude every project file, so it cannot be used as a root.
        # Packages installed there are still caught by the segment rule.
        self.environment_roots = tuple(
            sorted(r for r in roots if not self.cwd.startswith(r))
        )

    def contains(self, file_path: str) -> bool:
        """Return True if *file_path* is project source under the cwd."""
        if not file_path.startswith(self.cwd):
            return False
        if file_path.startswith(self.environment_roots):
            return False
        relative_parts = file_path[len(self.cwd) :].split(os.sep)
        return _PACKAGE_DIR_NAMES.isdisjoint(relative_parts)

    def module_names(self) -> frozenset[str]:
        """Names of loaded project modules, excluding leela/pytest internals.

        Django's model and admin modules are excluded too, with every project
        module they reference: re-executing them re-registers into
        process-global registries, which Django rejects, and re-importing a
        module they reference would split class identity (a kept model
        subclassing the old copy of a re-imported mixin).
        """
        project = {
            name
            for name, mod in list(sys.modules.items())
            if mod is not None
            and (f := getattr(mod, "__file__", None)) is not None
            and self.contains(f)
            and not name.startswith(_KEEP_PREFIXES)
        }
        pinned = _referenced_closure(_django_registry_module_names(), project)
        return frozenset(project - pinned)


def _referenced_closure(roots: frozenset[str], candidates: set[str]) -> set[str]:
    """*roots* plus every candidate module they reference, transitively.

    A module references another when its namespace holds that module or an
    object (class, function) whose ``__module__`` names it.
    """
    pinned = set(roots)
    frontier = list(roots)
    while frontier:
        for value in list(vars(sys.modules[frontier.pop()]).values()):
            dep = (
                value.__name__
                if isinstance(value, types.ModuleType)
                else getattr(value, "__module__", None)
            )
            # Any object may carry a ``__module__`` attribute of any type.
            if isinstance(dep, str) and dep in candidates and dep not in pinned:
                pinned.add(dep)
                frontier.append(dep)
    return pinned


def _django_registry_module_names() -> frozenset[str]:
    """Loaded Django model and admin modules (and submodules), once set up.

    Re-executing either registers into a process-global registry a second
    time: models warn "Reloading models is not advised" (django/apps/
    registry.py, ``register_model``) and admin raises ``AlreadyRegistered``
    (django/contrib/admin/sites.py, ``register``).  A re-import can also
    re-enter cycles that only resolve in Django's own app-loading order.
    Admin modules are every ``<app>.admin`` plus the module of every
    ModelAdmin registered on any AdminSite (django/contrib/admin/sites.py:
    "all_sites = WeakSet()", each site's ``_registry``).
    """
    if _django_apps is None or not _django_apps.ready:
        # Django absent, or installed but never set up: nothing registered.
        return frozenset()
    roots: list[str] = []
    for config in _django_apps.get_app_configs():
        roots.append(f"{config.name}.admin")
        if config.models_module is not None:
            roots.append(config.models_module.__name__)
    admin_sites = sys.modules.get("django.contrib.admin.sites")
    if admin_sites is not None:
        # None when django.contrib.admin was never imported: no ModelAdmins.
        all_sites = cast("WeakSet[AdminSite]", admin_sites.all_sites)
        for admin_site in list(all_sites):
            for model_admin in list(admin_site._registry.values()):
                roots.append(type(model_admin).__module__)
    packages = tuple(f"{root}." for root in roots)
    return frozenset(
        name for name in sys.modules if name in roots or name.startswith(packages)
    )


def precompute_user_modules(scope: ProjectModuleScope | None = None) -> frozenset[str]:
    """Scan sys.modules once and return the project's own module names.

    This precomputes the set of user modules so that the mutation loop can
    use targeted O(K) operations instead of scanning all ~500-1000 entries
    in sys.modules on every mutant run.
    """
    return (scope or ProjectModuleScope()).module_names()


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


def _clear_user_modules(scope: ProjectModuleScope | None = None) -> None:
    """Remove the project's own modules (tests + targets) from sys.modules.

    Keeps stdlib, installed packages (even a virtualenv inside the project)
    and pytest-leela internals intact.  This forces pytest to reimport test
    files on every mutation run so they pick up the current mutant's code
    via the import hook.
    """
    for name in (scope or ProjectModuleScope()).module_names():
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
        self.collection_errors: list[str] = []  # "nodeid: summary" entries
        self.collection_error_ids: list[str] = []
        self.total = 0
        self.session_started = False

    def pytest_sessionstart(self, session: Any) -> None:
        self.session_started = True

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
        if report.failed:
            summary = _last_line(report.longreprtext)
            self.collection_error_ids.append(report.nodeid)
            self.collection_errors.append(
                report.nodeid if summary is None else f"{report.nodeid}: {summary}"
            )


def _last_line(text: str) -> str | None:
    """Return the last non-blank line of *text* (the exception summary)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else None


# Exit codes meaning the inner pytest run actually executed the tests.
_COMPLETED_EXIT_CODES = (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED)
_KNOWN_EXIT_CODES = frozenset(code.value for code in pytest.ExitCode)


def _exit_code_label(exit_code: int) -> str:
    """``INTERRUPTED`` for a pytest exit code, the number for anything else.

    A plugin can set any integer as the session exit status.
    """
    if exit_code in _KNOWN_EXIT_CODES:
        return pytest.ExitCode(exit_code).name
    return f"exit code {exit_code}"


def _inner_run_error(exit_code: int, collector: _ResultCollector) -> str | None:
    """Explain why an inner run that no test failed did not test the mutant.

    Returns None when the run completed and executed at least one test, i.e.
    the mutant genuinely survived.
    """
    if exit_code not in _COMPLETED_EXIT_CODES:
        reason = f"pytest exited with {_exit_code_label(exit_code)}"
        if collector.collection_errors:
            reason += f" ({'; '.join(collector.collection_errors)})"
        return reason
    if collector.total == 0:
        return "no tests ran"
    return None


class InnerSession:
    """One in-process pytest run against the current import state.

    ``finder`` is the mutating import hook, or None for a run that must not
    mutate anything (the baseline).  ``exitfirst`` stops at the first
    failure, which is all a mutant needs; the baseline runs everything so
    it can name every failing test.
    """

    def __init__(
        self,
        finder: MutatingFinder | None,
        test_ids: list[str] | None,
        test_dir: str | None,
        scope: ProjectModuleScope,
        known_user_modules: frozenset[str] | None = None,
        test_times: dict[str, float] | None = None,
        exitfirst: bool = True,
    ) -> None:
        self.finder = finder
        self.test_ids = test_ids
        self.test_dir = test_dir
        self.scope = scope
        self.known_user_modules = known_user_modules
        self.test_times = test_times
        self.exitfirst = exitfirst
        self.collector = _ResultCollector()
        self.timed_out = threading.Event()
        self.exit_code: int = pytest.ExitCode.OK
        self.crash: str | None = None
        self.elapsed = 0.0

    @property
    def import_errors(self) -> list[str]:
        """Exceptions raised while executing mutated source in this run."""
        return [] if self.finder is None else self.finder.import_errors

    def _target_module_names(self) -> list[str]:
        return [] if self.finder is None else list(self.finder.target_modules)

    def _evict(self) -> None:
        # Clear target modules by name (they may lack __file__ when loaded
        # through the mutating import hook) and test modules by file path
        # (they cache direct references to target functions via
        # ``from target.X import func``).
        clear_target_modules(self._target_module_names())
        if self.known_user_modules is not None:
            _clear_user_modules_fast(self.known_user_modules)
        else:
            _clear_user_modules(self.scope)
        _clear_framework_caches()

    def _args(self) -> list[str]:
        # Disable the leela plugin to prevent recursion.
        args: list[str] = [
            "--tb=no",
            "-q",
            "--no-header",
            "--override-ini=addopts=",
            "-p",
            "no:leela",
            "-p",
            "no:leela-benchmark",
            "--capture=sys",
        ]
        if self.exitfirst:
            args.append("-x")
        if self.test_ids:
            args.extend(self.test_ids)
        elif self.test_dir:
            args.append(self.test_dir)
        return args

    def run(self) -> InnerSession:
        """Evict, run pytest in-process, restore interpreter state."""
        start = time.monotonic()
        self._evict()
        try:
            self._run_pytest()
        finally:
            if self.finder is not None:
                remove_hook(self.finder)
            self._evict()

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
        self.elapsed = time.monotonic() - start
        return self

    def _run_pytest(self) -> None:
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
        timer: threading.Timer | None = None
        if self.test_times is not None and self.test_ids:
            total_expected = sum(self.test_times.get(t, 1.0) for t in self.test_ids)
            timeout_seconds = max(2 * total_expected + 1.0, 5.0)
            timer = threading.Timer(timeout_seconds, self.timed_out.set)
            timer.daemon = True
            timer.start()

        plugins: list[Any] = [self.collector]
        if timer is not None:
            plugins.append(_TimeoutPlugin(self.timed_out))

        # Run pytest in-process (suppress noisy output)
        try:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.exit_code = pytest.main(self._args(), plugins=plugins)
        except (Exception, SystemExit) as exc:
            # Recorded, not raised: a crash is a verdict about this mutant
            # (see result_for), and the outer session must keep running.
            self.crash = f"pytest crashed: {type(exc).__name__}: {exc}"
        finally:
            if timer is not None:
                timer.cancel()

            # Restore meta_path: removes hooks that inner pytest.main()
            # added (AssertionRewritingHook etc.).  The saved snapshot
            # includes our MutatingFinder + the outer session's hooks,
            # so outer state is preserved.
            sys.meta_path[:] = saved_meta_path

            # Restore any modules evicted during the inner run (e.g. by
            # mutated cleanup code).  Then remove project modules that the
            # inner run added (installed packages it imported stay loaded).
            sys.modules.update(saved_modules)
            for key in list(sys.modules.keys()):
                if key not in saved_modules:
                    mod = sys.modules.get(key)
                    mod_file = (
                        getattr(mod, "__file__", None) if mod is not None else None
                    )
                    if mod_file is not None and self.scope.contains(mod_file):
                        sys.modules.pop(key, None)

    def failures(self) -> list[str]:
        """Tests that failed in the call phase, then setup/teardown errors."""
        return self.collector.failed + self.collector.errors

    def error(self) -> str | None:
        """Why the run did not test anything, or None if tests completed."""
        if self.crash is not None:
            return self.crash
        return _inner_run_error(self.exit_code, self.collector)

    def _import_failed(self) -> bool:
        """Whether pytest reported a failure for the mutated module's import.

        Only a failed collection report, or a conftest import failure (pytest
        returns USAGE_ERROR before any session starts: _pytest/config/
        __init__.py, ``main``: "except ConftestImportFailure"), makes the
        suite red.  A caught import, a module-level skip or a skip marker
        leave it green or empty, so they are no kill.
        """
        if self.collector.collection_error_ids:
            return True
        return (
            self.exit_code == pytest.ExitCode.USAGE_ERROR
            and not self.collector.session_started
        )

    def result_for(self, mutant: Mutant) -> MutantResult:
        """Classify this run as a kill, a survival or an error for *mutant*."""
        collector = self.collector
        test_ids_run = collector.passed + collector.failed + collector.errors
        if self.timed_out.is_set():
            # An infinite loop introduced by the mutant, whether pytest
            # caught the timeout's SystemExit or let it escape: a real kill.
            return MutantResult(
                mutant=mutant,
                killed=True,
                tests_run=collector.total,
                killing_test="<timeout>",
                time_seconds=self.elapsed,
                test_ids_run=[] if self.crash is not None else test_ids_run,
                killing_tests=["<timeout>"],
            )
        if self.crash is None:
            killing_tests = self.failures()
            if not killing_tests and self.import_errors and self._import_failed():
                killing_tests = collector.collection_error_ids or [
                    f"<import of {self.import_errors[0]}>"
                ]
            if killing_tests:
                return MutantResult(
                    mutant=mutant,
                    killed=True,
                    tests_run=collector.total,
                    killing_test=killing_tests[0],
                    time_seconds=self.elapsed,
                    test_ids_run=test_ids_run,
                    killing_tests=killing_tests,
                )
        return MutantResult(
            mutant=mutant,
            killed=False,
            tests_run=collector.total,
            killing_test=None,
            time_seconds=self.elapsed,
            test_ids_run=[] if self.crash is not None else test_ids_run,
            killing_tests=[],
            error=self.error(),
        )


def run_tests_for_mutant(
    mutant: Mutant,
    target_sources: dict[str, str],
    module_to_file: dict[str, str],
    test_ids: list[str] | None = None,
    test_dir: str | None = None,
    known_user_modules: frozenset[str] | None = None,
    test_times: dict[str, float] | None = None,
    scope: ProjectModuleScope | None = None,
) -> MutantResult:
    """Run tests against a single mutant, return the result."""
    finder = install_hook(target_sources, mutant, module_to_file)
    session = InnerSession(
        finder,
        test_ids,
        test_dir,
        scope or ProjectModuleScope(),
        known_user_modules=known_user_modules,
        test_times=test_times,
    )
    return session.run().result_for(mutant)


def run_baseline(
    test_ids: list[str] | None,
    test_dir: str | None,
    known_user_modules: frozenset[str],
    scope: ProjectModuleScope,
) -> InnerSession:
    """Run the tests once with no import hook installed: nothing is mutated."""
    session = InnerSession(
        None,
        test_ids,
        test_dir,
        scope,
        known_user_modules=known_user_modules,
        exitfirst=False,
    )
    return session.run()
