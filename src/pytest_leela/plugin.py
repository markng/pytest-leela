"""pytest plugin entry point for leela mutation testing."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# On Python <=3.13, this lets an invalid ``BitOr -> BitAnd`` annotation
# mutation fail while the definition is executed. Python 3.14 uses PEP 649
# lazy annotations instead; the annotation-policy regression reads supported
# hints to exercise that failure. See ``pytest_leela.import_hook`` for why
# its ``compile()`` call must also remain unflagged.

import contextlib
import fnmatch
import glob as _glob
import io
import os
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import cast

import pytest

from pytest_leela.config import load_config
from pytest_leela.coverage_tracker import CoveragePlugin
from pytest_leela.engine import BaselineFailure, Engine
from pytest_leela.git_diff import changed_files
from pytest_leela.models import EngineProgress
from pytest_leela.operators import ALL_OPERATORS
from pytest_leela.output import format_terminal_report
from pytest_leela.resources import ResourceLimits


def _is_test_file(basename: str, python_files: list[str]) -> bool:
    """Return True if the filename matches the project's test-file patterns."""
    return basename == "conftest.py" or any(
        fnmatch.fnmatch(basename, p) for p in python_files
    )


class _MutationProgressReporter:
    """Write bounded mutation progress to pytest's outer terminal stream."""

    def __init__(self, writer: object) -> None:
        self._stream = cast(io.TextIOBase, getattr(writer, "_file", writer))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._current = "mutation preparation"
        self._started = 0.0
        self._phase_started = 0.0
        self._thread: threading.Thread | None = None
        self._completed = 0
        self._total = 0

    def _write(self, message: str) -> None:
        with self._lock:
            self._stream.write(message + "\n")
            self._stream.flush()

    def start(self) -> None:
        self._started = time.monotonic()
        self._phase_started = self._started
        self._write("leela: mutation preparation started")
        self._thread = threading.Thread(
            target=self._heartbeat, name="leela-progress", daemon=True
        )
        self._thread.start()

    def _heartbeat(self) -> None:
        while not self._stop.wait(30.0):
            with self._lock:
                phase = self._current
                elapsed = time.monotonic() - self._phase_started
                completed, total = self._completed, self._total
            self._write(
                f"leela: mutation heartbeat {completed}/{total} {phase} "
                f"elapsed={elapsed:.0f}s"
            )

    def __call__(self, event: EngineProgress) -> None:
        if event.kind == "preparation-done":
            with self._lock:
                self._current = "baseline preparation"
                self._phase_started = time.monotonic()
            self._write("leela: mutation preparation complete")
        elif event.kind == "baseline-start":
            with self._lock:
                self._current = "baseline tests"
                self._phase_started = time.monotonic()
            self._write("leela: baseline started")
        elif event.kind == "baseline-done":
            with self._lock:
                self._current = "mutation run preparation"
                self._phase_started = time.monotonic()
            self._write("leela: baseline complete")
        elif event.kind == "mutation-start":
            with self._lock:
                self._total = event.total
                self._current = "mutation run"
                self._phase_started = time.monotonic()
            self._write(f"leela: mutation run started ({event.total} mutants)")
        elif event.kind == "mutant-phase":
            timeout = (
                f"{event.timeout_seconds:.1f}s"
                if event.timeout_seconds is not None else "none"
            )
            if event.phase in {"mutant", "control", "confirmation"}:
                with self._lock:
                    self._current = (
                        f"{event.phase} {event.file_path}:{event.lineno} {event.op} "
                        f"tests={event.selected_tests if event.selected_tests is not None else 'all'} "
                        f"timeout={timeout}"
                    )
                    self._phase_started = time.monotonic()
            if event.phase in {"mutant", "control", "confirmation"}:
                self._write(
                    f"leela: mutant {event.ordinal}/{event.total} {self._current}"
                )
            elif event.phase in {"control-done", "confirmation-done"}:
                self._write(
                    f"leela: mutant {event.ordinal}/{event.total} "
                    f"{event.phase} timeout={'yes' if event.timed_out else 'no'}"
                )
            elif event.timed_out:
                self._write(
                    f"leela: mutant {event.ordinal}/{event.total} timeout occurred"
                )
        elif event.kind == "mutant":
            with self._lock:
                self._completed = event.ordinal
            locus = f"{event.file_path}:{event.lineno} {event.op}"
            self._write(
                f"leela: mutant {event.ordinal}/{event.total} {locus} "
                f"tests={event.selected_tests if event.selected_tests is not None else 'all'} "
                f"{event.status} timeout={'yes' if event.timed_out else 'no'} "
                f"elapsed={event.elapsed_seconds:.2f}s"
            )
        elif event.kind == "done":
            self._write("leela: mutation run complete")

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None


_SKIP_DIRS = {
    ".venv",
    "venv",
    ".tox",
    "build",
    "dist",
    ".git",
    ".hg",
    "__pycache__",
    "node_modules",
    ".eggs",
    "*.egg-info",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("leela", "mutation testing")
    group.addoption(
        "--leela", action="store_true", default=False, help="Enable mutation testing"
    )
    group.addoption(
        "--diff", default=None, help="Only mutate files changed since this git ref"
    )
    group.addoption(
        "--target",
        action="append",
        default=[],
        help="File/directory to mutate (repeatable)",
    )
    group.addoption("--max-cores", type=int, default=None, help="Max CPU cores")
    group.addoption("--max-memory", type=int, default=None, help="Max memory percent")
    group.addoption(
        "--leela-html",
        default=None,
        metavar="PATH",
        help="Generate interactive HTML mutation report at PATH",
    )
    group.addoption(
        "--leela-benchmark",
        action="store_true",
        default=False,
        help="Run benchmark mode",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("leela", default=False) or config.getoption(
        "leela_html", default=None
    ):
        config.pluginmanager.register(LeelaPlugin(config), "leela-plugin")
    elif config.getoption("leela_benchmark", default=False):
        from pytest_leela.benchmark import BenchmarkPlugin

        config.pluginmanager.register(BenchmarkPlugin(config), "leela-benchmark")


def _find_target_files(target: str, python_files: list[str]) -> list[str]:
    """Resolve a --target path to a list of .py files."""
    target_path = os.path.abspath(target)
    if os.path.isfile(target_path) and target_path.endswith(".py"):
        return [target_path]
    if os.path.isdir(target_path):
        return sorted(
            os.path.abspath(p)
            for p in _glob.glob(os.path.join(target_path, "**", "*.py"), recursive=True)
            if not os.path.basename(p).startswith("__")
            and not _is_test_file(os.path.basename(p), python_files)
        )
    return []


def _find_default_targets(rootpath: Path, python_files: list[str]) -> list[str]:
    """Look for common source directories to use as default targets.

    Falls back to rootpath itself when no ``src/`` or ``target/`` directory
    exists, so that projects with a flat layout still get mutation coverage.
    """
    for candidate in ("target", "src"):
        candidate_dir = rootpath / candidate
        if candidate_dir.is_dir():
            return sorted(
                os.path.abspath(str(p))
                for p in candidate_dir.rglob("*.py")
                if not p.name.startswith("__")
                and not _is_test_file(p.name, python_files)
            )
    # Fallback: scan rootpath itself for a flat-layout project
    return sorted(
        os.path.abspath(str(p))
        for p in rootpath.rglob("*.py")
        if not p.name.startswith("__")
        and not _is_test_file(p.name, python_files)
        and not any(
            part in _SKIP_DIRS or fnmatch.fnmatch(part, "*.egg-info")
            for part in p.relative_to(rootpath).parts
        )
    )


def _apply_excludes(
    files: list[str], excludes: tuple[str, ...] | list[str], rootpath: Path
) -> list[str]:
    """Filter out files matching any of the exclude glob patterns.

    Each file path is made relative to *rootpath* before matching against
    patterns with :func:`fnmatch.fnmatch`.
    """
    if not excludes:
        return files
    root = str(rootpath)
    result: list[str] = []
    for filepath in files:
        rel = os.path.relpath(filepath, root).replace(os.sep, "/")
        if not any(fnmatch.fnmatch(rel, pat) for pat in excludes):
            result.append(filepath)
    return result


class LeelaPlugin:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self._coverage_plugin: CoveragePlugin | None = None

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Resolve target files early and install coverage tracing.

        By tracing coverage during the normal test run (instead of
        re-running all tests in Engine.run), we eliminate one full test
        suite execution from the mutation testing pipeline.
        """
        target_files = self._resolve_target_files(session)
        if target_files:
            self._coverage_plugin = CoveragePlugin(set(target_files))
            session.config.pluginmanager.register(
                self._coverage_plugin, "leela-coverage"
            )

    def _resolve_target_files(self, session: pytest.Session) -> list[str]:
        """Resolve target files from config options."""
        rootpath = session.config.rootpath
        leela_config = load_config(rootpath)
        python_files = self.config.getini("python_files")

        targets = self.config.getoption("target", default=[])
        diff_base = self.config.getoption("diff", default=None)

        if targets:
            target_files: list[str] = []
            for t in targets:
                target_files.extend(_find_target_files(t, python_files))
            target_files = sorted(set(target_files))
        elif diff_base:
            target_files = changed_files(diff_base, test_file_patterns=python_files)
        else:
            target_files = _find_default_targets(rootpath, python_files)

        return _apply_excludes(target_files, leela_config.exclude, rootpath)

    @contextlib.contextmanager
    def _outer_django_db_unblocked(self) -> Generator[None]:
        """Lift the outer session's pytest-django DB block for leela's run.

        pytest-django blocks DB access at configure time and only restores it
        at unconfigure, so it is still blocked here.  Each inner session's
        ``DjangoDbBlocker`` records whatever ``ensure_connection`` is on
        first use as the "real" one (pytest_django/plugin.py,
        ``_dj_db_wrapper``: "self._real_ensure_connection =
        BaseDatabaseWrapper.ensure_connection"), so it would record the
        outer block and its ``unblock()`` could never reach the database.
        """
        if "pytest_django.plugin" not in sys.modules:
            # pytest-django is not installed or not loaded: nothing to lift.
            yield
            return
        from pytest_django.plugin import blocking_manager_key

        if blocking_manager_key not in self.config.stash:
            # Loaded but disabled for this session (``-p no:django``).
            yield
            return
        with self.config.stash[blocking_manager_key].unblock():
            yield

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if exitstatus != 0:
            return

        rootpath = session.config.rootpath
        leela_config = load_config(rootpath)

        # Resolve operator categories
        enabled_categories = leela_config.operators
        if "all" in enabled_categories:
            enabled_categories = ALL_OPERATORS

        diff_base = self.config.getoption("diff", default=None)

        # Reuse target files resolved during pytest_sessionstart
        target_files = self._resolve_target_files(session)
        if not target_files:
            return

        # Retrieve coverage map collected during the normal test run
        # (avoids re-running the entire test suite).
        pre_coverage_map = None
        if self._coverage_plugin is not None:
            pre_coverage_map = self._coverage_plugin.coverage_map
            pre_coverage_map.test_times = self._coverage_plugin.test_times

        # Collect test node IDs from the session instead of hardcoding a
        # ``tests/`` directory.  This lets pytest-leela work with any test
        # layout (Django apps, flat repos, monorepos, etc.).
        test_node_ids = [item.nodeid for item in session.items]

        limits = ResourceLimits(
            max_cores=self.config.getoption("max_cores", default=None),
            max_memory_percent=self.config.getoption("max_memory", default=None),
        )

        tw = (
            session.config.get_terminal_writer()
            if hasattr(session.config, "get_terminal_writer")
            else None
        )

        progress = _MutationProgressReporter(tw if tw is not None else sys.stderr)
        engine = Engine(
            enabled_categories=enabled_categories,
            on_cli_progress=progress,
        )
        progress.start()
        try:
            with self._outer_django_db_unblocked():
                result = engine.run(
                    target_files,
                    test_node_ids=test_node_ids,
                    limits=limits,
                    diff_base=diff_base,
                    pre_coverage_map=pre_coverage_map,
                )
        except BaselineFailure as exc:
            message = f"\nleela: aborted: {exc}\n"
            if tw:
                tw.write(message)
            else:
                print(message)
            session.exitstatus = 1
            return
        finally:
            progress.close()

        report = format_terminal_report(result)

        if tw:
            tw.write(report)
        else:
            print(report)

        html_path = self.config.getoption("leela_html", default=None)
        if html_path is not None:
            from pytest_leela.html_report import generate_html_report

            generate_html_report(result, html_path)

        if result.survived or (result.errors and leela_config.fail_on_error):
            session.exitstatus = 1
