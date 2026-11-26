"""Opt-in local polling around the existing Leela diff run."""

from __future__ import annotations

import argparse
import fcntl
from fnmatch import fnmatch
import hashlib
import json
import math
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root)


def _commit(root: Path, ref: str) -> str:
    return _git(root, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()


def _snapshot(root: Path, base: str) -> tuple[str, str, dict[str, bytes]]:
    paths = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    files: dict[str, bytes] = {}
    for name in paths.split(b"\0"):
        if not name:
            continue
        path = root / os.fsdecode(name)
        if path.suffix not in {".py", ".toml", ".ini", ".cfg"} or not path.resolve().is_relative_to(root):
            continue
        try:
            if path.is_file():
                files[os.fsdecode(name)] = hashlib.sha256(path.read_bytes()).digest()
        except FileNotFoundError:
            # An atomic save or rename can finish between is_file and read.
            continue
    return _commit(root, "HEAD"), _commit(root, base), files


def _mode(root: Path, base: str, source: Path, changed: set[str]) -> str:
    """Only use diff mode when every relevant input maps to a tracked source diff."""
    changed_in_git: set[bytes] = set()
    for ref in (base, f"{base}...HEAD"):
        changed_in_git.update(_git(root, "diff", "--name-only", "-z", ref).split(b"\0"))
    changed_in_git.update(
        name for name in _git(root, "ls-files", "-z", "--others", "--exclude-standard").split(b"\0")
        if Path(os.fsdecode(name)).suffix in {".py", ".toml", ".ini", ".cfg"}
    )
    changed_in_git.discard(b"")
    eligible = set(_git(root, "diff", "--name-only", "-z", "--diff-filter=ACMR", base).split(b"\0"))
    for name in changed | {os.fsdecode(path) for path in changed_in_git}:
        path = Path(name)
        if (
            path.suffix != ".py" or not path.is_relative_to(source)
            or path.name == "conftest.py"
            or any(fnmatch(path.name, pattern) for pattern in ("test_*.py", "*_test.py", "describe_*.py"))
            or os.fsencode(name) not in eligible
        ):
            return "full"
    return "diff" if changed else "full"


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Kill only this invocation's process group, including test workers."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


def _status(state: str, **fields: object) -> None:
    print(json.dumps({"watch": state, **fields}), flush=True)


def _seconds(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Source directory relative to the repository root")
    parser.add_argument("--base", default="HEAD", help="Git comparison ref (default: HEAD)")
    parser.add_argument("--interval", type=_seconds, default=0.5, help="Polling interval in seconds")
    parser.add_argument("--debounce", type=_seconds, default=0.75, help="Quiet period in seconds")
    args = parser.parse_args()
    try:
        root = Path(os.fsdecode(_git(Path.cwd(), "rev-parse", "--show-toplevel").rstrip(b"\n")))
        source = (root / args.source).resolve().relative_to(root)
        if source == Path(".") or not (root / source).is_dir():
            parser.error("--source must be a source subdirectory of the checkout")
        # Keep local artifacts outside the working-tree scan.
        git_dir = Path(os.fsdecode(_git(root, "rev-parse", "--absolute-git-dir").rstrip(b"\n")))
        results = git_dir / "leela-watch"
        results.mkdir(exist_ok=True)
        with (results / "lock").open("a") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _status("unknown", error="a watcher already owns this checkout")
                return 2
            last = None
            pending = None
            changed = time.monotonic()
            while True:
                current = _snapshot(root, args.base)
                if current != pending:
                    pending = current
                    changed = time.monotonic()
                if current != last and time.monotonic() - changed >= args.debounce:
                    revision, base, files = current
                    previous: dict[str, bytes] = last[2] if last else {}
                    edited = {name for name in files.keys() | previous.keys() if files.get(name) != previous.get(name)}
                    mode = _mode(root, base, source, edited) if last and last[:2] == current[:2] else "full"
                    scope = ["--diff", base] if mode == "diff" else []
                    run_id = uuid4().hex
                    events = results / f"{run_id}.jsonl"
                    _status("running", run_id=run_id, revision=revision, base=base, scope=mode)
                    stale = False
                    with subprocess.Popen(
                        [
                            sys.executable, "-m", "pytest", "--leela", "--target", str(source),
                            "--leela-jsonl", str(events), *scope,
                        ],
                        cwd=root,
                        stdout=sys.stderr,
                        start_new_session=True,
                    ) as process:
                        try:
                            while process.poll() is None:
                                time.sleep(args.interval)
                                if _snapshot(root, args.base) != current:
                                    stale = True
                                    _stop(process)
                                    break
                        except BaseException:
                            _stop(process)
                            events.unlink(missing_ok=True)
                            raise
                    stale = stale or _snapshot(root, args.base) != current
                    if stale:
                        events.unlink(missing_ok=True)
                        _status("superseded", run_id=run_id, revision=revision, exit_code=process.returncode)
                    else:
                        _status(
                            "process-exited",
                            run_id=run_id,
                            revision=revision,
                            scope=mode,
                            exit_code=process.returncode,
                            events=str(events) if events.is_file() else None,
                        )
                    last = None if stale else current
                    if stale:
                        pending = None
                time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        _status("unknown", error=str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
