"""Execute tests against a single mutant (subprocess-isolated)."""

from __future__ import annotations

import base64
import json
import os
import pickle
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from pytest_leela.models import Mutant, MutantResult

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


def _compute_timeout(
    test_ids: list[str] | None, test_times: dict[str, float] | None
) -> float | None:
    """Compute timeout in seconds for a test run.

    Formula: max(2 * total_expected + 1.0, 5.0)
    where total_expected = sum(test_times.get(t, 1.0) for t in test_ids)

    Returns None if test_times or test_ids is not provided.
    """
    if test_ids and test_times is not None:
        total_expected = sum(test_times.get(t, 1.0) for t in test_ids)
        return max(2 * total_expected + 1.0, 5.0)
    return None


def precompute_user_modules() -> frozenset[str]:
    """Scan sys.modules once and return CWD-local, non-KEEP_PREFIXES module names."""
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
    """Remove only the precomputed set of user modules from sys.modules."""
    for name in known_user_modules:
        sys.modules.pop(name, None)


def _clear_framework_caches() -> None:
    """Clear framework-specific caches that may hold references to user modules."""
    if _django_clear_url_caches is not None:
        _django_clear_url_caches()


def _clear_user_modules() -> None:
    """Remove project-local modules (tests + targets) from sys.modules."""
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

    def pytest_runtest_logreport(self, report: object) -> None:
        when = getattr(report, "when", None)
        if when == "call":
            self.total += 1
            if getattr(report, "passed", False):
                self.passed.append(getattr(report, "nodeid", ""))
            elif getattr(report, "failed", False):
                self.failed.append(getattr(report, "nodeid", ""))
        elif when in ("setup", "teardown") and getattr(report, "failed", False):
            self.errors.append(getattr(report, "nodeid", ""))


# -------------------------------------------------------------------
# Subprocess entry point
# Runs as: python -c <script> src_path config_path
# Reads config from a JSON file (arg 2), runs pytest, outputs JSON to stdout.
# -------------------------------------------------------------------

_SUBPROCESS_SCRIPT = r"""
import contextlib
import io
import signal
import sys
import os
import json
import base64
import pickle

_src_path = sys.argv[1]
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

with open(sys.argv[2]) as _f:
    _cfg = json.load(_f)

_mutant = pickle.loads(base64.b64decode(_cfg["mutant_pickle"]))

from pytest_leela.import_hook import (
    MutatingFinder,
    clear_target_modules,
    install_hook,
    remove_hook,
)

try:
    from django.urls import clear_url_caches as _django_clear_url_caches
except ImportError:
    _django_clear_url_caches = None


def _clear_framework_caches():
    if _django_clear_url_caches is not None:
        _django_clear_url_caches()


class _ResultCollector:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.errors = []
        self.total = 0

    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            self.total += 1
            if report.passed:
                self.passed.append(report.nodeid)
            elif report.failed:
                self.failed.append(report.nodeid)
        elif report.when in ("setup", "teardown") and report.failed:
            self.errors.append(report.nodeid)


_module_names = list(_cfg["target_sources"].keys())
_finder = install_hook(
    _cfg["target_sources"], _mutant, _cfg["module_to_file"], None
)
clear_target_modules(_module_names)
_clear_framework_caches()

try:
    _collector = _ResultCollector()
    _args = [
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
    if _cfg.get("test_ids"):
        _args.extend(_cfg["test_ids"])
    elif _cfg.get("test_dir"):
        _args.append(_cfg["test_dir"])

    _timeout = _cfg.get("timeout_seconds")
    _timed_out = False

    if _timeout:

        def _sig_handler(_sig, _frame):
            raise SystemExit(124)

        _old = signal.signal(signal.SIGALRM, _sig_handler)
        signal.alarm(int(_timeout))

    try:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            import pytest
            pytest.main(_args, plugins=[_collector])
    except SystemExit as e:
        if getattr(e, "code", None) == 124:
            _timed_out = True
    finally:
        if _timeout:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, _old)

    _killed = len(_collector.failed) > 0 or len(_collector.errors) > 0
    _killing_test = None
    if _collector.failed:
        _killing_test = _collector.failed[0]
    elif _collector.errors:
        _killing_test = _collector.errors[0]
    if _timed_out:
        _killing_test = "<timeout>"
        _killed = True

    _result = {
        "killed": _killed,
        "tests_run": _collector.total,
        "killing_test": _killing_test,
        "test_ids_run": (
            _collector.passed + _collector.failed + _collector.errors
        ),
        "killing_tests": _collector.failed + _collector.errors,
    }
    if _timed_out:
        _result["killing_tests"] = ["<timeout>"]
    print(json.dumps(_result))

finally:
    remove_hook(_finder)
    clear_target_modules(_module_names)
    _clear_framework_caches()
    sys.meta_path[:] = [
        f for f in sys.meta_path if not isinstance(f, MutatingFinder)
    ]
    for _n, _m in {
        "ntpath": sys.modules["ntpath"],
        "posixpath": sys.modules["posixpath"],
    }.items():
        sys.modules.setdefault(_n, _m)
"""


def run_tests_for_mutant(
    mutant: Mutant,
    target_sources: dict[str, str],
    module_to_file: dict[str, str],
    test_ids: list[str] | None = None,
    test_dir: str | None = None,
    known_user_modules: frozenset[str] | None = None,
    test_times: dict[str, float] | None = None,
    target_codes: object = None,
) -> MutantResult:
    """Run tests against a single mutant, return the result.

    Runs each mutant's tests in an **isolated subprocess** to avoid segfaults
    from GC/threading interactions on Python 3.14 during session teardown.
    """
    del known_user_modules  # unused, kept for API compatibility
    del target_codes  # cannot pickle code objects; subprocess uses AST path

    start = time.monotonic()
    mutant_pickle = base64.b64encode(pickle.dumps(mutant)).decode()
    timeout_seconds = _compute_timeout(test_ids, test_times)

    config = {
        "mutant_pickle": mutant_pickle,
        "target_sources": target_sources,
        "module_to_file": module_to_file,
        "test_ids": test_ids,
        "test_dir": test_dir,
        "timeout_seconds": timeout_seconds,
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as cf:
        json.dump(config, cf)
        config_path = cf.name

    try:
        src_path = os.path.join(os.getcwd(), "src")
        existing_pythonpath = os.environ.get("PYTHONPATH", "")
        pythonpath_parts = [p for p in existing_pythonpath.split(os.pathsep) if p]
        if src_path not in pythonpath_parts:
            pythonpath_parts.insert(0, src_path)
        new_pythonpath = os.pathsep.join(pythonpath_parts)
        timeout = (timeout_seconds + 2.0) if timeout_seconds else 120.0

        proc = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_SCRIPT, src_path, config_path],
            capture_output=True,
            timeout=timeout,
            cwd=os.getcwd(),
            env={**os.environ, "PYTHONPATH": new_pythonpath},
        )

        output = (proc.stdout + proc.stderr).strip()
        if not output:
            elapsed = time.monotonic() - start
            return MutantResult(
                mutant=mutant,
                killed=True,
                tests_run=0,
                killing_test="<crashed>",
                time_seconds=elapsed,
                test_ids_run=[],
                killing_tests=["<crashed>"],
            )

        try:
            result_data = json.loads(output)
        except json.JSONDecodeError:
            elapsed = time.monotonic() - start
            return MutantResult(
                mutant=mutant,
                killed=True,
                tests_run=0,
                killing_test="<crashed>",
                time_seconds=elapsed,
                test_ids_run=[],
                killing_tests=["<crashed>"],
            )

        elapsed = time.monotonic() - start
        return MutantResult(
            mutant=mutant,
            killed=result_data.get("killed", False),
            tests_run=result_data.get("tests_run", 0),
            killing_test=result_data.get("killing_test"),
            time_seconds=elapsed,
            test_ids_run=result_data.get("test_ids_run", []),
            killing_tests=result_data.get("killing_tests", []),
        )

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        return MutantResult(
            mutant=mutant,
            killed=True,
            tests_run=0,
            killing_test="<timeout>",
            time_seconds=elapsed,
            test_ids_run=[],
            killing_tests=["<timeout>"],
        )
    except Exception:
        elapsed = time.monotonic() - start
        return MutantResult(
            mutant=mutant,
            killed=True,
            tests_run=0,
            killing_test="<crashed>",
            time_seconds=elapsed,
            test_ids_run=[],
            killing_tests=["<crashed>"],
        )
    finally:
        try:
            os.unlink(config_path)
        except OSError:
            pass
