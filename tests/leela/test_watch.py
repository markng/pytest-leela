"""Drive the shipped watch command against the actual Leela pytest plugin."""

import json
import os
from pathlib import Path
from queue import Queue
import signal
import subprocess
import sys
import time
from threading import Thread

import pytest


@pytest.mark.skipif(os.name != "posix", reason="watch mode uses POSIX checkout locking")
def test_local_watch_survivor_then_kill(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    def put(path: str, content: str) -> None:
        dest = tmp_path / path
        incoming = dest.with_suffix(".save")
        incoming.write_text(content)
        incoming.replace(dest)

    git("init")
    (tmp_path / "src").mkdir()
    put("pytest.ini", "[pytest]\npythonpath = src\n")
    put("src/subject.py", "def positive(value):\n    return value > -1\n")
    test_header = (
        "from pathlib import Path\n"
        "import time\n"
        "from subject import positive\n\n"
        "def test_positive():\n"
        "    if Path('.git/hold').exists():\n"
        "        Path('.git/started').touch()\n"
        "        time.sleep(30)\n"
        "    assert positive(1)\n"
    )
    put("test_subject.py", test_header)
    git("add", ".")
    git("-c", "user.email=watch@example.test", "-c", "user.name=Watch Test", "commit", "-m", "base")
    package_parent = str(Path(__file__).resolve().parents[2] / "src")
    env = dict(os.environ, PYTHONPATH=package_parent)
    with (tmp_path / "watch.stderr").open("w") as stderr:
        with subprocess.Popen(
            [sys.executable, "-m", "pytest_leela.watch", "--source", "src", "--interval", "0.04",
             "--debounce", "0.1"],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            start_new_session=True,
        ) as process:
            assert process.stdout is not None
            states: Queue[dict] = Queue()

            def read() -> None:
                assert process.stdout is not None
                for line in process.stdout:
                    states.put(json.loads(line))

            reader = Thread(target=read, daemon=True)
            reader.start()

            def finished(scope: str) -> tuple[dict, list[dict]]:
                begin = states.get(timeout=30)
                result = states.get(timeout=30)
                assert begin["watch"] == "running"
                assert result["watch"] == "process-exited", result
                assert result["scope"] == scope
                assert result["run_id"] == begin["run_id"]
                assert result["revision"] == begin["revision"]
                return result, [json.loads(line) for line in Path(result["events"]).read_text().splitlines()]

            try:
                finished("full")
                duplicate = subprocess.run(
                    [sys.executable, "-m", "pytest_leela.watch", "--source", "src"],
                    cwd=tmp_path, env=env, capture_output=True, text=True, timeout=5,
                )
                assert duplicate.returncode == 2
                assert json.loads(duplicate.stdout)["error"] == "a watcher already owns this checkout"
                put("src/subject.py", "def positive(value):\n    return value > 0\n")
                result, mutants = finished("diff")
                assert result["exit_code"] == 1
                assert any(mutant["verdict"] == "survived" for mutant in mutants)
                put("test_subject.py", test_header + "    assert not positive(0)\n    assert not positive(-1)\n")
                result, mutants = finished("full")
                assert result["exit_code"] == 0, mutants
                assert mutants and all(mutant["verdict"] == "killed" for mutant in mutants)
                # A subsequent source edit must not hide the still-dirty test.
                (tmp_path / ".git/hold").touch()
                put("src/subject.py", "def positive(value):\n    return value > (0)\n")
                began = states.get(timeout=10)
                assert began["watch"] == "running" and began["scope"] == "full"
                deadline = time.monotonic() + 10
                while not (tmp_path / ".git/started").exists() and time.monotonic() < deadline:
                    time.sleep(0.04)
                assert (tmp_path / ".git/started").exists()
                (tmp_path / ".git/hold").unlink()
                put("src/subject.py", "def positive(value):\n    return value >  0\n")
                interrupted = states.get(timeout=5)
                assert interrupted["watch"] == "superseded"
                assert interrupted["run_id"] == began["run_id"]
                assert not list((tmp_path / ".git/leela-watch").glob(f"{began['run_id']}.jsonl"))
                result, _ = finished("full")
                assert result["exit_code"] == 0
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                reader.join(timeout=5)
            assert process.returncode == 130
