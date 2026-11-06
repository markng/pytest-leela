"""Single-campaign resource guard; fail-closed except verified owned ENOENT."""
from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

DuResult = subprocess.CompletedProcess[str]
DuRunner = Callable[[str], DuResult]
_MISSING = re.compile(r"^du: (.*): No such file or directory$")


def _real_du(path: str) -> DuResult:
    return subprocess.run(
        ["/usr/bin/du", "-sk", path],
        text=True,
        capture_output=True,
        check=False,
    )


def _measurement(path: str, result: DuResult) -> tuple[int, list[str]]:
    """Validate one complete root measurement and classify only owned ENOENT."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise RuntimeError(f"measurement root missing or not a directory: {root}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError(f"incomplete du output for {root}: {result.stdout!r}")
    fields = lines[0].split(maxsplit=1)
    if len(fields) != 2 or not fields[0].isdigit():
        raise RuntimeError(f"malformed du output for {root}: {lines[0]!r}")
    amount = int(fields[0])
    if amount < 0:
        raise RuntimeError(f"negative du output for {root}: {amount}")
    if result.returncode == 0:
        if result.stderr.strip():
            raise RuntimeError(f"unexpected diagnostics for {root}: {result.stderr!r}")
        return amount, []
    if not result.stderr.strip():
        raise RuntimeError(f"du failed without diagnostic for {root}: {result.returncode}")
    transient: list[str] = []
    for diagnostic in result.stderr.splitlines():
        match = _MISSING.fullmatch(diagnostic)
        if match is None:
            raise RuntimeError(f"unexpected du failure for {root}: {diagnostic!r}")
        missing = Path(match.group(1)).resolve()
        try:
            missing.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(f"unowned missing path for {root}: {missing}") from exc
        if missing == root or missing.exists():
            raise RuntimeError(f"root-loss or non-transient missing path: {missing}")
        transient.append(str(missing))
    if not root.is_dir():
        raise RuntimeError(f"measurement root disappeared: {root}")
    return amount, transient


def measure_root(path: str, runner: DuRunner = _real_du) -> tuple[int, list[str]]:
    """Measure a stable owned root; return explicitly recorded transient paths."""
    return _measurement(path, runner(path))


def evaluate_limits(elapsed: float, growth_kib: int, free_bytes: int, deadline: float,
                    growth_limit_kib: int, minimum_free: int) -> str:
    if elapsed >= deadline:
        return "deadline"
    if growth_kib > growth_limit_kib:
        return "growth"
    if free_bytes < minimum_free:
        return "free-space"
    return ""


def _descendants(parent: int) -> list[int]:
    output = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid="], text=True)
    children: dict[int, list[int]] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2:
            pid, ppid = map(int, fields)
            children.setdefault(ppid, []).append(pid)
    pending = list(children.get(parent, []))
    result: list[int] = []
    while pending:
        pid = pending.pop()
        result.append(pid)
        pending.extend(children.get(pid, []))
    return result


def terminate_owned_descendants(parent: int, guard_pid: int, stream: object) -> list[int]:
    """Terminate descendants of the run shell, never the guard itself or parent."""
    targets = [pid for pid in _descendants(parent) if pid != guard_pid]
    for pid in reversed(targets):
        try:
            os.kill(pid, signal.SIGTERM)
            stream.write(f"terminated descendant={pid}\n")
        except ProcessLookupError:
            pass
    return targets


def run_guard(parent: int, root: Path, temp: Path, log: Path, seconds: float,
              growth_limit_kib: int, minimum_free: int, interval: float) -> None:
    started = time.monotonic()
    baseline_root, root_transient = measure_root(str(root))
    baseline_temp, temp_transient = measure_root(str(temp))
    baseline = baseline_root + baseline_temp
    with log.open("w", buffering=1) as stream:
        stream.write(
            f"baseline_kib={baseline} parent={parent} guard={os.getpid()}\n"
            f"baseline_transient_root={root_transient!r} baseline_transient_temp={temp_transient!r}\n"
        )
        while True:
            try:
                os.kill(parent, 0)
            except ProcessLookupError:
                time.sleep(0.2)
                try:
                    os.kill(parent, 0)
                except ProcessLookupError:
                    stream.write("parent exited; guard finished\n")
                    return
            reason = ""
            try:
                root_kib, root_transient = measure_root(str(root))
                temp_kib, temp_transient = measure_root(str(temp))
                elapsed = time.monotonic() - started
                growth = root_kib + temp_kib - baseline
                stats = os.statvfs(root)
                free = stats.f_bavail * stats.f_frsize
                stream.write(
                    f"elapsed={elapsed:.1f} growth_kib={growth} free_bytes={free} "
                    f"transient_root={root_transient!r} transient_temp={temp_transient!r}\n"
                )
                reason = evaluate_limits(
                    elapsed, growth, free, seconds, growth_limit_kib, minimum_free
                )
            except Exception as exc:
                reason = f"guard-error:{type(exc).__name__}:{exc}"
            if reason:
                stream.write(f"STOP {reason}\n")
                terminate_owned_descendants(parent, os.getpid(), stream)
                return
            time.sleep(interval)


def main() -> None:
    parent = int(sys.argv[1])
    root = Path(sys.argv[2])
    temp = Path(sys.argv[3])
    log = Path(sys.argv[4])
    run_guard(parent, root, temp, log, float(sys.argv[5]), int(sys.argv[6]),
              int(sys.argv[7]), float(sys.argv[8]))


if __name__ == "__main__":
    main()
