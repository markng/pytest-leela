"""Shutdown regressions execute in a killable child, never on this checkout."""
import subprocess
import sys
import textwrap

import pytest


def describe_daemon_shutdown():
    @pytest.mark.parametrize('fail', [False, True])
    def it_drains_before_closing_db_and_stops_scheduling(tmp_path, fail):
        script = '''
import io
from pathlib import Path
import threading
from pytest_leela.daemon import LeelaDaemon
from pytest_leela.index import IndexDB
root = Path.cwd()
(root / 'src').mkdir()
(root / 'src/calc.py').write_text('def f():\\n    return 1\\n')
started, release, stopping, finished = [threading.Event() for _ in range(4)]
closed = []
calls = []
original_exit = IndexDB.__exit__
def exit_db(self, *args):
    assert finished.is_set(), 'DB closed while worker still active'
    closed.append(True)
    return original_exit(self, *args)
IndexDB.__exit__ = exit_db
class Engine:
    def __init__(self, db):
        self.db = db
    def run(self, **kwargs):
        calls.append(True)
        started.set()
        assert release.wait(5)
        assert self.db.all_symbols(), 'DB was closed during drain'
        daemon._last_change_at = float('inf')
        daemon._maybe_spawn_reanalysis(self.db)
        finished.set()
        if FAIL:
            raise ValueError('controlled failure')
def factory(db, progress):
    return Engine(db)
out = io.StringIO()
daemon = LeelaDaemon(root, poll_interval=.001, reanalyze_debounce=0,
                     engine_factory=factory, out=out)
original_stop = daemon.stop
def stop():
    original_stop()
    stopping.set()
daemon.stop = stop
original_loop = daemon._loop
def loop(db):
    original_loop(db)
    # Once the poll loop stops, even a newly dirty change must not spawn work.
    daemon._reanalyzing = False
    daemon._last_change_at = float('inf')
    daemon._maybe_spawn_reanalysis(db)
daemon._loop = loop
thread = threading.Thread(target=daemon.run)
thread.start()
try:
    assert started.wait(5)
    daemon.stop()
    assert stopping.wait(5)
    thread.join(.05)
    assert thread.is_alive(), 'run returned before its worker drained'
    assert not closed
finally:
    release.set()
    daemon.stop()
    thread.join(5)
assert not thread.is_alive()
assert not daemon._reanalyzer_thread.is_alive()
assert calls == [True]
assert closed == [True]
assert not daemon._reanalyzing
assert not [t for t in threading.enumerate() if t is not threading.main_thread()]
if FAIL:
    assert 'controlled failure' in out.getvalue()
'''
        result = subprocess.run(
            [sys.executable, '-c', 'FAIL = ' + repr(fail) + '\n' + textwrap.dedent(script)],
            cwd=tmp_path, capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_reanalysis_resets_flag_when_there_are_no_targets(tmp_path):
    import io
    from unittest.mock import MagicMock
    from pytest_leela.daemon import LeelaDaemon
    from pytest_leela.index import IndexDB
    factory = MagicMock()
    daemon = LeelaDaemon(tmp_path, engine_factory=factory, out=io.StringIO())
    daemon._reanalyzing = True
    with IndexDB(tmp_path / 'index.db') as db:
        daemon._run_reanalysis(db, [])
    assert not daemon._reanalyzing
    factory.return_value.run.assert_not_called()
