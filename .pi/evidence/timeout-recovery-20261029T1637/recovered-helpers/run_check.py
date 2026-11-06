"""Disposable command containment for this repair investigation only.

Resource guard read-only reuse; bounded group cleanup on timeout/cancellation.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

here = Path(__file__).resolve().parent
root = here.parents[2]
name, seconds, *command = sys.argv[1:]
limit = float(seconds)
log = here / f'{name}-resources.log'
guard_source = root / '.pi/evidence/final-current-ci/py313/resource_guard_poller.py'
with (here / f'{name}-guard.log').open('wb') as stream:
    guard = subprocess.Popen([sys.executable, str(guard_source), str(os.getpid()),
                              str(root), os.environ['TMPDIR'], str(log),
                              str(limit + 15), '512000', '1073741824', '2'],
                             stdout=stream, stderr=stream)
    try:
        deadline = time.monotonic() + 15
        while not log.exists() or 'free_bytes=' not in log.read_text():
            assert guard.poll() is None, 'guard startup failure'
            assert time.monotonic() < deadline, 'guard readiness timeout'
            time.sleep(0.05)
        assert guard.poll() is None and 'STOP' not in log.read_text()
        print(f'live guard pid={guard.pid}; command={command!r}', flush=True)
        with (here / f'{name}.log').open('wb') as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                code = process.wait(timeout=limit)
            finally:
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
        assert guard.poll() is None and 'STOP' not in log.read_text()
        (here / f'{name}.exit').write_text(str(code) + '\n')
        print(f'exit={code}', flush=True)
    finally:
        if guard.poll() is None:
            guard.terminate()
        guard.wait(timeout=2)
sys.exit(code)
