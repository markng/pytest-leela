"""The private process boundary owns cleanup, never mutation scoring."""

import json
import os
import sys
import subprocess

import pytest

from pytest_leela._timeout_process import DirectTestProcess as WorkerProcess, TestProcess as GuardedProcess


def _request(tmp_path):
    return json.dumps({
        "path": sys.path + [str(tmp_path)], "cwd": str(tmp_path),
        "prefixes": {name: getattr(sys, name) for name in
                     ("prefix", "base_prefix", "exec_prefix", "base_exec_prefix")},
        "mutation": None, "sources": {}, "files": {},
        "test_ids": [str(tmp_path / "test_worker.py")],
        "test_dir": None, "exitfirst": True,
    }).encode()


def test_process_returns_completed_evidence_and_reaps(tmp_path):
    (tmp_path / "test_worker.py").write_text("def test_pass():\n    assert 1 + 1 == 2\n")
    worker = WorkerProcess()
    response = json.loads(worker.run(_request(tmp_path), 5))
    assert response["exit_code"] == 0
    assert response["collector"]["total"] == 1
    assert len(response["collector"]["passed"]) == 1
    assert response["collector"]["failed"] == []
    assert worker.process is None
    assert worker.exchange_thread is not None
    assert worker.exchange_thread.daemon is False
    assert not worker.exchange_thread.is_alive()
    worker.close()


@pytest.mark.parametrize("ending", ["return", "crash", "timeout"])
@pytest.mark.parametrize("boundary", [WorkerProcess, GuardedProcess])
def test_process_stops_owned_descendants(tmp_path, ending, boundary):
    marker = tmp_path / "child.pid"
    child_code = "import threading; threading.Event().wait()"
    source = (
        "import os, subprocess, sys, threading\nfrom pathlib import Path\n"
        "def test_child():\n"
        f"    child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"    Path({str(marker)!r}).write_text(str(child.pid))\n"
    )
    if ending == "crash":
        source += "    os._exit(7)\n"
    elif ending == "timeout":
        source += "    threading.Event().wait()\n"
    else:
        source += "    assert child.pid != os.getpid()\n"
    (tmp_path / "test_worker.py").write_text(source)
    worker = boundary()
    if ending == "return":
        response = json.loads(worker.run(_request(tmp_path), 5))
        assert response["collector"]["total"] == 1
    else:
        # On crash a descendant can keep stdout open, so the same deadline
        # still contains the exchange instead of an unbounded pipe read.
        with pytest.raises((RuntimeError, TimeoutError)):
            worker.run(_request(tmp_path), 5)
    pid = int(marker.read_text())
    snapshot = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "stat="],
        capture_output=True, text=True, timeout=2,
    )
    assert not snapshot.stdout.strip() or snapshot.stdout.strip().startswith("Z")
    if isinstance(worker, WorkerProcess):
        assert worker.process is None
        assert not worker.exchange_thread.is_alive()


def test_group_exited_rejects_live_or_missing_members(monkeypatch):
    worker = WorkerProcess()
    for output, expected in [("1 44 Z\n2 44 Z\n3 45 R\n", True),
                             ("1 44 Z\n2 44 S\n", False),
                             ("1 45 Z\n", False)]:
        monkeypatch.setattr("pytest_leela._timeout_process.subprocess.run",
                            lambda *a, **k: subprocess.CompletedProcess(a, 0, output, ""))
        assert worker._group_exited(44) is expected


def test_process_reports_bootstrap_crash_without_a_verdict():
    worker = WorkerProcess()
    with pytest.raises(RuntimeError, match="exited without a result"):
        worker.run(b"not json", 5)
    assert worker.process is None
    assert not worker.exchange_thread.is_alive()


def test_process_cancellation_reaps_before_propagating(tmp_path, monkeypatch):
    worker = WorkerProcess()
    def cancel(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(worker.reply, "get", cancel)
    with pytest.raises(KeyboardInterrupt):
        worker.run(_request(tmp_path), 5)
    assert worker.process is None
    assert not worker.exchange_thread.is_alive()


def test_process_start_failure_does_not_start_ipc(monkeypatch):
    worker = WorkerProcess()
    def unavailable(*args, **kwargs):
        raise OSError("interpreter unavailable")
    monkeypatch.setattr("pytest_leela._timeout_process.subprocess.Popen", unavailable)
    with pytest.raises(OSError, match="interpreter unavailable"):
        worker.run(b"{}", 5)
    assert worker.process is None
    assert worker.exchange_thread is None


def test_process_crash_is_not_a_test_failure(tmp_path):
    (tmp_path / "test_worker.py").write_text("import os\nos._exit(7)\n")
    worker = WorkerProcess()
    with pytest.raises(RuntimeError, match="exited without a result"):
        worker.run(_request(tmp_path), 5)
    assert worker.process is None
    assert not worker.exchange_thread.is_alive()


def test_inner_session_rejects_invalid_worker_json(tmp_path, monkeypatch):
    from pytest_leela.runner import InnerSession, ProjectModuleScope
    monkeypatch.setattr("pytest_leela._timeout_process.TestProcess.run", lambda *args: b"broken")
    session = InnerSession(None, ["test"], None, ProjectModuleScope(str(tmp_path)),
                           test_times={"test": 0.0}).run()
    assert session.timed_out.is_set() is False
    assert "JSONDecodeError" in session.error()
    assert session.collector.total == 0


def test_inner_session_rejects_missing_worker_fields(tmp_path, monkeypatch):
    from pytest_leela.runner import InnerSession, ProjectModuleScope
    monkeypatch.setattr("pytest_leela._timeout_process.TestProcess.run", lambda *args: b"{}")
    session = InnerSession(None, ["test"], None, ProjectModuleScope(str(tmp_path)),
                           test_times={"test": 0.0}).run()
    assert session.timed_out.is_set() is False
    assert "KeyError" in session.error()


def test_process_preserves_parent_environment_and_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("LEELA_TIMEOUT_CONTRACT", "inherited")
    original = os.getcwd()
    (tmp_path / "test_worker.py").write_text(
        "import os, sys, sysconfig\ndef test_environment():\n"
        "    assert os.environ['LEELA_TIMEOUT_CONTRACT'] == 'inherited'\n"
        f"    assert sys.prefix == {sys.prefix!r}\n"
        f"    assert sys.exec_prefix == {sys.exec_prefix!r}\n"
        f"    assert sysconfig.get_path('purelib') == {__import__('sysconfig').get_path('purelib')!r}\n"
    )
    response = json.loads(WorkerProcess().run(_request(tmp_path), 5))
    assert response["collector"]["total"] == 1
    assert response["collector"]["failed"] == []
    assert os.getcwd() == original


def test_guarded_process_rejects_unsupported_platform_before_start(monkeypatch):
    monkeypatch.setattr("pytest_leela._timeout_process.os.name", "unsupported")
    with pytest.raises(RuntimeError, match="requires POSIX"):
        GuardedProcess().run(b"{}", 5)


def test_guardian_nonzero_exit_after_response_is_not_success(tmp_path):
    fake = tmp_path / "controlled_guardian.py"
    fake.write_text("import sys\nprint('{\"result\": {}}', flush=True)\nsys.stderr.write('controlled failure')\nsys.exit(7)\n")
    request = tmp_path / "request.json"
    request.write_text("{}")
    with pytest.raises(RuntimeError, match="status 7: controlled failure"):
        GuardedProcess()._run_guardian(fake, request, 5)


def test_guardian_has_no_lifetime_thread_at_successful_return(tmp_path, monkeypatch):
    import threading
    from pytest_leela import _timeout_guardian as guardian
    request = tmp_path / "request.json"
    request.write_bytes(_request(tmp_path))
    read, write = os.pipe()
    before = set(threading.enumerate())
    monkeypatch.setattr(sys, "argv", ["guardian", str(request)])
    monkeypatch.setattr(WorkerProcess, "run", lambda *args: b"{}")
    from types import SimpleNamespace
    monkeypatch.setattr(guardian, "time", SimpleNamespace(monotonic=lambda: 0))
    data = json.loads(request.read_text())
    data["deadline"] = 5
    request.write_text(json.dumps(data))
    with os.fdopen(read, "rb", buffering=0) as lifetime:
        monkeypatch.setattr(sys, "stdin", lifetime)
        guardian.main()
    os.close(write)
    assert set(threading.enumerate()) == before


def test_worker_execute_returns_native_collector_without_a_mutant(tmp_path, monkeypatch):
    from pytest_leela._timeout_worker import execute
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_worker.py").write_text("def test_pass():\n    assert 2 == 2\n")
    response = execute(json.loads(_request(tmp_path)))
    assert response["exit_code"] == 0
    assert response["collector"]["total"] == 1
    assert response["collector"]["failure_evidence"] == []
    assert response["import_errors"] == response["import_failures"] == []
    assert response["collector"]["_rootpath"] == str(tmp_path)


def test_worker_execute_returns_actual_call_fingerprint(tmp_path, monkeypatch):
    import dataclasses
    from pytest_leela._timeout_worker import execute
    from pytest_leela.ast_analysis import find_mutation_points
    from pytest_leela.models import Mutant
    monkeypatch.chdir(tmp_path)
    source = "def add(a, b):\n    return a + b\n"
    target = tmp_path / "worker_target.py"
    target.write_text(source)
    (tmp_path / "test_worker.py").write_text(
        "from worker_target import add\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    point = next(p for p in find_mutation_points(source, str(target), "worker_target")
                 if p.node_type == "BinOp")
    request = json.loads(_request(tmp_path))
    request.update(mutation=dataclasses.asdict(Mutant(point, "Sub", 0)),
                   sources={"worker_target": source}, files={"worker_target": str(target)})
    response = execute(request)
    assert response["exit_code"] == 1
    evidence = response["collector"]["failure_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["phase"] == "call"
    assert evidence[0]["fingerprint"][0] == "builtins.AssertionError"


def test_guardian_stops_watcher_after_worker_error(tmp_path, monkeypatch, capsys):
    import threading
    from pytest_leela import _timeout_guardian as guardian
    request = tmp_path / "request.json"
    data = json.loads(_request(tmp_path))
    data["deadline"] = 5
    request.write_text(json.dumps(data))
    read, write = os.pipe()
    before = set(threading.enumerate())
    def fail(*args):
        raise OSError("controlled worker failure")
    monkeypatch.setattr(sys, "argv", ["guardian", str(request)])
    monkeypatch.setattr(WorkerProcess, "run", fail)
    with os.fdopen(read, "rb", buffering=0) as lifetime:
        monkeypatch.setattr(sys, "stdin", lifetime)
        guardian.main()
    os.close(write)
    envelope = json.loads(capsys.readouterr().out)
    assert envelope == {"failure": "OSError", "message": "controlled worker failure"}
    assert set(threading.enumerate()) == before


def test_guardian_parent_eof_is_a_lifecycle_error(tmp_path, monkeypatch, capsys):
    import threading
    from pytest_leela import _timeout_guardian as guardian
    request = tmp_path / "request.json"
    data = json.loads(_request(tmp_path))
    data["deadline"] = 5
    request.write_text(json.dumps(data))
    read, write = os.pipe()
    os.close(write)
    before = set(threading.enumerate())
    def await_parent(self, *args):
        error = self.reply.get(timeout=2)
        raise error
    monkeypatch.setattr(sys, "argv", ["guardian", str(request)])
    monkeypatch.setattr(WorkerProcess, "run", await_parent)
    with os.fdopen(read, "rb", buffering=0) as lifetime:
        monkeypatch.setattr(sys, "stdin", lifetime)
        guardian.main()
    envelope = json.loads(capsys.readouterr().out)
    assert envelope == {"failure": "ParentExited", "message": "leela: caller exited"}
    assert set(threading.enumerate()) == before


def test_process_ipc_write_failure_is_not_deadline(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    worker = WorkerProcess()
    closed = io.BytesIO()
    closed.close()
    worker.process = SimpleNamespace(stdin=closed, stdout=io.BytesIO())
    worker._exchange(b"request")
    error = worker.reply.get_nowait()
    assert isinstance(error, ValueError)
    assert str(error) == "I/O operation on closed file."


@pytest.mark.parametrize("deadline,clock,expected", [(12.0, 5.0, 7.0), (3.0, 5.0, 0.0)])
def test_guardian_subtracts_startup_from_original_budget(tmp_path, monkeypatch, capsys, deadline, clock, expected):
    from pytest_leela import _timeout_guardian as guardian
    request = tmp_path / "request.json"
    data = json.loads(_request(tmp_path))
    data["deadline"] = deadline
    request.write_text(json.dumps(data))
    budgets = []
    def observe(self, request, timeout):
        budgets.append(timeout)
        return b"{}"
    read, write = os.pipe()
    monkeypatch.setattr(sys, "argv", ["guardian", str(request)])
    monkeypatch.setattr(WorkerProcess, "run", observe)
    from types import SimpleNamespace
    monkeypatch.setattr(guardian, "time", SimpleNamespace(monotonic=lambda: clock))
    with os.fdopen(read, "rb", buffering=0) as lifetime:
        monkeypatch.setattr(sys, "stdin", lifetime)
        guardian.main()
    os.close(write)
    assert budgets == [expected]
    assert json.loads(capsys.readouterr().out) == {"result": {}}


def test_worker_main_emits_one_response_before_waiting(tmp_path, monkeypatch, capsys):
    import io
    from pytest_leela import _timeout_worker as worker
    (tmp_path / "test_worker.py").write_text("def test_pass():\n    assert True\n")
    class StopAfterResponse(Exception):
        pass
    class Completion:
        def wait(self):
            raise StopAfterResponse
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(_request(tmp_path))))
    monkeypatch.setattr(worker.threading, "Event", Completion)
    monkeypatch.setattr(worker, "execute", lambda request: {"checked": request["cwd"]})
    with pytest.raises(StopAfterResponse):
        worker.main()
    assert json.loads(capsys.readouterr().out) == {"checked": str(tmp_path)}


def test_worker_script_entry_dispatches_main(monkeypatch):
    import ast
    from pathlib import Path
    from pytest_leela import _timeout_worker as worker
    tree = ast.parse(Path(worker.__file__).read_text())
    entry = tree.body[-1]
    assert isinstance(entry, ast.If)
    calls = []
    exec(compile(ast.Module(body=[entry], type_ignores=[]), worker.__file__, "exec"),
         {"__name__": "__main__", "main": lambda: calls.append("main")})
    assert calls == ["main"]


def test_worker_execute_preserves_pre_session_import_evidence(tmp_path, monkeypatch):
    import dataclasses
    from pytest_leela._timeout_worker import execute
    from pytest_leela.ast_analysis import find_mutation_points
    from pytest_leela.models import Mutant
    monkeypatch.chdir(tmp_path)
    source = "value = 2 + 1\n"
    target = tmp_path / "worker_broken.py"
    target.write_text(source)
    (tmp_path / "conftest.py").write_text(
        "import worker_broken\nassert worker_broken.value == 3, 'bootstrap value'\n"
    )
    (tmp_path / "test_worker.py").write_text("def test_pass():\n    assert True\n")
    point = next(p for p in find_mutation_points(source, str(target), "worker_broken")
                 if p.node_type == "BinOp")
    # Division by zero is a real import failure in mutated target source.
    source = "value = 2 + 0\n"
    target.write_text(source)
    request = json.loads(_request(tmp_path))
    request.update(mutation=dataclasses.asdict(Mutant(point, "Div", 0)),
                   sources={"worker_broken": source}, files={"worker_broken": str(target)})
    response = execute(request)
    assert response["collector"]["_rootpath"] is None
    assert response["import_errors"]
    assert len(response["import_failures"]) == 1
    name, fingerprint, diagnostic = response["import_failures"][0]
    assert name == "worker_broken"
    assert fingerprint[0] == "builtins.ZeroDivisionError"
    assert "division by zero" in diagnostic


def test_a_process_deadline_arithmetic_before_process_activation(monkeypatch):
    from types import SimpleNamespace
    import io
    import queue
    from pytest_leela import _timeout_process as process_module
    observed = []
    clock = iter([10.0, 12.0])
    monkeypatch.setattr(process_module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(process_module.subprocess, "Popen", lambda *a, **k:
                        SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(b"result\n")))
    monkeypatch.setattr(WorkerProcess, "close", lambda self: observed.append("closed"))
    worker = WorkerProcess()
    def receive(timeout):
        observed.append(timeout)
        return b"result\n"
    monkeypatch.setattr(worker.reply, "get", receive)
    assert worker.run(b"request", 7) == b"result\n"
    worker.exchange_thread.join(timeout=1)
    assert observed == [5.0, "closed"]
    assert not worker.exchange_thread.is_alive()


def test_a_process_closes_pipes_and_reaps_after_permission_failure(monkeypatch):
    import io
    from types import SimpleNamespace
    worker = WorkerProcess()
    calls = []
    process = SimpleNamespace(pid=424242, stdin=io.BytesIO(), stdout=io.BytesIO(),
                              wait=lambda timeout: calls.append(("wait", timeout)))
    worker.process = process
    def denied(pid, sig):
        calls.append((pid, sig))
        raise PermissionError("controlled permission failure")
    monkeypatch.setattr("pytest_leela._timeout_process.os.killpg", denied)
    monkeypatch.setattr(worker, "_group_exited", lambda pid: False)
    with pytest.raises(PermissionError, match="controlled permission failure"):
        worker.close()
    assert calls == [(424242, __import__('signal').SIGKILL), ("wait", 2)]
    assert process.stdin.closed
    assert process.stdout.closed
    assert worker.process is None


def test_a_process_cleanup_uses_owned_group_before_wait(monkeypatch):
    import io
    import signal
    from types import SimpleNamespace
    worker = WorkerProcess()
    calls = []
    process = SimpleNamespace(pid=424242, stdin=io.BytesIO(), stdout=io.BytesIO(),
                              wait=lambda timeout: calls.append(("wait", timeout)))
    worker.process = process
    monkeypatch.setattr("pytest_leela._timeout_process.os.killpg",
                        lambda pid, sig: calls.append((pid, sig)))
    worker.close()
    assert calls == [(424242, signal.SIGKILL), ("wait", 2)]
    assert process.stdin.closed
    assert process.stdout.closed
    assert worker.process is None


def test_a_guardian_transport_deadline_before_process_activation(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    from pytest_leela import _timeout_process as module
    observed = []
    clock = iter([10.0, 12.0])
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), returncode=0,
                              wait=lambda timeout: observed.append(("wait", timeout)))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: process)
    class Ready:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def register(self, *args):
            pass
        def select(self, timeout):
            observed.append(("select", timeout))
            return []
    assert Ready().select(0) == []
    observed.clear()
    monkeypatch.setattr(module.selectors, "DefaultSelector", Ready)
    with pytest.raises(RuntimeError, match="failed to enforce deadline"):
        GuardedProcess()._exchange_guardian(tmp_path, tmp_path, 7, io.BytesIO())
    assert observed == [("select", 10.0), ("wait", 5)]
    assert process.stdin.closed
    assert process.stdout.closed


def test_a_guarded_request_carries_absolute_deadline(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from pytest_leela import _timeout_process as module
    seen = []
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 10.0))
    def inspect_request(self, guardian, request, timeout):
        seen.append((json.loads(request.read_text()), timeout, request))
        return b"response"
    monkeypatch.setattr(GuardedProcess, "_run_guardian", inspect_request)
    assert GuardedProcess().run(b"{}", 7) == b"response"
    payload, timeout, path = seen[0]
    assert payload == {"deadline": 17.0}
    assert timeout == 7
    assert not path.exists()


def test_guardian_deadline_envelope_has_specific_exception(tmp_path):
    from pytest_leela._timeout_process import DeadlineExpired
    fake = tmp_path / "controlled_deadline.py"
    fake.write_text("print('{\"failure\": \"DeadlineExpired\", \"message\": \"deadline\"}', flush=True)\n")
    request = tmp_path / "request.json"
    request.write_text("{}")
    with pytest.raises(DeadlineExpired, match="deadline"):
        GuardedProcess()._run_guardian(fake, request, 5)


def test_a_process_queue_failure_is_not_reclassified_timeout(monkeypatch):
    import io
    from types import SimpleNamespace
    from pytest_leela import _timeout_process as module
    worker = WorkerProcess()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k:
                        SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(b"result\n")))
    monkeypatch.setattr(worker, "close", lambda: None)
    def fail(timeout):
        raise RuntimeError("queue failed")
    monkeypatch.setattr(worker.reply, "get", fail)
    with pytest.raises(RuntimeError, match="queue failed"):
        worker.run(b"request", 5)
    worker.exchange_thread.join(timeout=1)
    assert not worker.exchange_thread.is_alive()


@pytest.mark.parametrize("error", [ProcessLookupError("gone"), ValueError("invalid signal")])
def test_a_process_cleanup_only_accepts_missing_group(monkeypatch, error):
    import io
    from types import SimpleNamespace
    worker = WorkerProcess()
    process = SimpleNamespace(pid=424242, stdin=io.BytesIO(), stdout=io.BytesIO(), wait=lambda timeout: None)
    worker.process = process
    def fail(pid, sig):
        raise error
    monkeypatch.setattr("pytest_leela._timeout_process.os.killpg", fail)
    if isinstance(error, ProcessLookupError):
        worker.close()
    else:
        with pytest.raises(ValueError, match="invalid signal"):
            worker.close()
    assert worker.process is None
    assert process.stdin.closed
    assert process.stdout.closed


def test_guardian_empty_response_is_prompt_protocol_error(tmp_path):
    fake = tmp_path / "controlled_empty.py"
    fake.write_text("pass\n")
    request = tmp_path / "request.json"
    request.write_text("{}")
    with pytest.raises(json.JSONDecodeError):
        GuardedProcess()._run_guardian(fake, request, 0.05)


@pytest.mark.parametrize("wait_error", [subprocess.TimeoutExpired("guardian", 5), OSError("wait failed")])
def test_a_guardian_cleanup_retries_only_actual_timeout(tmp_path, monkeypatch, wait_error):
    import io
    from types import SimpleNamespace
    from pytest_leela import _timeout_process as module
    calls = []
    def wait(timeout):
        calls.append(("wait", timeout))
        if len(calls) == 1:
            raise wait_error
    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), returncode=0,
                              wait=wait, kill=lambda: calls.append("kill"))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: process)
    class Ready:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def register(self, *args):
            pass
        def select(self, timeout):
            return [True]
    with Ready() as selector:
        assert selector.select(0) == [True]
    monkeypatch.setattr(module.selectors, "DefaultSelector", Ready)
    monkeypatch.setattr(module.os, "read", lambda *args: b'{"result": {}}\n')
    monkeypatch.setattr(process.stdout, "fileno", lambda: 0)
    if isinstance(wait_error, subprocess.TimeoutExpired):
        with pytest.raises(RuntimeError, match="guardian cleanup failed"):
            GuardedProcess()._exchange_guardian(tmp_path, tmp_path, 5, io.BytesIO())
        assert calls == [("wait", 5), "kill", ("wait", 2)]
    else:
        with pytest.raises(OSError, match="wait failed"):
            GuardedProcess()._exchange_guardian(tmp_path, tmp_path, 5, io.BytesIO())
        assert calls == [("wait", 5)]


def test_a_guardian_eof_stops_reading_before_retry(tmp_path, monkeypatch):
    import io
    from types import SimpleNamespace
    from pytest_leela import _timeout_process as module
    reads = []
    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), returncode=0, wait=lambda timeout: None)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: process)
    class Ready:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def register(self, *args):
            pass
        def select(self, timeout):
            return [True]
    monkeypatch.setattr(module.selectors, "DefaultSelector", Ready)
    monkeypatch.setattr(process.stdout, "fileno", lambda: 0)
    def eof(*args):
        reads.append("read")
        assert len(reads) == 1, "EOF must not retry the pipe"
        return b""
    assert eof(0, 1) == b""
    reads.clear()
    monkeypatch.setattr(module.os, "read", eof)
    with pytest.raises(json.JSONDecodeError):
        GuardedProcess()._exchange_guardian(tmp_path, tmp_path, 5, io.BytesIO())
    assert reads == ["read"]


def test_a_process_zombie_exception_does_not_hide_other_os_errors(monkeypatch):
    import io
    from types import SimpleNamespace
    worker = WorkerProcess()
    worker.process = SimpleNamespace(pid=424242, stdin=io.BytesIO(), stdout=io.BytesIO(), wait=lambda timeout: None)
    def invalid(pid, sig):
        raise OSError("invalid group operation")
    monkeypatch.setattr("pytest_leela._timeout_process.os.killpg", invalid)
    monkeypatch.setattr("pytest_leela._timeout_process.sys.platform", "darwin")
    monkeypatch.setattr(worker, "_group_exited", lambda pid: True)
    with pytest.raises(OSError, match="invalid group operation"):
        worker.close()
    assert worker.process is None


def test_a_process_delivers_ipc_exception_without_timeout(monkeypatch):
    import io
    from types import SimpleNamespace
    from pytest_leela import _timeout_process as module
    worker = WorkerProcess()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k:
                        SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(b"response\n")))
    monkeypatch.setattr(worker, "close", lambda: None)
    monkeypatch.setattr(worker.reply, "get", lambda timeout: OSError("transport failed"))
    with pytest.raises(OSError, match="transport failed"):
        worker.run(b"request", 5)
    worker.exchange_thread.join(timeout=1)
    assert not worker.exchange_thread.is_alive()


def test_guardian_worker_failure_envelope_is_runner_error(tmp_path):
    fake = tmp_path / "controlled_failure.py"
    fake.write_text("print('{\"failure\": \"OSError\", \"message\": \"worker failed\"}', flush=True)\n")
    request = tmp_path / "request.json"
    request.write_text("{}")
    with pytest.raises(RuntimeError, match="worker failed"):
        GuardedProcess()._run_guardian(fake, request, 5)


def test_a_guardian_import_does_not_execute_main(tmp_path, monkeypatch, capsys):
    import importlib
    from pathlib import Path
    from types import SimpleNamespace
    path = tmp_path / "request.json"
    request = json.loads(_request(tmp_path))
    request["deadline"] = 5
    path.write_text(json.dumps(request))
    read, write = os.pipe()
    calls = []
    monkeypatch.setattr(sys, "argv", ["guardian", str(path)])
    def complete(*args):
        calls.append("run")
        return b"{}"
    assert complete() == b"{}"
    calls.clear()
    monkeypatch.setattr(WorkerProcess, "run", complete)
    sys.modules.pop("pytest_leela._timeout_guardian", None)
    with os.fdopen(read, "rb", buffering=0) as lifetime:
        monkeypatch.setattr(sys, "stdin", lifetime)
        imported = importlib.import_module("pytest_leela._timeout_guardian")
    os.close(write)
    assert imported.__file__ == str(Path(__file__).resolve().parents[2] / "src/pytest_leela/_timeout_guardian.py")
    assert calls == [], "import must not execute the guardian entry point"
    assert capsys.readouterr().out == ""


def test_a_worker_import_does_not_execute_main(tmp_path, monkeypatch, capsys):
    import importlib
    import io
    from pathlib import Path
    from types import SimpleNamespace
    waits = []
    (tmp_path / "test_worker.py").write_text("def test_pass():\n    assert True\n")
    request = _request(tmp_path)
    class Completion:
        def wait(self):
            waits.append("wait")
    # Only the wait at the private entry point is replaced; a valid normal
    # pytest session remains available if a mutant wrongly executes main.
    import threading
    def event():
        return Completion()
    monkeypatch.setattr(threading, "Event", event)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(request)))
    sys.modules.pop("pytest_leela._timeout_worker", None)
    imported = importlib.import_module("pytest_leela._timeout_worker")
    assert imported.__file__ == str(Path(__file__).resolve().parents[2] / "src/pytest_leela/_timeout_worker.py")
    assert waits == [], "import must not wait or execute the worker entry point"
    assert capsys.readouterr().out == ""


def test_guardian_entry_guard_calls_main():
    import ast
    from pathlib import Path
    from pytest_leela import _timeout_guardian as guardian
    entry = ast.parse(Path(guardian.__file__).read_text()).body[-1]
    assert isinstance(entry, ast.If)
    calls = []
    exec(compile(ast.Module(body=[entry], type_ignores=[]), guardian.__file__, "exec"),
         {"__name__": "__main__", "main": lambda: calls.append("main")})
    assert calls == ["main"]


def test_process_delayed_ipc_does_not_resignal_reaped_pid(monkeypatch):
    import io
    import threading
    from types import SimpleNamespace
    entered = threading.Event()
    release = threading.Event()
    def delayed_return():
        entered.set()
        release.wait()
    thread = threading.Thread(target=delayed_return)
    worker = WorkerProcess()
    worker.exchange_thread = thread
    signals = []
    process = SimpleNamespace(pid=424242, stdin=io.BytesIO(), stdout=io.BytesIO(), wait=lambda timeout: None)
    worker.process = process
    monkeypatch.setattr("pytest_leela._timeout_process.os.killpg", lambda pid, sig: signals.append(pid))
    thread.start()
    try:
        assert entered.wait(timeout=1)
        with pytest.raises(RuntimeError, match="IPC did not stop"):
            worker.close()
        assert thread.is_alive()
        assert worker.process is None
        worker.close()
        assert signals == [424242]
    finally:
        release.set()
        thread.join(timeout=2)
        process.stdin.close()
        process.stdout.close()
    assert not thread.is_alive()


def test_guardian_delayed_watcher_reports_error_then_drains(tmp_path, monkeypatch):
    import selectors
    import threading
    from pytest_leela import _timeout_guardian as guardian
    data = json.loads(_request(tmp_path))
    data["deadline"] = 5
    request = tmp_path / "request.json"
    request.write_text(json.dumps(data))
    entered = threading.Event()
    release = threading.Event()
    readiness = []
    original = selectors.DefaultSelector
    class DelayedSelector:
        def __enter__(self):
            self.selector = original()
            return self
        def __exit__(self, *args):
            self.selector.close()
        def register(self, *args):
            self.selector.register(*args)
        def select(self):
            ready = self.selector.select()
            entered.set()
            release.wait()
            readiness.append(ready)
            return ready
    before = set(threading.enumerate())
    read, write = os.pipe()
    monkeypatch.setattr(sys, "argv", ["guardian", str(request)])
    monkeypatch.setattr(WorkerProcess, "run", lambda *args: b"{}")
    monkeypatch.setattr(guardian.selectors, "DefaultSelector", DelayedSelector)
    try:
        with os.fdopen(read, "rb", buffering=0) as lifetime:
            monkeypatch.setattr(sys, "stdin", lifetime)
            with pytest.raises(RuntimeError, match="lifetime watcher did not stop"):
                guardian.main()
        assert entered.is_set()
        delayed = set(threading.enumerate()) - before
        assert len(delayed) == 1
        assert all(thread.is_alive() for thread in delayed)
    finally:
        release.set()
        for thread in set(threading.enumerate()) - before:
            thread.join(timeout=2)
        os.close(write)
    assert set(threading.enumerate()) == before
    assert readiness
    assert isinstance(readiness[0], list)
    # Exercise the same delayed selector's normal release path too: callers
    # must receive the real readiness list rather than None.
    probe_read, probe_write = os.pipe()
    try:
        os.write(probe_write, b"ready")
        with DelayedSelector() as selector:
            selector.register(probe_read, selectors.EVENT_READ)
            ready = selector.select()
            assert isinstance(ready, list)
            assert ready[0][0].fd == probe_read
            assert ready[0][1] == selectors.EVENT_READ
    finally:
        os.close(probe_read)
        os.close(probe_write)


def test_worker_import_contract_positive_wait_control(tmp_path, monkeypatch, capsys):
    import importlib
    original = importlib.import_module
    def execute_entry(name, *args, **kwargs):
        module = original(name, *args, **kwargs)
        if name == "pytest_leela._timeout_worker":
            module.main()
        return module
    monkeypatch.setattr(importlib, "import_module", execute_entry)
    with pytest.raises(AssertionError, match="import must not wait"):
        test_a_worker_import_does_not_execute_main(tmp_path, monkeypatch, capsys)


def test_runner_json_fingerprint_preserves_unreliable_and_nested_causes():
    from pytest_leela.runner import _tuple_fingerprint
    assert _tuple_fingerprint(None) is None
    assert _tuple_fingerprint(["ValueError", "message", ["caused by", ["OSError", "cause", None, []]], []]) == (
        "ValueError", "message", ("caused by", ("OSError", "cause", None, ())), (),
    )


def test_isolated_transport_error_reports_elapsed_not_clock_origin(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest_leela.runner as runner
    clock = iter((100.0, 102.5))
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr("pytest_leela._timeout_process.TestProcess.run", lambda *args: b"invalid JSON")
    session = runner.InnerSession(None, ["test_subject"], None, runner.ProjectModuleScope(str(tmp_path)))
    assert session._run_isolated(5.0) is session
    assert session.elapsed == 2.5
    assert "JSONDecodeError" in session.error()
    assert not session.timed_out.is_set()


def test_worker_import_error_restores_absent_collection_root(tmp_path, monkeypatch):
    from pytest_leela._timeout_worker import execute
    from pytest_leela.runner import InnerSession, ProjectModuleScope
    monkeypatch.chdir(tmp_path)
    (tmp_path / "conftest.py").write_text("raise RuntimeError('service unavailable')\n")
    (tmp_path / "test_worker.py").write_text("def test_ready():\n    assert True\n")
    response = execute(json.loads(_request(tmp_path)))
    assert response["collector"]["_rootpath"] is None
    session = InnerSession(None, None, str(tmp_path), ProjectModuleScope(str(tmp_path)))
    session._restore_worker_result(response)
    assert session.collector._rootpath is None
    assert "service unavailable" in session.error()


def test_deliberate_pytest_exit_remains_a_runner_error(tmp_path, monkeypatch):
    from pytest_leela.runner import InnerSession, ProjectModuleScope
    monkeypatch.chdir(tmp_path)
    (tmp_path / "conftest.py").write_text("import pytest\npytest.exit('maintenance window')\n")
    session = InnerSession(None, None, str(tmp_path), ProjectModuleScope(str(tmp_path))).run()
    assert session.collector.deliberate_exit is not None
    assert session.error() == session.collector.deliberate_exit
    assert "pytest.exit" in session.error()


def test_isolated_request_removes_parent_hook_before_transport(tmp_path, monkeypatch):
    from pytest_leela.ast_analysis import find_mutation_points
    from pytest_leela.models import Mutant
    from pytest_leela.runner import InnerSession, ProjectModuleScope, install_hook
    source = "def add(a, b):\n    return a + b\n"
    path = str(tmp_path / "parent_owned.py")
    point = next(p for p in find_mutation_points(source, path, "parent_owned") if p.node_type == "BinOp")
    assert point.original_op == "Add"
    finder = install_hook({"parent_owned": source}, Mutant(point, "Sub", 0), {"parent_owned": path})
    calls = []
    def transport(self, payload, timeout):
        calls.append(json.loads(payload))
        assert finder not in sys.meta_path, "the parent must not execute the active mutation"
        raise OSError("controlled transport outage")
    monkeypatch.setattr("pytest_leela._timeout_process.TestProcess.run", transport)
    try:
        session = InnerSession(finder, ["test_add"], None, ProjectModuleScope(str(tmp_path)))
        session._run_isolated(5.0)
        assert len(calls) == 1
        assert calls[0]["files"] == {"parent_owned": path}
        assert calls[0]["sources"] == {"parent_owned": source}
        assert "controlled transport outage" in session.error()
        assert finder not in sys.meta_path
    finally:
        from pytest_leela.import_hook import remove_hook
        remove_hook(finder)


def test_causal_control_deadline_is_reported_in_completion_phase(tmp_path, monkeypatch):
    from pytest_leela.ast_analysis import find_mutation_points
    from pytest_leela.models import Mutant
    from pytest_leela.runner import run_tests_for_mutant
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    source = "def add(a, b):\n    return a + b\n"
    target = tmp_path / "control_target.py"
    target.write_text(source)
    (tmp_path / "attempt.txt").write_text("0")
    (tmp_path / "test_control.py").write_text(
        "import pytest, threading\nfrom pathlib import Path\n"
        "@pytest.fixture\ndef service():\n"
        "    path = Path('attempt.txt')\n"
        "    attempt = int(path.read_text())\n"
        "    path.write_text(str(attempt + 1))\n"
        "    if attempt == 0:\n        raise OSError('initial outage')\n"
        "    threading.Event().wait()\n"
        "def test_service(service):\n    assert service is None\n"
    )
    point = next(p for p in find_mutation_points(source, str(target), "control_target")
                 if p.node_type == "BinOp")
    assert point.original_op == "Add"
    phases = []
    result = run_tests_for_mutant(
        Mutant(point, "Sub", 0), {"control_target": source}, {"control_target": str(target)},
        test_ids=["test_control.py::test_service"],
        test_times={"test_control.py::test_service": 0.0},
        on_phase=lambda name, budget, expired: phases.append((name, budget, expired)),
    )
    assert result.status == "error"
    assert "control run also failed (leela: mutant timeout)" in result.error
    assert phases[-1] == ("control-done", 5.0, True)
    assert (tmp_path / "attempt.txt").read_text() == "2"


def test_empty_known_modules_refreshes_dependencies_for_causal_control(tmp_path, monkeypatch):
    import builtins
    from pytest_leela.ast_analysis import find_mutation_points
    from pytest_leela.models import Mutant
    from pytest_leela.runner import ProjectModuleScope, run_tests_for_mutant
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(builtins, "_timeout_dependency_imports", 0, raising=False)
    source = "def add(a, b):\n    return a + b\n"
    target = tmp_path / "dependency_target.py"
    target.write_text(source)
    (tmp_path / "dependency_state.py").write_text(
        "import builtins\n"
        "builtins._timeout_dependency_imports += 1\n"
        "available = builtins._timeout_dependency_imports > 1\n"
    )
    (tmp_path / "test_dependency.py").write_text(
        "import pytest\nfrom dependency_target import add\nimport dependency_state\n"
        "@pytest.fixture\ndef service():\n"
        "    if not dependency_state.available:\n        raise OSError('initial dependency unavailable')\n"
        "def test_dependency(service):\n    assert isinstance(add(1, 2), int)\n"
    )
    point = next(p for p in find_mutation_points(source, str(target), "dependency_target")
                 if p.node_type == "BinOp")
    assert point.original_op == "Add"
    import importlib
    # A dependency imported by the caller predates the session snapshot. An
    # explicitly empty fast-clear set must not pin that stale state into the
    # newly discovered clean-control selection.
    importlib.import_module("dependency_state")
    result = run_tests_for_mutant(
        Mutant(point, "Sub", 0), {"dependency_target": source}, {"dependency_target": str(target)},
        test_dir=str(tmp_path), known_user_modules=frozenset(), scope=ProjectModuleScope(str(tmp_path)),
    )
    assert result.status == "error"
    assert "control: passed" in result.error
    assert "confirmation: survived" in result.error


def test_cleanup_error_cannot_leave_the_active_mutation_hook(tmp_path, monkeypatch):
    from pytest_leela.ast_analysis import find_mutation_points
    from pytest_leela.models import Mutant
    from pytest_leela.runner import InnerSession, ProjectModuleScope, install_hook
    from pytest_leela import runner
    source = "value = 1 + 2\n"
    path = str(tmp_path / "cleanup_target.py")
    point = next(p for p in find_mutation_points(source, path, "cleanup_target") if p.node_type == "BinOp")
    finder = install_hook({"cleanup_target": source}, Mutant(point, "Sub", 0), {"cleanup_target": path})
    clears = []
    def clear_framework_caches():
        clears.append("clear")
        if len(clears) == 2:
            raise RuntimeError("framework cache cleanup unavailable")
    monkeypatch.setattr(runner, "_clear_framework_caches", clear_framework_caches)
    monkeypatch.setattr(InnerSession, "_run_pytest", lambda self: None)
    try:
        session = InnerSession(finder, None, None, ProjectModuleScope(str(tmp_path)))
        with pytest.raises(RuntimeError, match="framework cache cleanup unavailable"):
            session.run()
        assert finder not in sys.meta_path, "cleanup failure must not retain a mutation in the caller"
    finally:
        from pytest_leela.import_hook import remove_hook
        remove_hook(finder)


def test_isolated_caller_interrupt_is_not_a_worker_error(tmp_path, monkeypatch):
    from pytest_leela.runner import InnerSession, ProjectModuleScope
    def interrupt(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr("pytest_leela._timeout_process.TestProcess.run", interrupt)
    session = InnerSession(None, ["test_subject"], None, ProjectModuleScope(str(tmp_path)))
    with pytest.raises(KeyboardInterrupt):
        session._run_isolated(5.0)
    assert session.crash is None
    assert not session.timed_out.is_set()


def test_completed_worker_restores_real_collection_root(tmp_path, monkeypatch):
    from pathlib import Path
    from pytest_leela._timeout_worker import execute
    from pytest_leela.runner import InnerSession, ProjectModuleScope
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_worker.py").write_text("def test_complete():\n    assert True\n")
    response = execute(json.loads(_request(tmp_path)))
    session = InnerSession(None, None, str(tmp_path), ProjectModuleScope(str(tmp_path)))
    session._restore_worker_result(response)
    assert session.collector._rootpath == Path(tmp_path)
    assert session.collector.total == 1
    assert session.error() is None


def test_transport_allocation_failure_propagates_without_a_verdict(tmp_path, monkeypatch):
    """Controlled allocation failure at the JSON transport boundary, not real OOM."""
    from pytest_leela.runner import InnerSession, ProjectModuleScope
    failure = MemoryError("transport allocation unavailable")
    def unavailable(*args):
        raise failure
    monkeypatch.setattr("pytest_leela._timeout_process.TestProcess.run", unavailable)
    session = InnerSession(None, ["test_subject"], None, ProjectModuleScope(str(tmp_path)))
    with pytest.raises(MemoryError) as caught:
        session._run_isolated(5.0)
    assert caught.value is failure
    assert session.crash is None
    assert not session.timed_out.is_set()


@pytest.mark.parametrize("namespace", [None, type(sys)("_leela_runtime_namespace")])
def test_pytest_cleanup_preserves_new_namespace_modules(tmp_path, monkeypatch, namespace):
    from pytest_leela.runner import InnerSession, ProjectModuleScope
    import pytest_leela.runner as runner
    name = "_leela_runtime_namespace"
    def run_pytest(*args, **kwargs):
        sys.modules[name] = namespace
        return 0
    monkeypatch.setattr(runner.pytest, "main", run_pytest)
    try:
        session = InnerSession(None, None, None, ProjectModuleScope(str(tmp_path)))
        session._run_pytest()
        assert session.crash is None
        assert sys.modules[name] is namespace
    finally:
        sys.modules.pop(name, None)
