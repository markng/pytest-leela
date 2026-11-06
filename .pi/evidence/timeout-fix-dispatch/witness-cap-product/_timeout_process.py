"""Owned exec-process lifetime for budgeted test sessions."""

import os
import json
import queue
import signal
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path

from pytest_leela._timeout_job import WindowsJob


class DeadlineExpired(TimeoutError):
    """Only the parent-owned deadline, never a worker or transport exception."""


class ParentExited(RuntimeError):
    """The caller lifetime pipe closed before the test session completed."""


class DirectTestProcess:
    """Keep the worker unreaped until all its contained processes are stopped."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.job: WindowsJob | None = None
        self.exchange_thread: threading.Thread | None = None
        self.reply: queue.Queue[bytes | Exception] = queue.Queue()

    def _exchange(self, request: bytes) -> None:
        process = self.process
        assert process is not None
        assert process.stdin is not None
        assert process.stdout is not None
        try:
            process.stdin.write(request)
            process.stdin.close()
            response = process.stdout.readline()
            self.reply.put(response)
        except Exception as exc:
            self.reply.put(exc)

    def run(self, request: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        worker = Path(__file__).with_name("_timeout_worker.py")
        try:
            if os.name == "nt":
                self.job = WindowsJob()
            self.process = subprocess.Popen(
                [sys.executable, "-I", "-S", str(worker)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=(os.name != "nt"),
            )
            if self.job is not None:
                self.job.assign(self.process.pid)
            self.exchange_thread = threading.Thread(
                target=self._exchange, args=(request,), daemon=True,
            )
            self.exchange_thread.start()
            try:
                response = self.reply.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise DeadlineExpired("leela: mutant timeout") from exc
            if isinstance(response, Exception):
                raise response
            if not response:
                raise RuntimeError("leela: test worker exited without a result")
            return response
        finally:
            self.close()

    def _group_exited(self, group: int) -> bool:
        snapshot = subprocess.run(
            ["/bin/ps", "-axo", "pid=,pgid=,stat="],
            check=True, capture_output=True, text=True, timeout=1,
        )
        members = []
        for line in snapshot.stdout.splitlines():
            pid, pgid, state = line.split()
            if int(pgid) == group:
                members.append((int(pid), state))
        return bool(members) and all(state.startswith("Z") for _, state in members)

    def close(self) -> None:
        process = self.process
        if self.job is not None:
            self.job.close()
            self.job = None
        if process is None:
            return
        # No poll/communicate/wait before this signal: a dead group leader
        # remains our unreaped child, so its numeric ID cannot be recycled.
        try:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Darwin reports EPERM for an existing zombie-only group.
                    # Never swallow a permission failure involving live members.
                    if sys.platform != "darwin" or not self._group_exited(process.pid):
                        raise
        finally:
            process.wait(timeout=2)
        thread = self.exchange_thread
        if thread is not None:
            thread.join(timeout=2)
            if thread.is_alive():
                raise RuntimeError("leela: worker IPC did not stop after termination")
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        self.process = None


class TestProcess:
    """Caller-side lifetime channel; an exec guardian owns group termination."""

    def run(self, request: bytes, timeout: float) -> bytes:
        payload = json.loads(request)
        payload["timeout"] = timeout
        guardian = Path(__file__).with_name("_timeout_guardian.py")
        with tempfile.TemporaryDirectory(prefix="leela-worker-") as directory:
            request_file = Path(directory, "request.json")
            request_file.write_text(json.dumps(payload))
            return self._run_guardian(guardian, request_file, timeout)

    def _run_guardian(self, guardian: Path, request_file: Path, timeout: float) -> bytes:
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", str(guardian), str(request_file)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=(os.name != "nt"),
        )
        assert process.stdin is not None
        assert process.stdout is not None
        output = process.stdout
        replies: queue.Queue[bytes | Exception] = queue.Queue()

        def exchange() -> None:
            try:
                replies.put(output.readline())
            except Exception as exc:
                replies.put(exc)

        thread = threading.Thread(target=exchange, daemon=True)
        try:
            thread.start()
            try:
                reply = replies.get(timeout=timeout + 5)
            except queue.Empty as exc:
                raise RuntimeError("leela: guardian failed to enforce deadline") from exc
            if isinstance(reply, Exception):
                raise reply
            envelope = json.loads(reply)
            if "failure" in envelope:
                if envelope["failure"] == "DeadlineExpired":
                    raise DeadlineExpired(envelope["message"])
                raise RuntimeError(envelope["message"])
            return json.dumps(envelope["result"]).encode()
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
                raise RuntimeError("leela: guardian cleanup failed")
            thread.join(timeout=2)
            process.stdout.close()
