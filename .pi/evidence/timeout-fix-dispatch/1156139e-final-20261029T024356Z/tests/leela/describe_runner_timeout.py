"""Hard-deadline contracts, including actual background-thread execution."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest


_BOOTSTRAP = """
import json, os, sys, threading
from pytest_leela.runner import InnerSession, ProjectModuleScope
sys.path.insert(0, os.getcwd())
def run():
    session = InnerSession(None, ['test_subject.py::test_subject'], None,
                           ProjectModuleScope(), test_times={'test_subject.py::test_subject': 0.0})
    session.run()
    print(json.dumps({'timeout': session.timed_out.is_set(),
                      'passed': session.collector.passed,
                      'failed': session.collector.failed,
                      'crash': session.crash}), flush=True)
thread = threading.Thread(target=run)
thread.start()
thread.join()
"""


@pytest.mark.parametrize("phase", ["call", "collection", "teardown"])
def test_inner_session_terminates_nonreturning_execution(tmp_path, phase):
    block = "from pathlib import Path\nimport inspect\nPath('entered').write_text(inspect.currentframe().f_code.co_name)\nimport threading\nthreading.Event().wait()\n"
    if phase == "call":
        source = "def test_subject():\n" + "".join("    " + line + "\n" for line in block.splitlines())
    elif phase == "collection":
        source = block + "def test_subject():\n    assert True\n"
    else:
        source = "import pytest\n@pytest.fixture(autouse=True)\ndef cleanup():\n    yield\n"
        source += "".join("    " + line + "\n" for line in block.splitlines())
        source += "def test_subject():\n    assert True\n"
    (tmp_path / "test_subject.py").write_text(source)
    process = subprocess.Popen(
        [sys.executable, "-c", _BOOTSTRAP], cwd=tmp_path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=10)
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
    assert process.returncode == 0, stderr.decode()
    assert (tmp_path / "entered").read_text() == {
        "call": "test_subject", "collection": "<module>", "teardown": "cleanup",
    }[phase]
    result = json.loads(stdout)
    assert result["timeout"] is True
    assert result["crash"] is None
    assert result["failed"] == []


def test_inner_session_worker_preserves_real_call_failure(tmp_path, monkeypatch):
    from pytest_leela.ast_analysis import find_mutation_points
    from pytest_leela.models import Mutant
    from pytest_leela.runner import run_tests_for_mutant

    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    source = "def add(a, b):\n    return a + b\n"
    target = tmp_path / "arithmetic.py"
    target.write_text(source)
    (tmp_path / "test_subject.py").write_text(
        "from arithmetic import add\ndef test_subject():\n    assert add(2, 3) == 5\n"
    )
    point = next(p for p in find_mutation_points(source, str(target), "arithmetic")
                 if p.node_type == "BinOp")
    result = run_tests_for_mutant(
        Mutant(point, "Sub", 0), {"arithmetic": source}, {"arithmetic": str(target)},
        ["test_subject.py::test_subject"], test_times={"test_subject.py::test_subject": 0.0},
    )
    assert result.killed is True
    assert result.killing_tests == ["test_subject.py::test_subject"]
    assert result.tests_run == 1
    assert result.error is None


def test_timeout_regression_outer_guard_reaps_on_deadline(tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls = []
    process = SimpleNamespace(pid=424242, returncode=None,
                              wait=lambda timeout: calls.append(("wait", timeout)))
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("controlled child", 10)
    process.communicate = timeout
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    with pytest.raises(subprocess.TimeoutExpired):
        test_inner_session_terminates_nonreturning_execution(tmp_path, "call")
    assert calls == [(424242, signal.SIGKILL), ("wait", 2)]
