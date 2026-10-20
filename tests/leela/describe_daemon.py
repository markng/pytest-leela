"""Tests for pytest_leela.daemon - the filesystem watcher daemon."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock

import pytest

def _daemon_symbol(name: str):
    """Resolve a daemon symbol only when a test uses it."""
    import importlib

    return getattr(importlib.import_module("pytest_leela.daemon"), name)


def ChangeEvent(*args, **kwargs):
    return _daemon_symbol("ChangeEvent")(*args, **kwargs)


def LeelaDaemon(*args, **kwargs):
    return _daemon_symbol("LeelaDaemon")(*args, **kwargs)


class _LazyDaemonStatus:
    @classmethod
    def from_db(cls, *args, **kwargs):
        return _daemon_symbol("DaemonStatus").from_db(*args, **kwargs)


DaemonStatus = _LazyDaemonStatus

from pytest_leela.index import (
    STATE_ANALYZING,
    STATE_CLEAN,
    STATE_DIRTY,
    IndexDB,
    ReconcileResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bump_mtime(path: Path) -> None:
    """Bump a file's mtime by 2 seconds. macOS has second-resolution
    mtimes, so a sub-second sleep isn't enough to be detected by
    ``os.path.getmtime``."""
    st = path.stat()
    os.utime(path, (st.st_atime + 2, st.st_mtime + 2))


def _make_project(root: Path) -> tuple[Path, Path]:
    """Create a tiny source + tests project under ``root``."""
    src_dir = root / "src"
    src_dir.mkdir()
    src = src_dir / "calc.py"
    src.write_text("def add(x: int, y: int) -> int:\n    return x + y\n")
    test_dir = root / "tests"
    test_dir.mkdir()
    (test_dir / "__init__.py").write_text("")
    (test_dir / "test_calc.py").write_text(
        "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    # Give pytest a ``pythonpath = ["src"]`` so ``from calc import``
    # resolves in the collected tests. The real project has this in
    # its own pyproject.toml; we recreate it locally so the daemon's
    # ``pytest … --collect-only`` step can find the source module.
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
    )
    return src, test_dir


def _make_daemon(
    root: Path,
    *,
    poll_interval: float = 0.05,
    reanalyze_debounce: float = 0.1,
    engine_factory=None,
    out: io.StringIO | None = None,
) -> LeelaDaemon:
    return LeelaDaemon(
        project_root=root,
        index_path=root / ".leela" / "index.db",
        poll_interval=poll_interval,
        reanalyze_debounce=reanalyze_debounce,
        engine_factory=engine_factory,
        out=out or io.StringIO(),
    )


def _read(out: io.StringIO) -> str:
    out.seek(0)
    return out.read()


_DYNAMIC_IMPORT_MUTANTS_SQL = (
    "SELECT replacement_op, killed, test_set_hash, analyzed_at "
    "FROM mutants ORDER BY replacement_op"
)
_PACKAGE_MUTANTS_SQL = (
    "SELECT mutants.replacement_op, mutants.killed "
    "FROM mutants JOIN symbols ON symbols.id = mutants.symbol_id "
    "WHERE symbols.file = ? AND symbols.state = 'clean' "
    "ORDER BY mutants.replacement_op"
)


def _watch_rows(index: Path, query: str, parameters: tuple[str, ...] = ()) -> list[tuple]:
    if not index.exists():
        return []
    with closing(sqlite3.connect(index)) as connection:
        ready = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'mutants'"
        ).fetchone()
        if ready is None:
            return []
        return connection.execute(query, parameters).fetchall()


def test_standalone_src_watch_reanalyzes_dynamic_import_after_test_edit(tmp_path: Path):
    """Public watch must mutate calc even when pytest adds src only per session."""
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
    )
    (project / "src/calc.py").write_text("def add(a, b):\n    return a + b\n")
    test = project / "tests/test_calc.py"
    test.write_text("calc = __import__('calc')\ndef test_add():\n"
                    "    assert calc.add(2, 1) > 0\n")
    index = project / ".leela/index.db"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    command = [sys.executable, "-m", "pytest_leela.daemon"]
    watch = subprocess.Popen(
        [*command, "watch", str(project), "--index", str(index),
         "--poll-interval", "0.1", "--reanalyze-debounce", "0.1"],
        cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )

    def rows():
        return _watch_rows(index, _DYNAMIC_IMPORT_MUTANTS_SQL)

    def await_rows(predicate):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = rows()
            if predicate(current):
                return current
            if watch.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError(f"watch exit={watch.poll()}, rows={rows()}")

    def status(expected):
        result = subprocess.run(
            [*command, "status", str(project), "--index", str(index), "--json"],
            cwd=project, env=env, text=True, capture_output=True, timeout=15,
        )
        assert result.returncode == expected, (result.stdout, result.stderr)
        payload = json.loads(result.stdout)
        assert (payload["with_gaps"], payload["pending_reanalysis"]) == (expected, 0)

    try:
        weak = await_rows(lambda r: len(r) == 3 and
                          {op: killed for op, killed, _, _ in r} ==
                          {"None": 1, "Sub": 0, "Mult": 0})
        assert all(test_hash == "" for _, _, test_hash, _ in weak)
        status(1)
        test.write_text("calc = __import__('calc')\ndef test_add():\n"
                        "    assert calc.add(2, 1) == 3\n")
        strong = await_rows(lambda r: len(r) == 3 and
                            all(killed == 1 and test_hash == "" and
                                analyzed_at != dict((op, timestamp) for op, _, _, timestamp in weak)[op]
                                for op, killed, test_hash, analyzed_at in r))
        assert len(strong) == 3
        status(0)
    finally:
        if watch.poll() is None:
            watch.send_signal(__import__("signal").SIGINT)
        stdout, stderr = watch.communicate(timeout=30)
        assert watch.returncode == 0, (stdout, stderr)


def test_standalone_src_watch_preserves_packages_during_origin_matching(
    tmp_path: Path,
):
    """Origin fallback must leave packages to their normal package loader."""
    project = tmp_path / "project"
    package = project / "src/pkg"
    package.mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
    )
    (package / "__init__.py").write_text("from .helper import OFFSET\n")
    (package / "helper.py").write_text("OFFSET = 0\n")
    source = package / "calc.py"
    source.write_text("from . import OFFSET\ndef add(a, b):\n    return a + b + OFFSET\n")
    (project / "tests/test_calc.py").write_text(
        "calc = __import__('pkg.calc', fromlist=['calc'])\n"
        "def test_add():\n"
        "    assert calc.add(2, 1) > 0\n"
    )
    index = project / ".leela/index.db"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    command = [sys.executable, "-m", "pytest_leela.daemon"]
    watch = subprocess.Popen(
        [*command, "watch", str(project), "--index", str(index),
         "--poll-interval", "0.1", "--reanalyze-debounce", "0.1"],
        cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )

    def rows():
        return _watch_rows(index, _PACKAGE_MUTANTS_SQL, (str(source),))

    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = rows()
            if len(current) == 3 or watch.poll() is not None:
                break
            time.sleep(0.05)
        else:
            current = rows()
        # The outer ``+ OFFSET`` is intercepted in pkg.calc: replacing it
        # with multiplication produces zero and is killed, while subtraction
        # remains positive and survives the deliberately weak assertion.
        assert {op: killed for op, killed in current} == {
            "None": 1, "Sub": 0, "Mult": 1,
        }
    finally:
        if watch.poll() is None:
            watch.send_signal(__import__("signal").SIGINT)
        stdout, stderr = watch.communicate(timeout=30)
        assert watch.returncode == 0, (stdout, stderr)


@pytest.mark.parametrize("query,parameters", [
    (_DYNAMIC_IMPORT_MUTANTS_SQL, ()),
    (_PACKAGE_MUTANTS_SQL, ("src/pkg/calc.py",)),
])
def test_watch_rows_waits_for_schema_and_closes_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    query: str, parameters: tuple[str, ...],
):
    index = tmp_path / "index.db"
    real_connect = sqlite3.connect
    connections = []

    class TrackedConnection:
        def __init__(self, path):
            self.connection = real_connect(path)
            self.closed = False
            connections.append(self)

        def execute(self, statement, values=()):
            return self.connection.execute(statement, values)

        def close(self):
            self.closed = True
            self.connection.close()

    monkeypatch.setattr(sqlite3, "connect", TrackedConnection)

    assert _watch_rows(index, query, parameters) == []
    assert connections == []
    with closing(real_connect(index)) as writer:
        assert _watch_rows(index, query, parameters) == []
        assert connections[-1].closed
        assert writer.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == []
        writer.executescript(
            "CREATE TABLE symbols (id TEXT, file TEXT, state TEXT);"
            "CREATE TABLE mutants (symbol_id TEXT, replacement_op TEXT, "
            "killed INTEGER, test_set_hash TEXT, analyzed_at TEXT);"
            "INSERT INTO symbols VALUES ('calc', 'src/pkg/calc.py', 'clean');"
            "INSERT INTO mutants VALUES ('calc', 'Sub', 0, '', 'now');"
        )
    expected = [("Sub", 0, "", "now")] if not parameters else [("Sub", 0)]
    assert _watch_rows(index, query, parameters) == expected
    assert connections[-1].closed
    assert all(connection.closed for connection in connections)

    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        _watch_rows(index, "SELECT missing_column FROM mutants", parameters)
    assert connections[-1].closed


def describe_module_entry_point():
    """Importing the daemon module must not start the daemon.

    ``daemon.py`` runs the CLI only when it is executed as a script. An
    ordinary import — the way the plugin, the tests and any consumer pull
    the module in — must not parse the caller's normal pytest arguments,
    start a watch loop, or exit the interpreter.
    """

    def it_imports_without_running_the_cli() -> None:
        import importlib
        import pytest_leela

        previous_module = sys.modules.pop("pytest_leela.daemon", None)
        previous_attr = getattr(pytest_leela, "daemon", None)
        had_attr = hasattr(pytest_leela, "daemon")
        if had_attr:
            delattr(pytest_leela, "daemon")
        previous_argv = sys.argv[:]
        sys.argv[:] = ["pytest", "-q"]
        try:
            module = importlib.import_module("pytest_leela.daemon")
            assert callable(module.main)
        except SystemExit as exit_signal:
            raise AssertionError(
                f"importing the daemon parsed caller arguments: {exit_signal.code!r}"
            ) from exit_signal
        finally:
            sys.argv[:] = previous_argv
            sys.modules.pop("pytest_leela.daemon", None)
            if previous_module is not None:
                sys.modules["pytest_leela.daemon"] = previous_module
            if had_attr:
                setattr(pytest_leela, "daemon", previous_attr)
            elif hasattr(pytest_leela, "daemon"):
                delattr(pytest_leela, "daemon")


# ---------------------------------------------------------------------------
# ChangeEvent rendering
# ---------------------------------------------------------------------------


def describe_change_event():
    def it_renders_a_plain_event_without_reconcile():
        ev = ChangeEvent(file_path="src/foo.py", kind="modified")
        rendered = ev.render()
        assert "modified" in rendered
        assert "src/foo.py" in rendered
        # Plain events have no diff counts. A mutation that flips
        # ``is None`` to ``is not None`` would route this case to
        # the with-reconcile branch and emit ``(+0 ~0 -0)``.
        assert "(+" not in rendered

    def it_includes_diff_counts_when_reconcile_present():
        rec = ReconcileResult(
            added=frozenset({"a", "b"}),
            removed=frozenset(),
            changed=frozenset({"c"}),
        )
        ev = ChangeEvent(file_path="src/foo.py", kind="modified", reconcile=rec)
        rendered = ev.render()
        assert "+2" in rendered
        assert "~1" in rendered
        assert "-0" in rendered

    def it_emits_a_non_empty_diff_string_for_each_nonempty_field():
        """The with-reconcile branch renders ``+N ~N -N`` for each
        counter. The mutation that inverts ``is None`` would route
        the reconcile-present case to the plain branch, producing
        a string with no ``+``/``~``/``-`` markers. Asserting on
        the format string distinguishes the branches.
        """
        rec = ReconcileResult(
            added=frozenset({"x"}),
            removed=frozenset({"y", "z"}),
            changed=frozenset(),
        )
        ev = ChangeEvent(file_path="src/foo.py", kind="created", reconcile=rec)
        rendered = ev.render()
        assert "+1" in rendered
        assert "~0" in rendered
        assert "-2" in rendered


# ---------------------------------------------------------------------------
# DaemonStatus
# ---------------------------------------------------------------------------


def describe_daemon_status():
    def it_counts_symbols_by_state(tmp_path: Path):
        db_path = tmp_path / "index.db"
        with IndexDB(db_path) as db:
            db.reconcile_file("src/a.py", "def f():\n    return 1\n")
            db.reconcile_file("src/b.py", "def g():\n    return 1\n")
            db.mark_state("src.a:f", STATE_CLEAN)
            status = DaemonStatus.from_db(db)
            assert status.total_symbols == 2
            assert status.clean == 1
            assert status.dirty == 1
            assert status.pending_reanalysis == 1
            # No mutants written yet, so no coverage gaps.
            assert status.with_gaps == 0
            # ``covered`` is the operator-facing "fully tested"
            # bucket: clean symbols with no surviving or
            # uncovered mutants. With nothing in the mutants
            # table, both are 0 except for the lone clean
            # symbol.
            assert status.covered == 1

    def it_renders_three_buckets_not_four_states(tmp_path: Path):
        """Operator-visible status uses three buckets.

        The DB stores four symbol states (``clean`` /
        ``dirty`` / ``stale`` / ``error``). The status line
        collapses those into three buckets the operator
        actually cares about:

        * ``covered`` — fully tested
        * ``with gaps`` — needs more tests
        * ``pending re-analysis`` — engine is on it

        Operators asked for this because ``clean`` in the
        DB means "analysis done", which is misleading — a
        symbol with surviving mutants is still "clean" in
        the DB even though it has real work to do. The new
        display names the buckets after the action they
        imply.
        """
        from pytest_leela.models import Mutant, MutationPoint

        db_path = tmp_path / "index.db"
        with IndexDB(db_path) as db:
            db.reconcile_file("src/a.py", "def f():\n    return 1\n")
            db.reconcile_file("src/b.py", "def g():\n    return 1\n")
            db.reconcile_file("src/c.py", "def h():\n    return 1\n")
            db.mark_state("src.a:f", STATE_CLEAN)  # covered
            db.mark_state("src.b:g", STATE_CLEAN)  # has gaps
            db.mark_state("src.c:h", STATE_DIRTY)  # pending
            db.write_mutant_result(
                symbol_id="src.b:g",
                mutant=Mutant(
                    point=MutationPoint(
                        file_path="src/b.py",
                        module_name="src.b",
                        lineno=1,
                        col_offset=0,
                        node_type="BinOp",
                        original_op="Add",
                        inferred_type="int",
                    ),
                    replacement_op="Sub",
                    mutant_id=1,
                ),
                result=MagicMock(
                    error=None,
                    killed=False,
                    tests_run=3,
                    killing_test=None,
                    time_seconds=0.0,
                    test_ids_run=[],
                    killing_tests=[],
                ),
                source_hash="h1",
                test_set_hash="t1",
            )
            status = DaemonStatus.from_db(db)
            assert status.clean == 2
            assert status.dirty == 1
            assert status.with_gaps == 1
            # covered = clean - with_gaps = 2 - 1 = 1
            assert status.covered == 1
            assert status.pending_reanalysis == 1
            rendered = status.render()
            # The render line must use the three buckets,
            # not the raw DB state names.
            assert "1 covered" in rendered
            assert "1 with gaps" in rendered
            assert "1 pending re-analysis" in rendered
            # The raw four-state breakdown is no longer the
            # primary display — only the three buckets are.
            assert "clean" not in rendered.split("—")[1]
            assert "dirty" not in rendered.split("—")[1]
            assert "stale" not in rendered.split("—")[1]
            assert "error" not in rendered.split("—")[1]

    def it_counts_analyzing_symbols_in_total_and_pending(tmp_path: Path):
        """``STATE_ANALYZING`` symbols must be counted in
        ``total_symbols`` and ``pending_reanalysis`` — otherwise
        in-flight symbols vanish from the status line while the
        re-analysis thread is running.

        Before the fix, ``DaemonStatus.from_db`` only summed
        ``clean + dirty + stale + error``, so a symbol the
        engine had marked ``analyzing`` was invisible.
        """
        db_path = tmp_path / "index.db"
        with IndexDB(db_path) as db:
            db.reconcile_file("src/a.py", "def f():\n    return 1\n")
            db.reconcile_file("src/b.py", "def g():\n    return 1\n")
            db.reconcile_file("src/c.py", "def h():\n    return 1\n")
            db.mark_state("src.a:f", STATE_CLEAN)
            db.mark_state("src.b:g", STATE_ANALYZING)
            db.mark_state("src.c:h", STATE_DIRTY)
            status = DaemonStatus.from_db(db)
            assert status.total_symbols == 3
            assert status.clean == 1
            # analyzing is part of "pending" work.
            assert status.pending_reanalysis == 2  # analyzing + dirty

    def it_does_not_truncate_symbol_names_in_observer_output(tmp_path):
        """The observer log uses the full symbol id.

        Previously the daemon truncated symbol names to the
        last 16 chars of the trailing component
        (``_observer_progress``). That stripped the module
        path, which is the disambiguator for two symbols
        that share a short name across modules (e.g.
        ``runner:_trace`` vs ``plugin:_trace``). Operators
        asked for the full name.
        """
        out = io.StringIO()
        # Long, unambiguous symbol id.
        long_id = "pytest_leela.daemon:LeelaDaemon._check_baseline_tests"

        # Direct call to the formatter: bypass the engine
        # entirely. The method just renders the supplied
        # symbol id; if it truncates, this test fails.
        from pytest_leela.daemon import LeelaDaemon

        daemon = LeelaDaemon(
            project_root=tmp_path,
            index_path=tmp_path / ".leela" / "index.db",
            target_dirs=(".",),
            out=out,
        )
        daemon._emit_observer_summary(long_id, {long_id: {"survived": 1, "error": 0}})
        rendered = out.getvalue()
        assert long_id in rendered
        # Specifically: nothing was truncated. The trailing
        # 16 chars alone would be "_check_baseline_test"
        # (one char short of the full name); assert the full
        # suffix is present.
        assert "_check_baseline_tests" in rendered


def describe_default_target_dirs():
    """The default ``target_dirs`` is ``("src",)`` — not
    ``("target", "src")``.

    ``target`` is in ``_SKIP_DIRS`` (the daemon's own test
    target dir), so including it in the default meant the
    ``target`` entry could never yield a file. The default now
    points only at ``src/``, the conventional source root.
    """

    def test_default_is_src_only(tmp_path: Path):
        from pytest_leela.daemon import LeelaDaemon

        daemon = LeelaDaemon(project_root=tmp_path)
        assert daemon.target_dirs == ("src",)

    def test_target_dir_is_not_walked(tmp_path: Path):
        """With defaults, a file under ``target/`` is NOT walked.

        Even though ``target/`` exists as a directory, it is
        skipped because ``target`` is both absent from the
        default ``target_dirs`` AND present in ``_SKIP_DIRS``.
        """
        from pytest_leela.daemon import LeelaDaemon

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "mod.py").write_text("def f():\n    return 1\n")
        (tmp_path / "target").mkdir()
        (tmp_path / "target" / "tgt.py").write_text("def g():\n    return 2\n")
        daemon = LeelaDaemon(project_root=tmp_path)
        files = list(daemon._iter_all_py())
        # Only src/ is walked by default.
        # Check for the specific /target/ directory, not just the
        # substring "target" (which could appear in tmp_path names).
        assert any("/src/" in str(p) for p in files)
        assert not any(
            p.parent.name == "target" for p in files
        )


# ---------------------------------------------------------------------------
# Initial build
# ---------------------------------------------------------------------------


def describe_initial_build():
    def test_reconciles_existing_source_files(tmp_path: Path):
        src, _tests = _make_project(tmp_path)
        out = io.StringIO()
        daemon = _make_daemon(tmp_path, out=out)
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            # The two functions in calc.py are in the index.
            assert db.count() >= 1
            # The init line is printed.
            assert "[init] reconciled" in _read(out)

    def test_sets_last_change_at_so_the_reanalyzer_fires(tmp_path: Path):
        """Regression: a fresh start used to leave ``_last_change_at``
        unset, so the reanalyzer's debounce guard short-circuited
        forever and the initial dirty symbols were never analyzed.
        The fix: ``_initial_build`` seeds ``_last_change_at`` to a
        time before the debounce window, so the first poll's
        ``_maybe_spawn_reanalysis`` call passes the guard and
        fires the reanalyzer.
        """
        _make_project(tmp_path)
        daemon = _make_daemon(
            tmp_path,
            poll_interval=0.05,
            reanalyze_debounce=10.0,  # would normally gate forever
        )
        with IndexDB(daemon.index_path) as db:
            assert daemon._last_change_at is None
            daemon._initial_build(db)
            assert daemon._last_change_at is not None
            # The seeded timestamp is *before* the debounce window,
            # so the next ``_maybe_spawn_reanalysis`` call must pass
            # the debounce check.
            elapsed = time.monotonic() - daemon._last_change_at
            assert elapsed > daemon.reanalyze_debounce, (
                f"initial build should seed _last_change_at to a "
                f"time before the debounce window, got elapsed={elapsed}"
            )


# ---------------------------------------------------------------------------
# ``status`` subcommand
# ---------------------------------------------------------------------------


def describe_status_subcommand():
    """``python -m pytest_leela.daemon status`` reports the
    current state without running the daemon. It reads the
    index DB, prints the same three-bucket status line the
    daemon emits, and exits with a code that scripts can
    branch on.
    """

    def _populate(db: IndexDB, *, clean: int = 0, dirty: int = 0) -> None:
        """Write ``clean`` clean symbols and ``dirty`` dirty symbols."""
        for i in range(clean):
            db.reconcile_file(f"src/c{i}.py", f"def f{i}():\n    return 1\n")
            db.mark_state(f"src.c{i}:f{i}", STATE_CLEAN)
        for i in range(dirty):
            db.reconcile_file(f"src/d{i}.py", f"def g{i}():\n    return 1\n")
            db.mark_state(f"src.d{i}:g{i}", STATE_DIRTY)

    def it_prints_the_status_line(tmp_path: Path, capsys):
        index = tmp_path / ".leela" / "index.db"
        index.parent.mkdir(parents=True)
        with IndexDB(index) as db:
            _populate(db, clean=2, dirty=1)

        from pytest_leela.daemon import main as daemon_main

        rc = daemon_main(["status", str(tmp_path)])
        captured = capsys.readouterr()
        # Same render format as the live daemon uses.
        assert "3 symbols" in captured.out
        assert "covered" in captured.out
        assert "with gaps" in captured.out
        assert "pending re-analysis" in captured.out
        # No gaps written → 2 covered, 0 with gaps, 1 pending.
        assert "2 covered" in captured.out
        assert "0 with gaps" in captured.out
        assert "1 pending re-analysis" in captured.out
        # 1 dirty → exit 1 (work to do).
        assert rc == 1

    def it_exits_zero_when_nothing_to_do(tmp_path: Path, capsys):
        index = tmp_path / ".leela" / "index.db"
        index.parent.mkdir(parents=True)
        with IndexDB(index) as db:
            # One clean symbol, no gaps → 1 covered, 0 with gaps.
            db.reconcile_file("src/a.py", "def f():\n    return 1\n")
            db.mark_state("src.a:f", STATE_CLEAN)

        from pytest_leela.daemon import main as daemon_main

        rc = daemon_main(["status", str(tmp_path)])
        captured = capsys.readouterr()
        assert "1 covered" in captured.out
        assert "0 with gaps" in captured.out
        assert "0 pending re-analysis" in captured.out
        assert rc == 0

    def it_exits_two_when_index_missing(tmp_path: Path, capsys):
        from pytest_leela.daemon import main as daemon_main

        # Project dir exists, but no index.db inside it.
        rc = daemon_main(["status", str(tmp_path)])
        captured = capsys.readouterr()
        assert rc == 2
        assert "no index" in captured.err
        assert "has the daemon ever run" in captured.err

    def it_exits_two_when_project_missing(capsys):
        from pytest_leela.daemon import main as daemon_main

        rc = daemon_main(["status", "/path/that/does/not/exist"])
        captured = capsys.readouterr()
        assert rc == 2
        assert "not a directory" in captured.err

    def it_lists_gap_symbols_when_requested(tmp_path: Path, capsys):
        from pytest_leela.models import Mutant, MutationPoint

        index = tmp_path / ".leela" / "index.db"
        index.parent.mkdir(parents=True)
        with IndexDB(index) as db:
            # Two clean symbols. Write one uncovered mutant
            # for each so both have gaps.
            db.reconcile_file("src/a.py", "def f():\n    return 1\n")
            db.reconcile_file("src/b.py", "def g():\n    return 1\n")
            db.mark_state("src.a:f", STATE_CLEAN)
            db.mark_state("src.b:g", STATE_CLEAN)
            for sid, file, op, mid in (
                ("src.a:f", "src/a.py", "Sub", 1),
                ("src.b:g", "src/b.py", "Mult", 2),
            ):
                db.write_mutant_result(
                    symbol_id=sid,
                    mutant=Mutant(
                        point=MutationPoint(
                            file_path=file,
                            module_name=sid.split(":")[0],
                            lineno=1,
                            col_offset=0,
                            node_type="BinOp",
                            original_op="Add",
                            inferred_type="int",
                        ),
                        replacement_op=op,
                        mutant_id=mid,
                    ),
                    result=MagicMock(
                        error=None,
                        killed=False,
                        tests_run=0,
                        killing_test=None,
                        time_seconds=0.0,
                        test_ids_run=[],
                        killing_tests=[],
                    ),
                    source_hash="h",
                    test_set_hash="t",
                )

        from pytest_leela.daemon import main as daemon_main

        rc = daemon_main(["status", str(tmp_path), "--gaps"])
        captured = capsys.readouterr()
        # Both symbols listed.
        assert "src.a:f" in captured.out
        assert "src.b:g" in captured.out
        # The gap count is right there in the status line.
        assert "2 with gaps" in captured.out
        # Both symbols have one uncovered mutant each.
        assert "(1 surviving/uncovered)" in captured.out
        # Exit 1 (work to do).
        assert rc == 1

    def it_emits_json_when_requested(tmp_path: Path, capsys):
        import json

        index = tmp_path / ".leela" / "index.db"
        index.parent.mkdir(parents=True)
        with IndexDB(index) as db:
            _populate(db, clean=1)

        from pytest_leela.daemon import main as daemon_main

        rc = daemon_main(["status", str(tmp_path), "--json"])
        captured = capsys.readouterr()
        # Valid JSON, parseable.
        payload = json.loads(captured.out)
        assert payload["total_symbols"] == 1
        assert payload["covered"] == 1
        assert payload["with_gaps"] == 0
        assert payload["pending_reanalysis"] == 0
        assert payload["project"] == str(tmp_path)
        assert rc == 0

    def it_emits_gaps_in_json_when_requested(tmp_path: Path, capsys):
        import json

        from pytest_leela.models import Mutant, MutationPoint

        index = tmp_path / ".leela" / "index.db"
        index.parent.mkdir(parents=True)
        with IndexDB(index) as db:
            db.reconcile_file("src/a.py", "def f():\n    return 1\n")
            db.mark_state("src.a:f", STATE_CLEAN)
            db.write_mutant_result(
                symbol_id="src.a:f",
                mutant=Mutant(
                    point=MutationPoint(
                        file_path="src/a.py",
                        module_name="src.a",
                        lineno=1,
                        col_offset=0,
                        node_type="BinOp",
                        original_op="Add",
                        inferred_type="int",
                    ),
                    replacement_op="Sub",
                    mutant_id=1,
                ),
                result=MagicMock(
                    error=None,
                    killed=False,
                    tests_run=0,
                    killing_test=None,
                    time_seconds=0.0,
                    test_ids_run=[],
                    killing_tests=[],
                ),
                source_hash="h",
                test_set_hash="t",
            )

        from pytest_leela.daemon import main as daemon_main

        rc = daemon_main(["status", str(tmp_path), "--json", "--gaps"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["with_gaps"] == 1
        assert len(payload["gaps"]) == 1
        assert payload["gaps"][0]["symbol_id"] == "src.a:f"
        assert payload["gaps"][0]["n_uncovered_mutants"] == 1
        assert rc == 1

    def test_defaults_to_watch_subcommand(tmp_path: Path):
        """Without an explicit subcommand, ``main`` runs watch.

        Pre-status CLI used ``python -m pytest_leela.daemon
        [PROJECT]`` directly. The new parser requires a
        subcommand, so ``main`` must default to ``watch`` when
        none is given — otherwise the old CLI breaks.
        """
        from pytest_leela.daemon import main as daemon_main

        # Empty argv: with no subcommand, parser accepts the
        # default ``watch`` and tries to start the daemon on
        # ``.``. We don't actually want the daemon to run, so
        # use a SIGINT-friendly check: we just verify the
        # parser routes to the watch path. The simplest way is
        # to inspect args via a side channel — we use a
        # non-existent project path so the watch path errors
        # out with ``not a directory`` instead of blocking.
        rc = daemon_main(["/path/that/does/not/exist"])
        # 2 == ``error: ... not a directory``, which the
        # ``watch`` path emits. If the status path had been
        # taken, the error message would be different.
        assert rc == 2


def describe_cli_help():
    """Help regressions run in bounded subprocesses and temporary projects."""

    def it_preserves_option_only_legacy_arguments(monkeypatch):
        import pytest_leela.daemon as module
        seen = []
        monkeypatch.setattr(module, "_watch", lambda args: seen.append(args) or 0)
        assert module.main(["--verbose", "--poll-interval", "0.25"]) == 0
        assert seen[0].verbose
        assert seen[0].poll_interval == 0.25
        assert seen[0].project == "."

    def it_honors_help_with_a_legacy_project_path(tmp_path):
        result = subprocess.run(
            [sys.executable, "-m", "pytest_leela.daemon", str(tmp_path), "--help"],
            cwd=tmp_path, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--poll-interval" in result.stdout
        assert not (tmp_path / ".leela").exists()

    def it_prints_top_level_help_and_exits(tmp_path: Path) -> None:
        import subprocess

        # Run from a temp dir so any accidental ``.leela/``
        # writes land in the scratch area, not the project.
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest_leela.daemon",
                "--help",
            ],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
        # argparse's help text mentions the prog name and the
        # subcommands.
        assert "leela" in proc.stdout
        assert "watch" in proc.stdout
        assert "status" in proc.stdout
        # No daemon side effects: no index DB, no logs.
        assert not (tmp_path / ".leela").exists()
        # No analysis/re-analysis output (the watcher prints
        # these lines on startup).
        assert "re-analyzing" not in proc.stdout
        assert "symbols" not in proc.stdout

    def it_prints_watch_subcommand_help(tmp_path: Path) -> None:
        import subprocess

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest_leela.daemon",
                "watch",
                "--help",
            ],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
        assert "--index" in proc.stdout
        assert "--poll-interval" in proc.stdout
        assert not (tmp_path / ".leela").exists()

    def it_prints_status_subcommand_help(tmp_path: Path) -> None:
        import subprocess

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest_leela.daemon",
                "status",
                "--help",
            ],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
        assert "--gaps" in proc.stdout
        assert "--json" in proc.stdout
        assert not (tmp_path / ".leela").exists()



# ---------------------------------------------------------------------------
# File scan
# ---------------------------------------------------------------------------


def describe_scan_for_changes():
    def it_detects_a_new_file(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            # Add a brand-new file.
            new_file = tmp_path / "src" / "newmod.py"
            new_file.write_text("def new_fn():\n    return 1\n")
            events = daemon._scan_for_changes(db)
            kinds = [e.kind for e in events]
            assert "created" in kinds

    def it_detects_a_modified_file(tmp_path: Path):
        src, _tests = _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            # Write new content and bump mtime (mtime resolution on
            # macOS is 1 second, so we set it explicitly).
            time.sleep(0.05)
            src.write_text("def add(x: int, y: int) -> int:\n    return x + y + 0\n")
            _bump_mtime(src)
            events = daemon._scan_for_changes(db)
            kinds = [e.kind for e in events]
            assert "modified" in kinds

    def it_detects_a_deleted_file(tmp_path: Path):
        src, _tests = _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            src.unlink()
            events = daemon._scan_for_changes(db)
            kinds = [e.kind for e in events]
            assert "deleted" in kinds
            # The symbol should be removed from the index.
            assert db.count() == 0

    def it_returns_no_events_when_nothing_changed(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            events = daemon._scan_for_changes(db)
            assert events == []


# ---------------------------------------------------------------------------
# Targeted re-analysis on test-file changes
# ---------------------------------------------------------------------------


def describe_targeted_reanalysis_on_test_changes():
    """A new or modified test file should dirty only the source
    symbols those tests actually exercise — not every symbol in
    the project. This is the daemon's incremental-analysis story:
    adding a test for ``change_me.add`` should not re-analyze
    operators, models, engine, etc."""

    def _make_two_symbol_project(root: Path) -> tuple[Path, Path]:
        """A project with two source symbols and one matching test."""
        src_dir = root / "src"
        src_dir.mkdir()
        (src_dir / "calc.py").write_text(
            "def add(x, y):\n    return x + y\n\ndef sub(x, y):\n    return x - y\n"
        )
        test_dir = root / "tests"
        test_dir.mkdir()
        (test_dir / "__init__.py").write_text("")
        (test_dir / "test_add.py").write_text(
            "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n"
        )
        (root / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
        )
        return src_dir, test_dir

    def it_dirts_only_covered_symbol_when_test_file_added(tmp_path: Path):
        """Adding a new test for ``add`` should dirty only ``add``'s
        symbol, not ``sub``'s (which the new test doesn't cover)."""
        _make_two_symbol_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)

            # After initial build both symbols are clean.
            assert db.count() == 2

            # Simulate the engine analyzing both symbols (state=clean).
            for sid in db.all_symbols():
                db.mark_state(sid.id, STATE_CLEAN)

            # Add a new test file that only covers ``sub``.
            new_test = tmp_path / "tests" / "test_sub.py"
            new_test.write_text(
                "from calc import sub\ndef test_sub():\n    assert sub(5, 3) == 2\n"
            )

            daemon._scan_for_changes(db)

            # ``add`` is still CLEAN; ``sub`` is now DIRTY.
            # The new test covers sub, not add.
            for sym in db.all_symbols():
                if sym.id.endswith(":sub"):
                    assert sym.state == STATE_DIRTY, (
                        f"expected sub DIRTY, got {sym.state}"
                    )
                elif sym.id.endswith(":add"):
                    assert sym.state == STATE_CLEAN, (
                        f"expected add CLEAN, got {sym.state}"
                    )

    def it_dirts_covered_symbol_when_test_file_modified(tmp_path: Path):
        """Modifying an existing test file should dirty only the
        symbol that test exercises."""
        src, test_dir = _make_two_symbol_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            for sid in db.all_symbols():
                db.mark_state(sid.id, STATE_CLEAN)

            # Modify the existing test (still covers only ``add``).
            time.sleep(0.05)
            (test_dir / "test_add.py").write_text(
                "from calc import add\n"
                "def test_add():\n    assert add(1, 2) == 3\n"
                "def test_add_zero():\n    assert add(0, 0) == 0\n"
            )
            _bump_mtime(test_dir / "test_add.py")

            daemon._scan_for_changes(db)

            for sym in db.all_symbols():
                if sym.id.endswith(":add"):
                    assert sym.state == STATE_DIRTY
                elif sym.id.endswith(":sub"):
                    assert sym.state == STATE_CLEAN

    def it_reports_missing_source_import_and_keeps_analysis_pending(tmp_path: Path):
        """A new test for code that doesn't exist yet — the
        TDD test-first case — runs and covers 0 source lines.
        No source symbols should be dirtied, but the daemon
        should print a signal so the operator knows."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "future_module.py").write_text("def existing_fn():\n    return 1\n")
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
        )
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            for sid in db.all_symbols():
                db.mark_state(sid.id, STATE_CLEAN)

            # Test for a function that doesn't exist yet.
            (test_dir / "test_future.py").write_text(
                "from future_module import not_yet_written\n"
                "def test_future():\n"
                "    assert not_yet_written(1) == 2\n"
            )
            daemon._scan_for_changes(db)

            # Unknown collection boundaries cannot certify existing_fn clean.
            for sym in db.all_symbols():
                assert sym.state == STATE_DIRTY, (
                    f"expected DIRTY, got {sym.state} for {sym.id}"
                )

            output = _read(daemon.out)
            assert "scoped collection failed" in output, (
                f"expected collection failure in output, got: {output!r}"
            )


# ---------------------------------------------------------------------------
# Re-analysis debounce
# ---------------------------------------------------------------------------


def describe_reanalysis_debounce():
    def test_does_not_spawn_when_change_is_too_recent(tmp_path: Path):
        """If the last change is within the debounce window, don't
        spawn a reanalyzer pass yet - wait for the user to stop
        typing."""
        _make_project(tmp_path)
        engine_mock = MagicMock()
        engine_factory = MagicMock(return_value=engine_mock)
        daemon = _make_daemon(
            tmp_path,
            engine_factory=engine_factory,
            reanalyze_debounce=10.0,  # 10 second debounce
        )
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            # Last change is "just now" - within the 10s debounce.
            daemon._last_change_at = time.monotonic()
            daemon._maybe_spawn_reanalysis(db)
            # Engine was NOT called.
            assert engine_factory.call_count == 0

    def test_does_not_spawn_when_nothing_dirty(tmp_path: Path):
        _make_project(tmp_path)
        engine_mock = MagicMock()
        engine_factory = MagicMock(return_value=engine_mock)
        daemon = _make_daemon(
            tmp_path,
            engine_factory=engine_factory,
            reanalyze_debounce=0.0,
        )
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            # Mark all symbols clean so nothing is dirty.
            for sym in db.all_symbols():
                db.mark_symbol_clean(sym.id)
            daemon._last_change_at = time.monotonic()
            daemon._maybe_spawn_reanalysis(db)
            assert engine_factory.call_count == 0

    def test_spawns_after_debounce_when_dirty(tmp_path: Path):
        _make_project(tmp_path)
        engine_mock = MagicMock()
        engine_mock.run = MagicMock(return_value=MagicMock(results=[]))
        engine_factory = MagicMock(return_value=engine_mock)
        daemon = _make_daemon(
            tmp_path,
            engine_factory=engine_factory,
            reanalyze_debounce=0.0,
        )
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            # The initial build marks symbols dirty. Set a recent
            # change timestamp and trigger the spawner.
            daemon._last_change_at = time.monotonic() - 1.0
            daemon._maybe_spawn_reanalysis(db)
            # Wait for the thread to spawn and complete.
            assert daemon._reanalyzer_thread is not None
            daemon._reanalyzer_thread.join(timeout=2.0)
            assert not daemon._reanalyzer_thread.is_alive()
            assert engine_factory.call_count == 1

    def test_does_not_respawn_when_no_new_change(tmp_path: Path):
        """Regression test: a pass that leaves symbols dirty must not
        trigger a tight re-spawn loop on subsequent polls.
        """
        _make_project(tmp_path)
        engine_mock = MagicMock()
        engine_mock.run = MagicMock(return_value=MagicMock(results=[]))
        engine_factory = MagicMock(return_value=engine_mock)
        daemon = _make_daemon(
            tmp_path,
            engine_factory=engine_factory,
            reanalyze_debounce=0.0,
        )
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            # First spawn: change happened long ago, no prior pass.
            daemon._last_change_at = time.monotonic() - 1.0
            daemon._maybe_spawn_reanalysis(db)
            assert daemon._reanalyzer_thread is not None
            daemon._reanalyzer_thread.join(timeout=2.0)
            assert not daemon._reanalyzer_thread.is_alive()
            assert engine_factory.call_count == 1
            # Second poll: still no new change since the last pass.
            # ``_last_change_at`` is unchanged (or earlier than the
            # pass's start). The spawner must not fire again.
            daemon._maybe_spawn_reanalysis(db)
            assert engine_factory.call_count == 1

    def test_respawns_when_a_new_change_arrives(tmp_path: Path):
        """A change that arrives *after* the last pass *does* trigger
        a fresh pass - but only once."""
        _make_project(tmp_path)
        engine_mock = MagicMock()
        engine_mock.run = MagicMock(return_value=MagicMock(results=[]))
        engine_factory = MagicMock(return_value=engine_mock)
        daemon = _make_daemon(
            tmp_path,
            engine_factory=engine_factory,
            reanalyze_debounce=0.0,
        )
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            daemon._last_change_at = time.monotonic() - 1.0
            daemon._maybe_spawn_reanalysis(db)
            assert daemon._reanalyzer_thread is not None
            daemon._reanalyzer_thread.join(timeout=2.0)
            assert not daemon._reanalyzer_thread.is_alive()
            assert engine_factory.call_count == 1
            # New change arrives after the previous pass.
            time.sleep(0.01)
            daemon._last_change_at = time.monotonic()
            daemon._maybe_spawn_reanalysis(db)
            assert daemon._reanalyzer_thread is not None
            daemon._reanalyzer_thread.join(timeout=2.0)
            assert not daemon._reanalyzer_thread.is_alive()
            assert engine_factory.call_count == 2


# ---------------------------------------------------------------------------
# End-to-end run loop (short timeout)
# ---------------------------------------------------------------------------


def describe_run_loop():
    def test_stops_on_keyboard_interrupt(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path, poll_interval=0.05)

        def _stop_after_a_moment() -> None:
            time.sleep(0.15)
            daemon.stop()

        t = threading.Thread(target=_stop_after_a_moment)
        t.start()
        # ``run()`` is a blocking call. The helper thread will call
        # ``stop()`` after 150ms, which the loop respects at the
        # next poll boundary.
        daemon.run()
        t.join(timeout=1.0)
        assert not t.is_alive()
        # The stop event was set.
        assert daemon._stop.is_set()


# ---------------------------------------------------------------------------
# Source-hash invalidation across edits
# ---------------------------------------------------------------------------


def describe_invalidation_across_edits():
    def test_modified_file_marks_its_symbols_dirty(tmp_path: Path):
        src, _tests = _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            # Mark everything clean (the post-initial-build state is
            # all dirty, so we flip one clean to verify it goes back
            # to dirty on edit).
            for sym in db.all_symbols():
                db.mark_symbol_clean(sym.id)
            assert all(s.state == STATE_CLEAN for s in db.all_symbols())

            # Edit the source so the source hash changes.
            time.sleep(0.05)
            src.write_text("def add(x: int, y: int) -> int:\n    return x + y + 0\n")
            _bump_mtime(src)
            daemon._scan_for_changes(db)

            # Every symbol in the file should now be dirty.
            for sym in db.all_symbols():
                assert sym.state == STATE_DIRTY, (
                    f"expected {sym.id} dirty, got {sym.state}"
                )


# ---------------------------------------------------------------------------
# Progress callback wiring
# ---------------------------------------------------------------------------


def describe_engine_factory_receives_progress_callback():
    """The daemon must hand the engine a progress callback so the
    log can show which symbols and mutations are being processed.

    We don't run the engine here — we just verify the daemon's
    factory is invoked with a non-None ``on_progress`` callable.
    The factory's contract changed from
    ``Callable[[IndexDB], Engine]`` to
    ``Callable[[IndexDB, Callable | None], Engine]``; this test
    pins the new shape."""

    def it_passes_a_progress_callback_to_the_factory(tmp_path):
        captured: dict[str, object] = {}

        def factory(db, on_progress=None):
            captured["db"] = db
            captured["on_progress"] = on_progress
            return MagicMock()

        # Build a trivial source file so the daemon has something
        # to reconcile.
        src = tmp_path / "calc.py"
        src.write_text(
            '"""Tiny arithmetic module."""\n'
            "def add(x: int, y: int) -> int:\n"
            "    return x + y\n"
        )

        daemon = LeelaDaemon(
            project_root=tmp_path,
            index_path=tmp_path / ".leela" / "index.db",
            target_dirs=(".",),
            test_dir=tmp_path,
            engine_factory=factory,
        )
        with IndexDB(daemon.index_path) as db:
            daemon._run_reanalysis(db, pending=["dummy"])

        assert callable(captured.get("on_progress"))
        # The factory must have received the same db the daemon
        # was using (so writes are visible).
        assert captured.get("db") is db


def describe_daemon_default_output_stream_lifetime():
    """``LeelaDaemon()`` without ``out=`` must write to the ``sys.stdout``
    that is current at construction, not the one captured when the module
    was first imported. pytest capture streams are closed at fixture
    teardown, so an import-time snapshot can retain a closed stream and the
    first ``_print`` then raises ``ValueError: I/O operation on closed
    file`` (observed in the installed regression run). This reproduces that
    exact sequence in a fresh subprocess: import under one stream, close
    it, then construct and print under a replacement stream. A fresh
    subprocess is required so the module-level import happens exactly once
    under our controlled stream — reusing the test session's already
    imported module (or reloading it) would not exercise the real lifetime."""

    def it_resolves_stdout_at_construction_not_at_import(tmp_path):
        script = (
            "import io\n"
            "import sys\n"
            "first = io.StringIO()\n"
            "sys.stdout = first\n"
            "import pytest_leela.daemon as daemon_module\n"
            "first.close()  # what pytest capture teardown does\n"
            "second = io.StringIO()\n"
            "sys.stdout = second\n"
            "daemon = daemon_module.LeelaDaemon(project_root=sys.argv[1])\n"
            "daemon._print('constructed-against-new-stream')\n"
            "assert second.getvalue() == 'constructed-against-new-stream\\n', second.getvalue()\n"
            "explicit = io.StringIO()\n"
            "with_explicit = daemon_module.LeelaDaemon(project_root=sys.argv[1], out=explicit)\n"
            "assert with_explicit.out is explicit, 'explicit out identity must be preserved'\n"
            "with_explicit._print('explicit-stream')\n"
            "assert explicit.getvalue() == 'explicit-stream\\n', explicit.getvalue()\n"
            "sys.stderr.write('STREAM-LIFETIME-OK\\n')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        assert "STREAM-LIFETIME-OK" in proc.stderr


def describe_reanalysis_test_discovery_arguments():
    def it_leaves_unselected_tests_to_normal_pytest_discovery(tmp_path):
        src = tmp_path / "src"
        tests = tmp_path / "tests"
        src.mkdir()
        tests.mkdir()
        (src / "calc.py").write_text("def add(x, y): return x + y\n")
        for name in ("test_calc.py", "describe_calc.py", "utility.py", "__init__.py"):
            (tests / name).write_text("\n")
        received = []

        class RecordingEngine:
            def __init__(self, db, on_progress=None):
                pass

            def run(self, target_files, test_dir=None, test_node_ids=None):
                received.append((target_files, test_dir, test_node_ids))

        daemon = _make_daemon(
            tmp_path, engine_factory=lambda db, on_progress=None: RecordingEngine(db)
        )
        with IndexDB(daemon.index_path) as db:
            daemon._run_reanalysis(db, pending=["calc:add"])
        assert len(received) == 1
        targets, test_dir, node_ids = received[0]
        assert str(src / "calc.py") in targets
        assert test_dir == str(tests)
        assert node_ids is None


def describe_run_reanalysis_emits_per_symbol_log_lines():
    """The reanalysis loop must print one summary line per symbol
    plus one line per mutant, so the operator can see *what* the
    daemon is doing — not just ``status: 30 clean, ...``."""

    def it_prints_symbol_start_summary_and_done(tmp_path, capsys, monkeypatch):
        # Use a real Engine (not a mock) so the engine progress
        # callback actually fires against a tiny mutation. We
        # drive it via the daemon's _run_reanalysis path.

        # Source with one symbol, one mutation.
        src_dir = tmp_path / "src"
        tests_dir = tmp_path / "tests"
        src_dir.mkdir()
        tests_dir.mkdir()
        (src_dir / "calc.py").write_text(
            '"""Tiny arithmetic."""\n'
            "def add(x: int, y: int) -> int:\n"
            "    return x + y\n"
        )
        (tests_dir / "test_calc.py").write_text(
            "from src.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(str(tmp_path))

        out = io.StringIO()
        daemon = LeelaDaemon(
            project_root=tmp_path,
            index_path=tmp_path / ".leela" / "index.db",
            target_dirs=("src",),
            test_dir=tests_dir,
            out=out,
        )
        # Reconcile so the symbol exists and is dirty.
        with IndexDB(daemon.index_path) as db:
            db.reconcile_file(
                str(src_dir / "calc.py"),
                (src_dir / "calc.py").read_text(),
            )
            daemon._run_reanalysis(db, pending=["dummy"])

        captured = out.getvalue()
        # We expect at least one [analyze] line and a final
        # [analyze] done line.
        assert "[analyze]" in captured
        assert "[analyze] done" in captured


def describe_run_reanalysis_hides_cache_hits():
    """The daemon log must NOT show cache-hit mutants.

    The user asked for the log to focus on what the daemon
    *did* in response to a change — change events + actual
    re-analysis. Cache-hit mutants represent "nothing
    happened here" and clutter the log with noise. The
    reanalyzer loop filters them out before they reach the
    output stream.

    This test pins that behavior: even when the engine emits
    a ``cache-hit`` event, the daemon's output must not
    contain ``cache-hit`` anywhere.
    """

    def it_filters_cache_hits_from_the_output(tmp_path):
        from pytest_leela.models import EngineProgress

        # Stub the engine with a fake that emits a mix of
        # killed / cache-hit / survived events. The daemon's
        # on_progress callback is what gets exercised — we
        # want to verify the daemon drops cache-hits and
        # prints the rest.
        class FakeEngine:
            def __init__(self, db, on_progress=None):
                self._on_progress = on_progress

            def run(self, target_files, test_dir=None, test_node_ids=None):
                assert self._on_progress is not None
                # Symbol A: killed + cache-hit + survived.
                # The cache-hit must be dropped.
                self._on_progress(
                    EngineProgress(
                        kind="symbol-start",
                        file_path="/fake/a.py",
                        lineno=10,
                        op="",
                        status="",
                        symbol_id="symA",
                        n_mutants_in_symbol=3,
                    )
                )
                self._on_progress(
                    EngineProgress(
                        kind="mutant",
                        file_path="/fake/a.py",
                        lineno=11,
                        op="Sub",
                        status="killed",
                        symbol_id="symA",
                        killing_test="tests/test_a.py::test_x",
                    )
                )
                self._on_progress(
                    EngineProgress(
                        kind="mutant",
                        file_path="/fake/a.py",
                        lineno=12,
                        op="Mult",
                        status="cache-hit",
                        symbol_id="symA",
                    )
                )
                self._on_progress(
                    EngineProgress(
                        kind="mutant",
                        file_path="/fake/a.py",
                        lineno=13,
                        op="Add",
                        status="survived",
                        symbol_id="symA",
                    )
                )
                # Symbol B: only cache-hits. Should produce no
                # output at all (no header, no mutants, no
                # summary) because every event for it is
                # cache-hit.
                self._on_progress(
                    EngineProgress(
                        kind="symbol-start",
                        file_path="/fake/b.py",
                        lineno=1,
                        op="",
                        status="",
                        symbol_id="symB",
                        n_mutants_in_symbol=2,
                    )
                )
                self._on_progress(
                    EngineProgress(
                        kind="mutant",
                        file_path="/fake/b.py",
                        lineno=2,
                        op="Sub",
                        status="cache-hit",
                        symbol_id="symB",
                    )
                )

        # Build a trivial project so LeelaDaemon has somewhere
        # to point its index.
        src = tmp_path / "src"
        tests = tmp_path / "tests"
        src.mkdir()
        tests.mkdir()
        (src / "a.py").write_text("def a():\n    return 1\n")

        out = io.StringIO()
        daemon = LeelaDaemon(
            project_root=tmp_path,
            index_path=tmp_path / ".leela" / "index.db",
            target_dirs=("src",),
            test_dir=tests,
            out=out,
            verbose=True,  # required for per-symbol log
            engine_factory=lambda db, on_progress=None: FakeEngine(db, on_progress),
        )
        with IndexDB(daemon.index_path) as db:
            # pending = the symbols we want to see in the log.
            # symB is NOT in pending so the daemon should also
            # drop it (the engine processes every file but the
            # log only cares about dirty symbols).
            daemon._run_reanalysis(db, pending=["symA"])

        captured = out.getvalue()
        # Cache-hit must not appear anywhere.
        assert "cache-hit" not in captured
        # Real work for symA must appear. The mutant line
        # specifically must include the symbol id (``symA``)
        # in the position right before ``.L`` — a ``or`` →
        # ``and`` mutation in ``_print_mutant`` would replace
        # the symbol id with ``"?"`` and the line would read
        # ``?.L2 Sub killed`` instead. The header line above
        # already mentions ``symA`` so a naive ``in`` check
        # passes even with the mutation; pin the format here.
        assert "symA.L11 Sub killed" in captured
        assert "symA.L13 Add SURVIVED" in captured
        assert "Sub killed" in captured
        assert "Add SURVIVED" in captured
        # symB is entirely cache-hit AND not in pending, so it
        # must not appear at all.
        assert "symB" not in captured
        # The boilerplate frame is still there.
        assert "[analyze] re-analyzing 1 dirty symbol(s)" in captured
        assert "[analyze] done" in captured


def describe_observer_mode():
    """``--observer`` surfaces only 'work to do' signals.

    The observer mode is the daemon's way of telling a
    human (or another tool) where their attention is needed:

    * ``survived`` mutants — weak coverage. Tests run but
      didn't catch the mutation.
    * ``error`` mutants — no test coverage at all.
    * Baseline test failures — tests that don't pass on
      the unmutated code.

    Killed mutants are deliberately silent (they are good
    news — a test caught the mutation).

    Output is aggregated to one line per symbol so the
    operator sees "pieces of code that need work" rather
    than a torrent of per-mutant detail.
    """

    def it_surfaces_only_survived_and_error_mutants(tmp_path):
        from pytest_leela.models import EngineProgress

        class FakeEngine:
            def __init__(self, db, on_progress=None):
                self._cb = on_progress

            def run(self, target_files, test_dir=None, test_node_ids=None):
                assert self._cb is not None
                self._cb(
                    EngineProgress(
                        kind="symbol-start",
                        file_path="/fake/a.py",
                        lineno=10,
                        op="",
                        status="",
                        symbol_id="symA",
                        n_mutants_in_symbol=4,
                    )
                )
                # killed: silent (good news).
                self._cb(
                    EngineProgress(
                        kind="mutant",
                        file_path="/fake/a.py",
                        lineno=11,
                        op="Sub",
                        status="killed",
                        symbol_id="symA",
                        killing_test="tests/test_a.py::test_x",
                    )
                )
                # survived: must be counted.
                self._cb(
                    EngineProgress(
                        kind="mutant",
                        file_path="/fake/a.py",
                        lineno=12,
                        op="Mult",
                        status="survived",
                        symbol_id="symA",
                    )
                )
                # cache-hit: silent.
                self._cb(
                    EngineProgress(
                        kind="mutant",
                        file_path="/fake/a.py",
                        lineno=13,
                        op="Div",
                        status="cache-hit",
                        symbol_id="symA",
                    )
                )
                # error: must be counted.
                self._cb(
                    EngineProgress(
                        kind="mutant",
                        file_path="/fake/a.py",
                        lineno=14,
                        op="Add",
                        status="error",
                        symbol_id="symA",
                    )
                )
                # Transition to a non-dirty symbol: must NOT
                # produce any observer output.
                self._cb(
                    EngineProgress(
                        kind="symbol-start",
                        file_path="/fake/b.py",
                        lineno=1,
                        op="",
                        status="",
                        symbol_id="symB",
                        n_mutants_in_symbol=2,
                    )
                )
                self._cb(
                    EngineProgress(
                        kind="mutant",
                        file_path="/fake/b.py",
                        lineno=2,
                        op="Sub",
                        status="survived",
                        symbol_id="symB",
                    )
                )

        src = tmp_path / "src"
        tests = tmp_path / "tests"
        src.mkdir()
        tests.mkdir()
        (src / "a.py").write_text("def a():\n    return 1\n")

        out = io.StringIO()
        daemon = LeelaDaemon(
            project_root=tmp_path,
            index_path=tmp_path / ".leela" / "index.db",
            target_dirs=("src",),
            test_dir=tests,
            out=out,
            observer=True,
            engine_factory=lambda db, on_progress=None: FakeEngine(db, on_progress),
        )
        with IndexDB(daemon.index_path) as db:
            daemon._run_reanalysis(db, pending=["symA"])

        captured = out.getvalue()
        # Aggregated per-symbol summary: one line, with counts.
        assert "[observer] symA" in captured
        assert "1 weak coverage" in captured
        assert "1 no coverage" in captured
        # The good-news lines must NOT appear.
        assert "killed" not in captured
        assert "cache-hit" not in captured
        # Non-dirty symbols must NOT appear in the output.
        assert "symB" not in captured
        # Boilerplate.
        assert "[observer] running baseline tests" in captured
        assert "[analyze] done" in captured


# ---------------------------------------------------------------------------
# Unit tests for the targeted-reanalysis building blocks
# ---------------------------------------------------------------------------


def describe_is_test_file():
    """``_is_test_file`` classifies a path as living under the
    resolved test dir. Used by the daemon to distinguish source
    file events (handled by the existing reconcile path) from
    test file events (handled by ``_handle_test_file_changes``).
    """

    def it_returns_true_for_a_file_in_tests_dir(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        assert daemon._is_test_file(str(tmp_path / "tests" / "test_x.py"))

    def it_returns_false_for_a_file_in_src_dir(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        assert not daemon._is_test_file(str(tmp_path / "src" / "calc.py"))

    def it_returns_false_when_there_is_no_test_dir(tmp_path: Path):
        # tmp_path has no tests/ or test/ subdir.
        daemon = _make_daemon(tmp_path)
        assert daemon._is_test_file(str(tmp_path / "tests" / "x.py")) is False


def describe_dirty_symbols_from_deleted_test_file():
    """When a test file is deleted, recover the symbols it had
    been covering so they can be re-analyzed. The implementation
    queries the ``symbol_tests`` table first (the "A" approach)
    and falls back to ``mutants.details.test_ids_run`` (the "B"
    approach) so it works either way."""

    def it_returns_empty_set_when_no_coverage_data(tmp_path: Path):
        """Cold-start project: no mutants have been analyzed yet,
        so we don't know what the deleted tests covered. Mark
        nothing dirty — better than over-invalidating."""
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        with IndexDB(daemon.index_path) as db:
            deleted = str(tmp_path / "tests" / "test_calc.py")
            result = daemon._dirty_symbols_from_deleted_test_file(db, deleted)
            assert result == set()


def describe_handle_test_file_changes_failure_paths():
    """Coverage collection can fail (broken syntax in the new test
    file, etc.). The daemon must swallow the exception and log a
    message rather than crashing the loop."""

    def it_survives_when_collect_coverage_raises(tmp_path: Path):
        from unittest.mock import patch

        src, _tests = _make_project(tmp_path)
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            for sid in db.all_symbols():
                db.mark_state(sid.id, STATE_CLEAN)

            with patch(
                "pytest_leela.coverage_tracker.collect_coverage_subprocess",
                side_effect=RuntimeError("boom"),
            ):
                # Adding a test file triggers the collection;
                # we patched it to raise. The daemon should
                # log and continue.
                new_test = tmp_path / "tests" / "test_extra.py"
                new_test.write_text("def test_x(): pass\n")
                daemon._scan_for_changes(db)

            # Unknown execution coverage conservatively keeps analysis pending.
            for sym in db.all_symbols():
                assert sym.state == STATE_DIRTY

            # Daemon logged the failure.
            output = _read(daemon.out)
            assert "scoped collection failed" in output, output

    def it_reports_missing_function_import_in_the_daemon_log(tmp_path: Path):
        """A failed import reports unknown coverage and keeps work pending."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "future_module.py").write_text("def existing_fn():\n    return 1\n")
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
        )
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            for sid in db.all_symbols():
                db.mark_state(sid.id, STATE_CLEAN)

            # Test for a function that doesn't exist yet.
            (test_dir / "test_future.py").write_text(
                "from future_module import not_yet_written\n"
                "def test_future():\n"
                "    assert not_yet_written(1) == 2\n"
            )
            daemon._scan_for_changes(db)

            # Failed collection cannot certify existing_fn clean.
            for sym in db.all_symbols():
                assert sym.state == STATE_DIRTY, (
                    f"expected DIRTY, got {sym.state} for {sym.id}"
                )

            # The TDD signal appears in the log.
            output = _read(daemon.out)
            assert "scoped collection failed" in output, output


def describe_scan_test_files():
    """``_scan_test_files`` detects added, modified, and deleted
    test files using a separate mtime map. The daemon's main
    ``_iter_all_py`` skips the test dir, so this is the only
    place we look at tests."""

    def it_detects_new_test_files(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        # Prime _test_mtimes so only newly added files show up.
        daemon._scan_test_files()

        new_test = tmp_path / "tests" / "test_new.py"
        new_test.write_text("def test_new(): pass\n")

        added, modified, deleted = daemon._scan_test_files()
        assert any(str(new_test) == a for a in added)
        assert modified == []
        assert deleted == []

    def it_detects_modified_test_files(tmp_path: Path):
        import time as _time

        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        # Prime mtimes.
        added, _, _ = daemon._scan_test_files()
        assert added, "expected the seed scan to see test files"

        # Touch one of the existing test files.
        target = tmp_path / "tests" / "test_calc.py"
        _time.sleep(0.05)
        target.write_text(
            "from calc import add\n"
            "def test_add():\n    assert add(1, 2) == 3\n"
            "def test_add_zero():\n    assert add(0, 0) == 0\n"
        )
        _bump_mtime(target)

        added, modified, deleted = daemon._scan_test_files()
        assert added == []
        assert any(str(target) == m for m in modified)
        assert deleted == []

    def it_detects_deleted_test_files(tmp_path: Path):
        src, _tests = _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        # Prime mtimes.
        daemon._scan_test_files()

        target = tmp_path / "tests" / "test_calc.py"
        target.unlink()

        added, modified, deleted = daemon._scan_test_files()
        assert added == []
        assert modified == []
        assert any(str(target) == d for d in deleted)


def describe_collect_coverage_subprocess():
    """``collect_coverage_subprocess`` runs pytest in a fresh
    interpreter and reads the JSON coverage document back. The
    subprocess is needed because in-process ``pytest.main``
    inside an existing pytest run inherits the outer pytest's
    rootdir and config.
    """

    def it_returns_empty_coverage_map_when_target_files_empty(
        tmp_path: Path,
    ) -> None:
        from pytest_leela.coverage_tracker import (
            collect_coverage_subprocess,
        )

        cov = collect_coverage_subprocess(
            target_files=[],
            test_node_ids=[],
            cwd=str(tmp_path),
        )
        # Empty input should produce an empty map without error.
        assert cov.line_to_tests == {}


def describe_emit_symbol_summary():
    """``_emit_symbol_summary`` prints one log line per analyzed
    symbol, summarising the killed / survived / error counts.
    The ``if not counts: return`` early-return is a gap in the
    daemon — exercising both branches distinguishes a ``Not``
    mutation that flips it to ``if counts:``.
    """

    def it_emits_no_line_when_symbol_id_is_not_in_tally(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        # tally doesn't contain this symbol; counts = {}.
        # The early-return path must NOT print anything.
        daemon._emit_symbol_summary(
            "missing.module:foo", {"other.module:bar": {"killed": 1}}
        )
        assert _read(daemon.out) == ""

    def it_emits_a_summary_line_when_counts_are_present(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        daemon._emit_symbol_summary(
            "module:symbol", {"module:symbol": {"killed": 2, "survived": 1}}
        )
        output = _read(daemon.out)
        assert "module:symbol" in output
        assert "3 mutants" in output
        assert "2 killed" in output
        assert "1 survived" in output


def _summary_emitter(events):
    """Return an engine factory whose ``run`` replays ``events``.

    ``events`` is a list of ``EngineProgress``; the daemon's own
    ``on_progress`` callback consumes them, which is the only way
    to reach the per-symbol flush guards in ``_run_reanalysis``.
    """

    class ReplayEngine:
        def __init__(self, db, on_progress=None):
            self._on_progress = on_progress

        def run(self, target_files, test_dir=None, test_node_ids=None):
            assert self._on_progress is not None
            for ev in events:
                self._on_progress(ev)

    return lambda db, on_progress=None: ReplayEngine(db, on_progress)


def _symbol_start(symbol_id, lineno=1, n=1):
    from pytest_leela.models import EngineProgress

    return EngineProgress(
        kind="symbol-start",
        file_path="/fake/a.py",
        lineno=lineno,
        op="",
        status="",
        symbol_id=symbol_id,
        symbol_short=symbol_id,
        n_mutants_in_symbol=n,
    )


def _mutant(symbol_id, status, lineno=2, op="Sub"):
    from pytest_leela.models import EngineProgress

    return EngineProgress(
        kind="mutant",
        file_path="/fake/a.py",
        lineno=lineno,
        op=op,
        status=status,
        symbol_id=symbol_id,
    )


def _run_verbose_reanalysis(tmp_path, events, pending):
    """Drive ``_run_reanalysis`` in verbose mode and return the log."""
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir(exist_ok=True)
    tests.mkdir(exist_ok=True)
    (src / "a.py").write_text("def a():\n    return 1\n")
    out = io.StringIO()
    daemon = LeelaDaemon(
        project_root=tmp_path,
        index_path=tmp_path / ".leela" / "index.db",
        target_dirs=("src",),
        test_dir=tests,
        out=out,
        verbose=True,
        engine_factory=_summary_emitter(events),
    )
    with IndexDB(daemon.index_path) as db:
        daemon._run_reanalysis(db, pending=pending)
    return out.getvalue()


def describe_run_reanalysis_symbol_summary_tally_guard():
    """The per-symbol summary guard counts real work, not activity.

    ``_run_reanalysis`` flushes a symbol's summary only when that
    symbol recorded some real (non cache-hit) mutant outcome. The
    tally is seeded with zeroed ``killed`` / ``survived`` / ``error``
    counters when the symbol starts and only ever incremented, so
    three cases have to stay distinguishable in the log:

    * **zero** — the symbol started but produced no mutant events;
    * **cache-only** — every event for it was a cache-hit, which the
      progress filter drops before it reaches the tally;
    * **positive** — at least one real outcome was recorded.

    A guard that looked at "did this symbol produce any tally entry"
    instead of "did it record work" would emit a summary line for
    the first two cases; a guard that ignored cache-hits entirely
    would emit one for the third.
    """

    def it_omits_the_summary_for_a_symbol_with_no_mutant_events(tmp_path):
        captured = _run_verbose_reanalysis(
            tmp_path,
            [_symbol_start("symZero", lineno=10, n=2)],
            pending=["symZero"],
        )
        # The header proves the symbol was tracked in this run.
        assert "symZero" in captured
        # No mutants ran, so there is no work to summarise.
        assert "mutants (" not in captured

    def it_omits_the_summary_for_a_symbol_whose_events_are_all_cache_hits(tmp_path):
        captured = _run_verbose_reanalysis(
            tmp_path,
            [
                _symbol_start("symCache", lineno=10, n=2),
                _mutant("symCache", "cache-hit", lineno=11),
                _mutant("symCache", "cache-hit", lineno=12),
            ],
            pending=["symCache"],
        )
        assert "symCache" in captured
        assert "cache-hit" not in captured
        assert "mutants (" not in captured

    def it_emits_the_summary_for_a_symbol_with_real_outcomes(tmp_path):
        captured = _run_verbose_reanalysis(
            tmp_path,
            [
                _symbol_start("symWork", lineno=10, n=3),
                _mutant("symWork", "killed", lineno=11),
                _mutant("symWork", "cache-hit", lineno=12),
                _mutant("symWork", "survived", lineno=13, op="Mult"),
            ],
            pending=["symWork"],
        )
        assert "symWork" in captured
        assert "1 killed" in captured
        assert "1 survived" in captured
        assert "2 mutants (1 killed, 1 survived)" in captured

    def it_omits_a_non_pending_symbol_header(tmp_path):
        captured = _run_verbose_reanalysis(
            tmp_path,
            [_symbol_start("symUnchanged", lineno=10, n=1)],
            pending=["symChanged"],
        )
        assert "symUnchanged" not in captured
        assert "[analyze] done" in captured

    def it_flushes_an_earlier_symbol_summary_when_the_next_symbol_starts(tmp_path):
        # The mid-stream flush guard must behave the same way: a
        # symbol with work is summarised when the next symbol-start
        # arrives, an all-zero one is not.
        captured = _run_verbose_reanalysis(
            tmp_path,
            [
                _symbol_start("symFirst", lineno=10, n=1),
                _mutant("symFirst", "survived", lineno=11),
                _symbol_start("symSecond", lineno=20, n=1),
                _mutant("symSecond", "error", lineno=21),
            ],
            pending=["symFirst", "symSecond"],
        )
        assert "symFirst — 1 mutants (1 survived)" in captured
        # The final symbol is flushed by the end-of-run guard.
        assert "symSecond — 1 mutants (1 error)" in captured


def _seed_gap(db, file_name, symbol_name, *, survived, tests_run=1):
    """Mark ``symbol_name`` clean and give it ``survived`` gap mutants.

    ``survived`` mutants are written with ``tests_run`` so the
    ``--symbol`` detail can distinguish "no test ran" from "tests ran
    but missed".
    """
    from pytest_leela.models import Mutant, MutantResult, MutationPoint

    db.reconcile_file(file_name, f"def {symbol_name}():\n    return 1\n")
    # ``reconcile_file`` derives the canonical symbol id from the path
    # (``src.heavy:h1``); look it up rather than guessing the format.
    symbol_id = next(
        s.id for s in db.all_symbols() if s.id.rsplit(":", 1)[-1] == symbol_name
    )
    db.mark_state(symbol_id, STATE_CLEAN)
    for i in range(survived):
        # Distinct lines: mutants colliding on (line, operator) are
        # stored once, which would flatten the per-symbol gap counts.
        point = MutationPoint(file_name, symbol_name, 2 + i, 11, "BinOp", "Add", None)
        mutant = Mutant(point, "Sub", i)
        db.write_mutant_result(
            symbol_id,
            mutant,
            MutantResult(mutant, False, tests_run, None, 0.01),
            f"hash-{symbol_name}",
            "tests",
        )
    return symbol_id


def _seed_gap_file(db, file_name, gaps):
    """Reconcile one file holding several symbols and seed their gaps.

    ``gaps`` maps symbol name -> number of surviving mutants.
    """
    from pytest_leela.models import Mutant, MutantResult, MutationPoint

    body = "".join(f"def {name}():\n    return 1\n" for name in gaps)
    db.reconcile_file(file_name, body)
    for name, count in gaps.items():
        symbol_id = next(
            s.id for s in db.all_symbols() if s.id.rsplit(":", 1)[-1] == name
        )
        db.mark_state(symbol_id, STATE_CLEAN)
        for i in range(count):
            # Distinct lines so each mutant is stored separately.
            point = MutationPoint(file_name, name, 2 + i, 11, "BinOp", "Add", None)
            mutant = Mutant(point, "Sub", i)
            db.write_mutant_result(
                symbol_id,
                mutant,
                MutantResult(mutant, False, 1, None, 0.01),
                f"hash-{name}",
                "tests",
            )


def _seed_heavy_project(tmp_path):
    """Index with two gap symbols in one file plus one lighter file.

    ``heavy.py`` holds ``h1`` (5 gaps) and ``h2`` (2 gaps) so within-file
    descending order is observable; ``light.py`` holds ``l1`` (1 gap) so
    across-file descending order is observable too.
    """
    index = tmp_path / ".leela" / "index.db"
    index.parent.mkdir(parents=True)
    with IndexDB(index) as db:
        # One reconcile per file, declaring both symbols, so
        # ``reconcile_file`` does not evict the first symbol.
        _seed_gap_file(db, "src/heavy.py", {"h1": 5, "h2": 2})
        _seed_gap_file(db, "src/light.py", {"l1": 1})
    return index


def _seed_mixed_coverage(tmp_path):
    """One clean symbol carrying both an uncovered and a missed mutant."""
    from pytest_leela.models import Mutant, MutantResult, MutationPoint

    index = tmp_path / ".leela" / "index.db"
    index.parent.mkdir(parents=True)
    with IndexDB(index) as db:
        file_name = "src/m.py"
        db.reconcile_file(file_name, "def m():\n    return 1\n")
        symbol_id = next(
            s.id for s in db.all_symbols() if s.id.rsplit(":", 1)[-1] == "m"
        )
        db.mark_state(symbol_id, STATE_CLEAN)
        # Distinct lines so the two mutants do not collide on
        # (line, operator) and both are stored.
        for i, (lineno, tests_run) in enumerate(((2, 0), (3, 3))):
            point = MutationPoint(file_name, "m", lineno, 4, "BinOp", "Add", None)
            mutant = Mutant(point, "Sub", i)
            db.write_mutant_result(
                symbol_id,
                mutant,
                MutantResult(mutant, False, tests_run, None, 0.01),
                "h",
                "tests",
            )
    return index


def _seed_many_categorized(tmp_path, *, no_test_ran=0, tests_ran=0):
    """Index with many gap symbols split across the two categories.

    Each symbol lives in its own file so ``reconcile_file`` does not
    evict the previously seeded symbol.
    """
    index = tmp_path / ".leela" / "index.db"
    index.parent.mkdir(parents=True)
    with IndexDB(index) as db:
        for i in range(no_test_ran):
            _seed_gap(db, f"src/n{i}.py", f"n{i}", survived=1, tests_run=0)
        for i in range(tests_ran):
            _seed_gap(db, f"src/t{i}.py", f"t{i}", survived=1, tests_run=2)
    return index


def describe_status_gap_reporting_ordering():
    """The human-readable gap report lists the worst offenders first.

    Both the per-symbol ordering within a file and the per-file grouping
    sort by descending uncovered-mutant count. A report that surfaced the
    lightly-gapped entries first would send the operator to the least
    useful place first, so the ordering is pinned here.
    """

    def it_orders_by_file_grouping_worst_first(tmp_path: Path, capsys):
        from pytest_leela.daemon import main as daemon_main

        _seed_heavy_project(tmp_path)
        rc = daemon_main(["status", str(tmp_path), "--by-file"])
        out = capsys.readouterr().out
        assert rc == 1
        # heavy.py has 7 gaps, light.py has 1 -> heavy listed first.
        assert out.index("heavy.py") < out.index("light.py")

    def it_orders_within_file_by_descending_gap_count(tmp_path: Path, capsys):
        # The within-file symbol ordering appears in the --by-file JSON
        # ``symbols`` list (each file's gap symbols, worst first).
        import json

        from pytest_leela.daemon import main as daemon_main

        _seed_heavy_project(tmp_path)
        daemon_main(["status", str(tmp_path), "--by-file", "--json"])
        out = capsys.readouterr().out
        payload = json.loads(out)
        heavy = next(f for f in payload["by_file"] if f["file"].endswith("heavy.py"))
        order = [s["symbol_id"] for s in heavy["symbols"]]
        # h1 has 5 gaps and h2 has 2 -> h1 first.
        assert order[0].endswith("h1")
        assert order[1].endswith("h2")

    def it_omits_the_symbol_list_unless_gaps_is_requested(tmp_path: Path, capsys):
        # --by-file alone must not also print the flat per-symbol gap list.
        from pytest_leela.daemon import main as daemon_main

        _seed_heavy_project(tmp_path)
        daemon_main(["status", str(tmp_path), "--by-file"])
        out = capsys.readouterr().out
        assert "files with gaps" in out
        assert "symbols with gaps" not in out

    def it_orders_by_file_grouping_worst_first_in_json(tmp_path: Path, capsys):
        # The machine-readable ``--by-file --json`` payload also ranks
        # files by descending gap count, so tooling that consumes it
        # sees the same worst-first order the human report shows.
        import json

        from pytest_leela.daemon import main as daemon_main

        _seed_heavy_project(tmp_path)
        daemon_main(["status", str(tmp_path), "--by-file", "--json"])
        payload = json.loads(capsys.readouterr().out)
        order = [entry["file"] for entry in payload["by_file"]]
        # heavy.py holds 7 gaps, light.py holds 1.
        assert order[0].endswith("heavy.py")
        assert order[1].endswith("light.py")
        # And the per-symbol detail inside the worst file is ranked too.
        heavy = payload["by_file"][0]
        counts = [s["n_uncovered_mutants"] for s in heavy["symbols"]]
        assert counts == sorted(counts, reverse=True)
        assert counts[0] > counts[-1]

    def it_omits_the_file_breakdown_unless_by_file_is_requested(tmp_path: Path, capsys):
        # --gaps alone must not print the per-file breakdown.
        from pytest_leela.daemon import main as daemon_main

        _seed_heavy_project(tmp_path)
        daemon_main(["status", str(tmp_path), "--gaps"])
        out = capsys.readouterr().out
        assert "symbols with gaps" in out
        assert "files with gaps" not in out


def describe_status_symbol_detail_coverage_wording():
    """``--symbol`` labels each gap mutant by why it survived.

    ``tests_run == 0`` means no test attempted the mutation ("no test
    ran"); otherwise tests ran and missed it. Swapping the two labels
    would misdirect the operator writing new tests.
    """

    def it_labels_uncovered_and_missed_mutants_distinctly(tmp_path: Path, capsys):
        from pytest_leela.daemon import main as daemon_main

        _seed_mixed_coverage(tmp_path)
        daemon_main(["status", str(tmp_path), "--symbol", "src.m:m"])
        out = capsys.readouterr().out
        assert "no test ran" in out
        assert "survived 3 tests" in out


def describe_status_categorized_truncation():
    """``--categorized`` truncates long lists with a count of the rest.

    When more than ten symbols fall into a bucket, the report prints the
    first ten plus an "... and N more" line. That truncation is what keeps
    the terminal readable, so both the boundary (exactly ten) and the
    overflow count are pinned.
    """

    def it_truncates_both_buckets_past_ten(tmp_path: Path, capsys):
        from pytest_leela.daemon import main as daemon_main

        _seed_many_categorized(tmp_path, no_test_ran=12, tests_ran=11)
        rc = daemon_main(["status", str(tmp_path), "--categorized"])
        out = capsys.readouterr().out
        assert rc == 1
        # 12 -> 10 listed + "... and 2 more"; 11 -> 10 + "... and 1 more".
        assert "and 2 more" in out
        assert "and 1 more" in out

    def it_does_not_truncate_at_or_below_ten(tmp_path: Path, capsys):
        from pytest_leela.daemon import main as daemon_main

        _seed_many_categorized(tmp_path, no_test_ran=10, tests_ran=10)
        daemon_main(["status", str(tmp_path), "--categorized"])
        out = capsys.readouterr().out
        assert "more" not in out


def _observer_emitter(events):
    class ReplayEngine:
        def __init__(self, db, on_progress=None):
            self._cb = on_progress

        def run(self, target_files, test_dir=None, test_node_ids=None):
            assert self._cb is not None
            for ev in events:
                self._cb(ev)

    return lambda db, on_progress=None: ReplayEngine(db, on_progress)


def _observer_progress_event(kind, symbol_id, status="", lineno=1, op="Sub"):
    from pytest_leela.models import EngineProgress

    return EngineProgress(
        kind=kind,
        file_path="/fake/a.py",
        lineno=lineno,
        op=op,
        status=status,
        symbol_id=symbol_id,
    )


def _run_observer_reanalysis(tmp_path, events, pending):
    """Drive ``_run_reanalysis`` in observer mode and return the log."""
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir(exist_ok=True)
    tests.mkdir(exist_ok=True)
    (src / "a.py").write_text("def a():\n    return 1\n")
    out = io.StringIO()
    daemon = LeelaDaemon(
        project_root=tmp_path,
        index_path=tmp_path / ".leela" / "index.db",
        target_dirs=("src",),
        test_dir=tests,
        out=out,
        observer=True,
        engine_factory=_observer_emitter(events),
    )
    with IndexDB(daemon.index_path) as db:
        daemon._run_reanalysis(db, pending=pending)
    return out.getvalue()


def describe_observer_progress_edge_cases():
    """Observer mode ignores events it has no work signal for.

    Besides survived/error there are several other engine events:
    a ``symbol-start`` with no symbol id, a terminal ``done`` event,
    and cache-hit / killed mutants. None of those are "work to do",
    so none may be counted or summarised. The tally itself must
    accumulate correctly when several signals hit the same symbol.
    """

    def it_ignores_symbol_start_with_no_symbol_id(tmp_path):
        captured = _run_observer_reanalysis(
            tmp_path,
            [_observer_progress_event("symbol-start", None)],
            pending=["symA"],
        )
        # A symbol-start with no id is neither a header nor work.
        assert "[observer] symA" not in captured
        assert "weak coverage" not in captured

    def it_summarizes_a_pending_symbol_after_another_starts(tmp_path):
        captured = _run_observer_reanalysis(
            tmp_path,
            [
                _observer_progress_event("symbol-start", "symA"),
                _observer_progress_event("mutant", "symA", "survived", lineno=2),
                _observer_progress_event("symbol-start", "symB"),
            ],
            pending=["symA", "symB"],
        )
        assert "[observer] symA — 1 weak coverage" in captured
        assert "[observer] symB" not in captured

    def it_ignores_the_terminal_done_event(tmp_path):
        captured = _run_observer_reanalysis(
            tmp_path,
            [
                _observer_progress_event("symbol-start", "symA"),
                _observer_progress_event("mutant", "symA", "survived", lineno=2),
                _observer_progress_event("done", None, lineno=0, op=""),
            ],
            pending=["symA"],
        )
        # Only the one survived mutant counts; the done event adds none.
        assert "1 weak coverage" in captured

    def it_counts_repeated_signals_for_the_same_symbol(tmp_path):
        captured = _run_observer_reanalysis(
            tmp_path,
            [
                _observer_progress_event("symbol-start", "symA"),
                _observer_progress_event("mutant", "symA", "survived", lineno=2),
                _observer_progress_event("mutant", "symA", "survived", lineno=3),
                _observer_progress_event("mutant", "symA", "error", lineno=4),
            ],
            pending=["symA"],
        )
        # Exact summary line: two survived + one error. An exact match
        # (not a substring) keeps a decremented tally from sneaking
        # through as "-2 weak coverage".
        assert "[observer] symA \u2014 2 weak coverage, 1 no coverage" in captured

    def it_keeps_none_missing_and_present_symbol_summaries_distinct(tmp_path):
        captured = _run_observer_reanalysis(
            tmp_path,
            [
                _observer_progress_event("symbol-start", None),
                _observer_progress_event("symbol-start", "missing"),
                _observer_progress_event("symbol-start", "present"),
                _observer_progress_event("mutant", "present", "survived", lineno=2),
                _observer_progress_event("symbol-start", "other"),
            ],
            pending=["present", "other"],
        )
        assert "[observer] present — 1 weak coverage" in captured
        assert "[observer] missing" not in captured
        assert "[observer] None" not in captured
        assert "[observer] other" not in captured

    def it_omits_symbols_that_are_not_pending(tmp_path):
        # A symbol outside the pending set is not "work the user did",
        # so its surviving/uncovered mutants must not be surfaced even
        # though the engine emits events for every target file.
        captured = _run_observer_reanalysis(
            tmp_path,
            [
                _observer_progress_event("symbol-start", "symNotPending"),
                _observer_progress_event("mutant", "symNotPending", "survived", lineno=2),
            ],
            pending=["symOther"],
        )
        assert "symNotPending" not in captured
        assert "weak coverage" not in captured

    def it_keeps_caller_owned_progress_state_for_all_symbol_start_shapes(tmp_path):
        # These are the real callback records and caller-owned containers
        # used by the daemon. None/nonpending starts must not manufacture
        # tally entries, while a pending start gets its zero baseline.
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        pending_set = {"pending"}
        observer_tally = {
            "unrelated": {"survived": 4, "error": 2},
        }
        observer_current = [None]
        for event in (
            _observer_progress_event("symbol-start", None),
            _observer_progress_event("symbol-start", "pending"),
            _observer_progress_event("symbol-start", "not-pending"),
        ):
            daemon._observer_progress(
                event, pending_set, observer_tally, observer_current,
            )

        assert observer_tally == {
            "unrelated": {"survived": 4, "error": 2},
            "pending": {"survived": 0, "error": 0},
        }
        assert observer_current == ["not-pending"]

    def it_ignores_nonmutant_and_none_id_records_without_changing_tally(tmp_path):
        # Both mixed-shape records are supported callback inputs: a
        # non-mutant with an id and a mutant with no id are not outcomes.
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        pending_set = {"pending"}
        observer_tally = {}
        observer_current = [None]
        daemon._observer_progress(
            _observer_progress_event("symbol-start", "pending"),
            pending_set, observer_tally, observer_current,
        )
        daemon._observer_progress(
            _observer_progress_event("done", "pending", "survived"),
            pending_set, observer_tally, observer_current,
        )
        daemon._observer_progress(
            _observer_progress_event("mutant", None, "survived"),
            pending_set, observer_tally, observer_current,
        )

        assert observer_tally == {
            "pending": {"survived": 0, "error": 0},
        }


def describe_print_mutant_killing_test_suffix():
    """A killed mutant line names the test that caught it.

    The verbose per-mutant line ends with ``by <test name>`` using only
    the trailing ``test_x`` segment of the full node id, which is what
    the operator scans for. The line must carry that suffix and the
    full mutant identity, and the format must not degrade into an
    engine error.
    """

    import pytest

    @pytest.mark.parametrize("killing_test,display_name", [
        ("tests/leela/test_thing.py::describe_thing::test_thing", "test_thing"),
        ("tests/leela/test_thing.py", "tests/leela/test_thing.py"),
        ("<timeout>", "<timeout>"),
    ])
    def it_names_the_killing_test_on_the_killed_line(
        tmp_path, killing_test, display_name,
    ):
        from pytest_leela.models import EngineProgress

        class FakeEngine:
            def __init__(self, db, on_progress=None):
                self._cb = on_progress

            def run(self, target_files, test_dir=None, test_node_ids=None):
                self._cb(
                    EngineProgress(
                        kind="symbol-start",
                        file_path="/fake/a.py",
                        lineno=10,
                        op="",
                        status="",
                        symbol_id="symA",
                        symbol_short="symA",
                        n_mutants_in_symbol=1,
                    )
                )
                self._cb(
                    EngineProgress(
                        kind="mutant",
                        file_path="/fake/a.py",
                        lineno=11,
                        op="Sub",
                        status="killed",
                        symbol_id="symA",
                        killing_test=killing_test,
                    )
                )

        src = tmp_path / "src"
        tests = tmp_path / "tests"
        src.mkdir()
        tests.mkdir()
        (src / "a.py").write_text("def a():\n    return 1\n")
        out = io.StringIO()
        daemon = LeelaDaemon(
            project_root=tmp_path,
            index_path=tmp_path / ".leela" / "index.db",
            target_dirs=("src",),
            test_dir=tests,
            out=out,
            verbose=True,
            engine_factory=lambda db, on_progress=None: FakeEngine(db, on_progress),
        )
        with IndexDB(daemon.index_path) as db:
            daemon._run_reanalysis(db, pending=["symA"])
        captured = out.getvalue()
        # Full identity plus the trailing test name, and no error line.
        assert f"symA.L11 Sub killed by {display_name}" in captured
        assert "[analyze] error" not in captured


def describe_iter_test_files_and_source_roots():
    """Discovery helpers honour the resolved test dir and target dirs.

    ``_iter_test_files`` returns nothing when the resolved test dir does
    not exist, and ``_iter_all_py`` must not yield the test dir twice when
    it already appears among the target dirs.
    """

    def it_yields_no_test_files_when_there_is_no_test_directory(tmp_path):
        daemon = _make_daemon(tmp_path)
        assert list(daemon._iter_test_files()) == []

    def it_yields_no_test_files_when_test_dir_is_not_a_directory(tmp_path):
        from pytest_leela.daemon import LeelaDaemon

        # test_dir points at a regular file, not a directory.
        f = tmp_path / "tests_file"
        f.write_text("not a dir\n")
        daemon = LeelaDaemon(
            project_root=tmp_path,
            index_path=tmp_path / ".leela" / "index.db",
            test_dir=f,
        )
        assert list(daemon._iter_test_files()) == []

    def it_yields_files_when_the_resolved_test_directory_is_empty(tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_one.py").write_text("def test_one(): pass\n")
        daemon = _make_daemon(tmp_path)
        assert list(daemon._iter_test_files()) == [tests / "test_one.py"]

    def it_does_not_duplicate_test_files_already_in_target_dirs(tmp_path):
        from pytest_leela.daemon import LeelaDaemon

        (tmp_path / "src").mkdir()
        # Name the test dir ``spec`` so it is NOT in the daemon's source
        # skip list; ``tests`` is skipped by ``_should_skip`` regardless.
        (tmp_path / "spec").mkdir()
        (tmp_path / "src" / "a.py").write_text("def a():\n    return 1\n")
        (tmp_path / "spec" / "test_a.py").write_text("def test_a():\n    pass\n")
        # Include the test dir in target_dirs so it is already among the
        # roots that ``_iter_all_py`` walks; the resolved test dir must not
        # be appended a second time (which would yield each file twice).
        daemon = LeelaDaemon(
            project_root=tmp_path,
            index_path=tmp_path / ".leela" / "index.db",
            target_dirs=("src", "spec"),
            test_dir=tmp_path / "spec",
        )
        paths = [str(p) for p in daemon._iter_all_py()]
        assert paths.count(str(tmp_path / "spec" / "test_a.py")) == 1
        assert str(tmp_path / "src" / "a.py") in paths


def describe_check_baseline_tests():
    """``_check_baseline_tests`` always hands the observer a list.

    The observer callback does ``for failed_id in self._check_baseline_tests()``,
    so every early return must yield a list, never ``None``. The function
    returns an empty list when the suite passes, when there is no test dir,
    or when the subprocess cannot run; otherwise it returns the parsed
    failing node ids. A ``return None`` on any of those paths would raise
    ``TypeError`` at the iteration site, so pinning the return type is a real
    behavioural contract, not an implementation detail.
    """

    def _daemon_with_test_dir(tmp_path, test_dir):
        src = tmp_path / "src"
        src.mkdir(exist_ok=True)
        (src / "a.py").write_text("def a():\n    return 1\n")
        return LeelaDaemon(
            project_root=tmp_path,
            index_path=tmp_path / ".leela" / "index.db",
            target_dirs=("src",),
            test_dir=test_dir,
            observer=True,
            out=io.StringIO(),
        )

    def it_returns_empty_list_when_test_dir_is_not_a_directory(tmp_path):
        not_a_dir = tmp_path / "tests_file"
        not_a_dir.write_text("not a directory\n")
        daemon = _daemon_with_test_dir(tmp_path, not_a_dir)
        failures = daemon._check_baseline_tests()
        assert failures == []
        # The observer callback iterates the result directly.
        assert list(failures) == []

    def it_returns_empty_list_when_test_dir_is_missing(tmp_path):
        daemon = _daemon_with_test_dir(tmp_path, tmp_path / "absent")
        failures = daemon._check_baseline_tests()
        assert failures == []

    def it_returns_empty_list_when_the_suite_passes(tmp_path, monkeypatch):
        tests = tmp_path / "tests"
        tests.mkdir()
        daemon = _daemon_with_test_dir(tmp_path, tests)

        class OkResult:
            returncode = 0
            stdout = "1 passed\n"
            stderr = ""

        monkeypatch.setattr("subprocess.run", lambda *a, **k: OkResult())
        failures = daemon._check_baseline_tests()
        assert failures == []

    def it_parses_failed_lines_from_the_baseline_run(tmp_path, monkeypatch):
        tests = tmp_path / "tests"
        tests.mkdir()
        daemon = _daemon_with_test_dir(tmp_path, tests)

        class FailResult:
            returncode = 1
            stdout = (
                "FAILED tests/test_a.py::test_one - assert 0 == 1\n"
                # Exactly two fields: the summary may omit the trailing
                # reason, so a parser that demanded a third token would
                # silently drop this failure.
                "FAILED tests/test_b.py::test_two\n"
                "FAILED tests/test_c.py::test_three - boom\n"
                "not a failure line\n"
            )
            stderr = ""

        monkeypatch.setattr("subprocess.run", lambda *a, **k: FailResult())
        failures = daemon._check_baseline_tests()
        assert failures == [
            "tests/test_a.py::test_one",
            "tests/test_b.py::test_two",
            "tests/test_c.py::test_three",
        ]

    def it_returns_empty_list_when_the_baseline_subprocess_cannot_run(
        tmp_path, monkeypatch
    ):
        import subprocess as _subprocess

        tests = tmp_path / "tests"
        tests.mkdir()
        daemon = _daemon_with_test_dir(tmp_path, tests)

        def boom(*args, **kwargs):
            raise _subprocess.TimeoutExpired(cmd="pytest", timeout=120)

        monkeypatch.setattr("subprocess.run", boom)
        failures = daemon._check_baseline_tests()
        # A list, so the observer's ``for ... in`` loop still works.
        assert failures == []
        assert "baseline pytest failed to run" in _read(daemon.out)


def describe_resolve_index_path():
    """``--index`` is resolved relative to the project unless absolute.

    ``status --index X`` must read ``<project>/X`` for a relative path
    and use an absolute path verbatim. Getting this wrong points the
    command at the wrong database (or crashes on the missing parent).
    """

    def _args(project, index):
        import argparse

        return argparse.Namespace(project=str(project), index=index)

    def _resolve(project, index):
        from pytest_leela.daemon import _resolve_index_path

        return _resolve_index_path(_args(project, index))

    def it_joins_a_relative_index_to_the_project(tmp_path):
        from pytest_leela.daemon import _resolve_index_path

        resolved = _resolve_index_path(_args(tmp_path, ".leela/custom.db"))
        assert resolved == tmp_path / ".leela" / "custom.db"

    def it_uses_an_absolute_index_verbatim(tmp_path):
        absolute = tmp_path / "elsewhere" / "abs.db"
        assert _resolve(tmp_path, str(absolute)) == absolute

    def it_defaults_to_the_project_index(tmp_path):
        assert _resolve(tmp_path, None) == tmp_path / ".leela" / "index.db"

    def it_rejects_a_project_that_is_not_a_directory(tmp_path):
        import pytest

        from pytest_leela.daemon import _resolve_index_path

        missing = tmp_path / "nope"
        with pytest.raises(SystemExit) as exc:
            _resolve_index_path(_args(missing, None))
        assert "not a directory" in str(exc.value)


def describe_print_mutant_status_dispatch():
    """``_print_mutant`` prints a line per known mutant status only.

    A ``killed`` / ``survived`` / ``cache-hit`` / ``error`` event gets its
    specific label; any other status produces no output at all, because the
    engine has not taught the daemon a new vocabulary. A dispatcher that
    falls through to ``cache-hit`` (or ``error``) for unknown statuses
    would print misleading lines for them.
    """

    def _daemon(tmp_path):
        _make_project(tmp_path)
        return _make_daemon(tmp_path, out=io.StringIO())

    def it_prints_the_specific_label_for_each_known_status(tmp_path: Path):
        from pytest_leela.models import EngineProgress

        daemon = _daemon(tmp_path)
        cases = {
            "killed": "killed",
            "survived": "SURVIVED",
            "cache-hit": "cache-hit",
            "error": "ERROR",
        }
        for status, expected in cases.items():
            out = io.StringIO()
            daemon.out = out
            daemon._print_mutant(
                EngineProgress(
                    kind="mutant",
                    file_path="/f/a.py",
                    lineno=5,
                    op="Sub",
                    status=status,
                    symbol_id="m",
                    killing_test="t.py::test_x" if status == "killed" else None,
                )
            )
            assert expected in out.getvalue()

    def it_prints_nothing_for_an_unknown_status(tmp_path: Path):
        from pytest_leela.models import EngineProgress

        daemon = _daemon(tmp_path)
        out = io.StringIO()
        daemon.out = out
        daemon._print_mutant(
            EngineProgress(
                kind="mutant",
                file_path="/f/a.py",
                lineno=5,
                op="Sub",
                status="brand-new-status",
                symbol_id="m",
            )
        )
        assert out.getvalue() == ""


def _seed_symbol_test_history(tmp_path):
    """Index one symbol covered by a test file that will be deleted.

    Returns ``(daemon, db, symbol, test_id)``. The relationship is stored
    both in the mutant's ``details.test_ids_run`` and in the
    ``symbol_tests`` table, which is the mapping the daemon reads back.
    """
    from pytest_leela.models import Mutant, MutantResult, MutationPoint

    _make_project(tmp_path)
    daemon = _make_daemon(tmp_path)
    db = IndexDB(daemon.index_path)
    source = tmp_path / "src" / "calc.py"
    source.write_text("def add(x, y):\n    return x + y\n")
    db.reconcile_file(str(source), source.read_text())
    symbol = db.all_symbols()[0]
    point = MutationPoint(str(source), "add", 2, 11, "BinOp", "Add", None)
    mutant = Mutant(point, "Sub", 0)
    test_id = str(tmp_path / "tests" / "test_calc.py") + "::test_add"
    # ``test_ids_run`` (the 6th field) is what ``symbols_for_tests``
    # reads back out of the mutant's stored details.
    db.write_mutant_result(
        symbol.id,
        mutant,
        MutantResult(mutant, False, 1, None, 0.01, [test_id]),
        symbol.source_hash,
        "tests",
    )
    db._execute(
        "INSERT INTO symbol_tests (symbol_id, test_id) VALUES (?, ?)",
        (symbol.id, test_id),
    )
    return daemon, db, symbol, test_id


def describe_dirty_symbols_from_deleted_test_file_with_history():
    """A deleted test file with recorded coverage maps back to its symbols.

    ``symbol_tests`` records which tests exercised which symbols. When a
    test file is deleted, the daemon looks up the test ids recorded for
    that path prefix and asks the index which source symbols those tests
    had been covering, so they can be re-analyzed. With real history the
    helper must return those symbol ids in a set; with no history at all
    it must return an empty set rather than performing the lookup.

    Both branches matter: a guard that returned early when the lookup
    *did* have matches would silently stop invalidating genuinely affected
    symbols, and a return that produced ``None`` instead of a set would
    break the caller's ``|=`` union.
    """

    def it_returns_the_covered_symbols_for_a_deleted_test_file(tmp_path: Path):
        daemon, db, symbol, test_id = _seed_symbol_test_history(tmp_path)
        try:
            deleted = str(tmp_path / "tests" / "test_calc.py")
            result = daemon._dirty_symbols_from_deleted_test_file(db, deleted)
            assert isinstance(result, set)
            assert symbol.id in result
        finally:
            db.close()

    def it_returns_an_empty_set_when_nothing_was_covered(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        with IndexDB(daemon.index_path) as db:
            never_seen = str(tmp_path / "tests" / "test_never.py")
            result = daemon._dirty_symbols_from_deleted_test_file(db, never_seen)
            assert isinstance(result, set)
            assert result == set()


def describe_dirty_symbols_from_scoped_tests_empty_branches():
    """``_dirty_symbols_from_scoped_tests`` always returns a set.

    The caller does ``dirty_ids |= self._dirty_symbols_from_scoped_tests(...)``,
    so each early return has to produce a set. It returns an empty set when
    the project has no source files to track, and again when the changed
    tests covered no tracked lines (the test-first case). Returning ``None``
    from either branch would raise ``TypeError`` at the union.
    """

    def it_returns_an_empty_set_when_there_are_no_source_files(tmp_path: Path):
        # No ``src/`` at all, so ``_iter_source_files`` yields nothing.
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n")
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        with IndexDB(daemon.index_path) as db:
            result = daemon._dirty_symbols_from_scoped_tests(
                db, [str(tmp_path / "tests" / "test_x.py")]
            )
        assert isinstance(result, set)
        assert result == set()

    def it_returns_an_empty_set_when_tests_cover_no_source_lines(
        tmp_path: Path, monkeypatch
    ):
        from pytest_leela.coverage_tracker import CoverageMap

        src, _tests = _make_project(tmp_path)
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            # Tests ran but touched none of the tracked source lines.
            monkeypatch.setattr(
                "pytest_leela.coverage_tracker.collect_coverage_subprocess",
                lambda **kw: CoverageMap(),
            )
            result = daemon._dirty_symbols_from_scoped_tests(
                db, [str(_tests / "test_calc.py")]
            )
        assert isinstance(result, set)
        assert result == set()
        assert "covered 0 source lines" in _read(daemon.out)


def describe_reconcile_deletion_returns_a_result():
    """``_reconcile_deletion`` hands back the reconcile outcome.

    ``_scan_for_changes`` wraps each deletion in a ``ChangeEvent`` carrying
    that result, and ``_loop`` reads ``reconcile.added/removed/changed`` off
    it. Returning ``None`` would surface as an ``AttributeError`` there, so
    the deletion path must always yield a real ``ReconcileResult``.
    """

    def it_returns_a_reconcile_result_and_drops_the_symbols(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            source = tmp_path / "src" / "calc.py"
            result = daemon._reconcile_deletion(db, str(source))
            assert result is not None
            assert isinstance(result, ReconcileResult)
            # The now-empty symbol set is gone from the index.
            assert db.find_symbol_for(str(source), 1) is None


def _run_polls(daemon: LeelaDaemon, db: IndexDB, polls: int) -> int:
    """Drive ``_loop`` for exactly *polls* iterations.

    The loop's only clock is the daemon's own stop event: each pass ends
    with ``self._stop.wait(poll_interval)``. Replacing that single wait
    with a counter makes the number of passes exact, so the periodic
    status line and the per-pass change handling can be observed without
    racing a real timer.
    """
    waits: list[float | None] = []
    original_wait = daemon._stop.wait

    def counting_wait(timeout=None):
        waits.append(timeout)
        if len(waits) >= polls:
            daemon._stop.set()
        return daemon._stop.is_set()

    daemon._stop.clear()
    daemon._stop.wait = counting_wait
    try:
        daemon._loop(db)
    finally:
        daemon._stop.wait = original_wait
        daemon._stop.set()
        if daemon._reanalyzer_thread is not None:
            daemon._reanalyzer_thread.join(timeout=10.0)
    return len(waits)


def _poll_once(daemon: LeelaDaemon, db: IndexDB) -> int:
    """Run exactly one loop pass with no re-analysis scheduled.

    The re-analysis decision is a separate guard with its own tests; this
    helper clears the change stamp first so a pass here only exercises
    the scan/stamp side of the loop.
    """
    daemon._last_change_at = None
    return _run_polls(daemon, db, 1)


def describe_unresolvable_paths_are_not_test_files():
    """``_is_test_file`` answers "no" for a path it cannot resolve.

    The classification resolves the candidate path before comparing it
    with the test directory, because one side may be a symlinked or
    otherwise indirect path. A path the OS refuses to resolve must still
    come back as "not a test file": answering "yes" would route a file
    event that cannot exist into the test-file handling path.
    """

    def it_returns_false_for_a_path_the_os_cannot_resolve(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        # An embedded NUL byte is a real input (no filesystem entry can
        # contain one) and makes ``Path.resolve()`` raise.
        unresolvable = f"{tmp_path / 'tests'}/broken\x00name.py"
        assert daemon._is_test_file(unresolvable) is False

    def it_still_classifies_a_resolvable_test_path(tmp_path: Path):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path)
        assert daemon._is_test_file(str(tmp_path / "tests" / "test_x.py")) is True


def describe_test_file_change_count_reporting():
    """The ``[coverage]`` line reports symbols that actually changed state.

    ``_handle_test_file_changes`` marks the symbols the changed tests
    touch and reports how many the index actually flipped. Both halves of
    the boundary matter: a symbol that is already dirty transitions zero
    times and is not a transition worth reporting. Both still schedule
    re-analysis: a corrected test can retry already-pending work.
    """

    def it_reports_the_symbols_it_flipped_to_dirty(tmp_path: Path):
        daemon, db, symbol, _test_id = _seed_symbol_test_history(tmp_path)
        try:
            db.mark_symbol_clean(symbol.id)
            assert daemon._last_change_at is None
            deleted = str(tmp_path / "tests" / "test_calc.py")
            daemon._handle_test_file_changes(db, [], [], [deleted])
            output = _read(daemon.out)
            assert "dirtied 1 source symbol(s)" in output
            assert daemon._last_change_at is not None
        finally:
            db.close()

    def it_reports_nothing_when_the_symbols_are_already_dirty(tmp_path: Path):
        daemon, db, symbol, _test_id = _seed_symbol_test_history(tmp_path)
        try:
            assert symbol.state == STATE_DIRTY
            deleted = str(tmp_path / "tests" / "test_calc.py")
            daemon._handle_test_file_changes(db, [], [], [deleted])
            output = _read(daemon.out)
            # The index flipped nothing: no count to report. The real test
            # edit still schedules a retry of pending work.
            assert "dirtied" not in output
            assert daemon._last_change_at is not None
        finally:
            db.close()


def describe_scoped_coverage_path_resolution():
    """Coverage paths are matched after ``realpath``, with a raw fallback.

    A test can be traced through an indirect path (a symlinked checkout,
    a bind-mounted workspace) that the index never stored verbatim. The
    resolved path is the one that has to match; only when it does not is
    the raw path worth a second try, so a covered symbol is never lost.
    """

    def it_dirties_the_symbol_behind_an_indirect_coverage_path(
        tmp_path: Path, monkeypatch
    ):
        from pytest_leela.coverage_tracker import CoverageMap

        src, tests = _make_project(tmp_path)
        daemon = _make_daemon(tmp_path, out=io.StringIO())
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            symbol = next(s for s in db.all_symbols() if s.id.endswith(":add"))
            # Coverage saw the file through a second name for it.
            alias_root = tmp_path / "workspace"
            alias_root.symlink_to(tmp_path, target_is_directory=True)
            coverage = CoverageMap()
            coverage.add(str(alias_root / "src" / "calc.py"), 2, "tests::test_add")
            monkeypatch.setattr(
                "pytest_leela.coverage_tracker.collect_coverage_subprocess",
                lambda **kw: coverage,
            )
            dirty = daemon._dirty_symbols_from_scoped_tests(
                db, [str(tests / "test_calc.py")]
            )
        assert symbol.id in dirty
        assert src.exists()


class _FixedClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self, now: float) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def describe_reanalysis_debounce_boundaries():
    """The debounce window is half-open: elapsed == window still runs.

    ``_maybe_spawn_reanalysis`` skips a pass while the newest change is
    younger than the debounce window, and skips it again when the newest
    change is not newer than the last pass. Both comparisons sit exactly
    on a boundary in normal operation — a change landing precisely one
    debounce window ago is old enough, and a change timestamped exactly
    with the last pass is not new enough.
    """

    def it_spawns_when_exactly_one_debounce_window_has_elapsed(
        tmp_path: Path, monkeypatch
    ):
        from types import SimpleNamespace

        _make_project(tmp_path)
        engine_factory = MagicMock()
        daemon = _make_daemon(
            tmp_path,
            reanalyze_debounce=5.0,
            engine_factory=engine_factory,
            out=io.StringIO(),
        )
        clock = _FixedClock(1000.0)
        monkeypatch.setattr(
            "pytest_leela.daemon.time", SimpleNamespace(monotonic=clock.monotonic)
        )
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            daemon._last_change_at = clock.now - daemon.reanalyze_debounce
            daemon._maybe_spawn_reanalysis(db)
            if daemon._reanalyzer_thread is not None:
                daemon._reanalyzer_thread.join(timeout=10.0)
            assert daemon._reanalyzer_thread is not None
            assert not daemon._reanalyzer_thread.is_alive()
            assert engine_factory.call_count == 1

    def it_skips_when_the_newest_change_matches_the_last_pass(
        tmp_path: Path, monkeypatch
    ):
        from types import SimpleNamespace

        _make_project(tmp_path)
        engine_factory = MagicMock()
        daemon = _make_daemon(
            tmp_path,
            reanalyze_debounce=0.0,
            engine_factory=engine_factory,
            out=io.StringIO(),
        )
        clock = _FixedClock(2000.0)
        monkeypatch.setattr(
            "pytest_leela.daemon.time", SimpleNamespace(monotonic=clock.monotonic)
        )
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            daemon._last_change_at = clock.now
            daemon._last_reanalysis_at = clock.now
            daemon._maybe_spawn_reanalysis(db)
            if daemon._reanalyzer_thread is not None:
                daemon._reanalyzer_thread.join(timeout=10.0)
        # Nothing changed since the pass that already ran.
        assert daemon._reanalyzer_thread is None
        assert engine_factory.call_count == 0

    def it_spawns_when_a_change_is_newer_than_the_last_pass(
        tmp_path: Path, monkeypatch
    ):
        from types import SimpleNamespace

        _make_project(tmp_path)
        engine_factory = MagicMock()
        daemon = _make_daemon(
            tmp_path,
            reanalyze_debounce=0.0,
            engine_factory=engine_factory,
            out=io.StringIO(),
        )
        clock = _FixedClock(3000.0)
        monkeypatch.setattr(
            "pytest_leela.daemon.time", SimpleNamespace(monotonic=clock.monotonic)
        )
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            daemon._last_reanalysis_at = clock.now
            clock.advance(0.001)
            daemon._last_change_at = clock.now
            daemon._maybe_spawn_reanalysis(db)
            if daemon._reanalyzer_thread is not None:
                daemon._reanalyzer_thread.join(timeout=10.0)
            assert engine_factory.call_count == 1


def describe_baseline_without_a_test_directory():
    """A project with no test directory has no baseline to run.

    ``_check_baseline_tests`` answers the observer with a list of failing
    node ids. When the project has no tests directory at all there is
    nothing to run, so it must hand back an empty list rather than
    tripping over the missing directory.
    """

    def it_returns_an_empty_list_when_there_is_no_test_directory(
        tmp_path: Path
    ):
        src = tmp_path / "src"
        src.mkdir()
        (src / "calc.py").write_text("def add(x, y):\n    return x + y\n")
        daemon = LeelaDaemon(
            project_root=tmp_path,
            index_path=tmp_path / ".leela" / "index.db",
            out=io.StringIO(),
        )
        assert daemon._resolve_test_dir() is None
        assert daemon._check_baseline_tests() == []


def describe_run_drains_the_reanalyzer_thread():
    """``run()`` returns only after its worker finished.

    The re-analysis pass runs on its own thread and holds the shared
    index, so the engine is drained before ``run()`` returns. A worker
    that is still busy when ``run()`` returns would keep reading the
    index after the daemon has torn it down.
    """

    def it_waits_for_a_busy_worker_before_returning(
        tmp_path: Path, monkeypatch
    ):
        _make_project(tmp_path)
        started = threading.Event()
        release = threading.Event()

        class BlockingEngine:
            def __init__(self, db, on_progress=None):
                self._on_progress = on_progress

            def run(self, target_files, test_dir=None, test_node_ids=None):
                started.set()
                assert release.wait(30.0)

        engine_factory = lambda db, on_progress=None: BlockingEngine(  # noqa: E731
            db, on_progress
        )
        daemon = _make_daemon(
            tmp_path,
            poll_interval=0.01,
            reanalyze_debounce=0.0,
            engine_factory=engine_factory,
        )

        def one_pass(db):
            daemon._last_change_at = time.monotonic()
            daemon._maybe_spawn_reanalysis(db)
            daemon._stop.wait(5.0)

        monkeypatch.setattr(daemon, "_loop", one_pass)
        runner = threading.Thread(target=daemon.run)
        runner.start()
        try:
            assert started.wait(30.0), "re-analysis worker never started"
            # The worker is released shortly after the loop stops, so a
            # run that drains it comes back only once it is done.
            threading.Timer(1.5, release.set).start()
            daemon.stop()
            runner.join(30.0)
            assert not runner.is_alive()
            assert daemon._reanalyzer_thread is not None
            assert not daemon._reanalyzer_thread.is_alive()
        finally:
            release.set()
            daemon.stop()
            runner.join(30.0)
            if daemon._reanalyzer_thread is not None:
                daemon._reanalyzer_thread.join(30.0)


def describe_loop_poll_accounting():
    """Each poll scans once; the status line lands every tenth poll.

    ``_loop`` is the daemon's heartbeat: it must keep scanning while the
    daemon is running, and it prints the operator status line only on
    every tenth pass. Ten passes therefore produce exactly one status
    line — not one per pass, and none at all if the loop never runs.
    """

    def it_prints_one_status_line_after_ten_polls(
        tmp_path: Path, monkeypatch
    ):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path, poll_interval=0.001, out=io.StringIO())
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            # Keep the loop's own work out of the output: a re-analysis
            # pass would print a status line of its own.
            monkeypatch.setattr(daemon, "_last_change_at", None)
            expected_status = DaemonStatus.from_db(db).render()
            polls = _run_polls(daemon, db, 10)
        assert polls == 10
        assert _read(daemon.out).count(expected_status) == 1

    def it_prints_no_status_line_before_the_tenth_poll(
        tmp_path: Path, monkeypatch
    ):
        _make_project(tmp_path)
        daemon = _make_daemon(tmp_path, poll_interval=0.001, out=io.StringIO())
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            monkeypatch.setattr(daemon, "_last_change_at", None)
            expected_status = DaemonStatus.from_db(db).render()
            polls = _run_polls(daemon, db, 9)
        assert polls == 9
        assert expected_status not in _read(daemon.out)


def describe_loop_stamps_only_real_diffs():
    """Only a reconcile that changed something schedules work.

    The loop stamps ``_last_change_at`` when a change event's reconcile
    actually added, removed or changed a symbol. A file that was touched
    without changing (a formatter rewriting identical content, an mtime
    bump) reconciles to nothing and is not work; a new file that adds a
    symbol is.
    """

    def _src_only_project(root: Path) -> Path:
        src = root / "src"
        src.mkdir()
        source = src / "calc.py"
        source.write_text("def add(x, y):\n    return x + y\n")
        return source

    def it_does_not_stamp_a_change_that_reconciles_to_nothing(
        tmp_path: Path
    ):
        source = _src_only_project(tmp_path)
        daemon = _make_daemon(
            tmp_path, poll_interval=0.001, engine_factory=MagicMock(), out=io.StringIO()
        )
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            _poll_once(daemon, db)
            # Touch the file without changing a single byte of it.
            _bump_mtime(source)
            assert _poll_once(daemon, db) == 1
            assert daemon._last_change_at is None

    def it_stamps_a_change_that_adds_a_symbol(tmp_path: Path):
        _src_only_project(tmp_path)
        daemon = _make_daemon(
            tmp_path, poll_interval=0.001, engine_factory=MagicMock(), out=io.StringIO()
        )
        with IndexDB(daemon.index_path) as db:
            daemon._initial_build(db)
            _poll_once(daemon, db)
            (tmp_path / "src" / "extra.py").write_text(
                "def mul(x, y):\n    return x * y\n"
            )
            assert _poll_once(daemon, db) == 1
            assert daemon._last_change_at is not None
            symbols = [s.id for s in db.all_symbols()]
            assert any(sym.endswith(":mul") for sym in symbols)


@pytest.mark.parametrize("change", ["deleted", "modified"])
def test_poll_invalidates_retained_prior_test_consumers(tmp_path, monkeypatch, change):
    """Real Engine history, not symbol_tests, supplies old file consumers.

    Observed mutant test IDs are conservative consumers, not proof of line
    coverage. Only the watcher's fresh coverage run supplies new coverage.
    """
    import json
    from pytest_leela.engine import Engine

    sources = tmp_path / "src"
    tests = tmp_path / "tests"
    sources.mkdir()
    tests.mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(sources))
    monkeypatch.syspath_prepend(str(tmp_path))
    changed = tests / "test_change.py"
    changed.write_text(
        "import pytest\nfrom alpha import alpha\n"
        "@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_old(n):\n    assert alpha(n, 3) == n + 3\n"
    )
    for name in ("alpha", "beta", "gamma"):
        source = sources / (name + ".py")
        source.write_text(f"def {name}(a, b):\n    return a + b\n")
        if name != "alpha":
            (tests / f"test_{name}.py").write_text(
                f"from {name} import {name}\n"
                f"def test_{name}():\n    assert {name}(2, 3) == 5\n"
            )

    daemon = _make_daemon(tmp_path)
    with IndexDB(daemon.index_path) as db:
        for name in ("alpha", "beta", "gamma"):
            test = changed if name == "alpha" else tests / f"test_{name}.py"
            run = Engine(index=db, use_coverage=False).run(
                [str(sources / f"{name}.py")], test_dir=str(tests),
                test_node_ids=[str(test)],
            )
            assert run.results and not run.errors
        symbols = {s.id.rsplit(":", 1)[1]: s.id for s in db.all_symbols()}
        assert set(symbols) == {"alpha", "beta", "gamma"}
        assert all(db.get_state(sid) == STATE_CLEAN for sid in symbols.values())
        assert db._execute("SELECT count(*) FROM symbol_tests").fetchone()[0] == 0

        # A later actual attempt observes a different file. Recovery must use
        # C14's retained union, not only latest_test_ids_run.
        replacement = tests / "test_replacement.py"
        replacement.write_text(
            "from alpha import alpha\n"
            "def test_replacement():\n    assert alpha(2, 3) == 5\n"
        )
        Engine(index=db, use_coverage=False).run(
            [str(sources / "alpha.py")], test_dir=str(tests),
            test_node_ids=[str(replacement)],
        )
        history = [json.loads(row[0]) for row in db._execute(
            "SELECT details FROM mutants WHERE symbol_id = ?", (symbols["alpha"],),
        ).fetchall()]
        assert history and all(any("test_old[" in t for t in d["test_ids_run"]) for d in history)
        assert all(not any("test_old[" in t for t in d["latest_test_ids_run"]) for d in history)
        assert all(any("test_replacement" in t for t in d["latest_test_ids_run"]) for d in history)
        # Partial legacy rows must not prevent recovering valid consumers or
        # be rewritten during invalidation. These are actual Engine-created
        # rows with only their diagnostic metadata altered, not seeded edges.
        gamma_rows = db._execute(
            "SELECT id, details FROM mutants WHERE symbol_id = ? ORDER BY id",
            (symbols["gamma"],),
        ).fetchall()
        assert len(gamma_rows) >= 3
        db._execute("UPDATE mutants SET details = NULL WHERE id = ?", (gamma_rows[0][0],))
        db._execute(
            "UPDATE mutants SET details = ? WHERE id = ?",
            ('{"test_ids_run": legacy_unreadable}', gamma_rows[1][0]),
        )
        custom_details = json.loads(gamma_rows[2][1])
        custom_details["custom_metadata"] = {"owner": "retained"}
        db._execute(
            "UPDATE mutants SET details = ? WHERE id = ?",
            (json.dumps(custom_details), gamma_rows[2][0]),
        )
        daemon._initial_build(db)
        mutant_rows = [tuple(row) for row in db._execute("SELECT * FROM mutants ORDER BY id").fetchall()]

        if change == "deleted":
            changed.unlink()
        else:
            changed.write_text(
                "from beta import beta\n"
                "def test_now_beta():\n    assert beta(2, 3) == 5\n"
            )
            _bump_mtime(changed)
        daemon._scan_for_changes(db)
        states = {name: db.get_state(sid) for name, sid in symbols.items()}
        assert states == {
            "alpha": STATE_DIRTY,
            "beta": STATE_DIRTY if change == "modified" else STATE_CLEAN,
            "gamma": STATE_CLEAN,
        }
        assert daemon._last_change_at is not None
        assert daemon._dirty_symbols_from_deleted_test_file(
            db, str(tests / ".." / "tests" / "test_change.py"),
        ) == {symbols["alpha"]}
        assert db._execute("SELECT count(*) FROM symbol_tests").fetchone()[0] == 0
        assert [tuple(row) for row in db._execute("SELECT * FROM mutants ORDER BY id").fetchall()] == mutant_rows


# Batch B lifecycle regressions use real Engine runs and disposable databases.
def _lifecycle_daemon(root, monkeypatch):
    from pytest_leela.engine import Engine

    monkeypatch.chdir(root)
    (root / "src").mkdir()
    tests = root / "tests"
    tests.mkdir()
    monkeypatch.syspath_prepend(str(root / "src"))
    monkeypatch.syspath_prepend(str(tests))
    (tests / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "root = Path(__file__).resolve().parents[1]\n"
        "sys.path[:0] = [str(root / 'src'), str(root / 'tests')]\n"
    )
    results = []
    progress = []

    def factory(db, callback):
        def observe(event):
            progress.append(event)
            callback(event)
        engine = Engine(index=db, on_progress=observe)
        original = engine.run

        def run(*args, **kwargs):
            result = original(*args, **kwargs)
            results.append((kwargs.get("test_node_ids"), result))
            return result
        engine.run = run
        return engine

    daemon = LeelaDaemon(root, test_dir=tests, reanalyze_debounce=0,
                         engine_factory=factory, out=io.StringIO())
    return daemon, results, progress


def _analyze_lifecycle(daemon, db, results, progress):
    before = len(results)
    progress.clear()
    daemon._maybe_spawn_reanalysis(db)
    assert daemon._reanalyzer_thread is not None, daemon.out.getvalue()
    daemon._reanalyzer_thread.join(timeout=30)
    assert not daemon._reanalyzer_thread.is_alive()
    assert len(results) == before + 1, daemon.out.getvalue()
    result = results[-1][1]
    assert result.results
    assert all(r.status != "error" for r in result.results)
    # Not just scheduling a no-op: actual mutant executions, not cached verdicts.
    assert any(e.kind == "mutant" and e.status != "cache-hit" for e in progress)
    assert all(s.state == STATE_CLEAN for s in db.all_symbols())
    return result


def _sub_status(result):
    return next(r.status for r in result.results
                if r.mutant.replacement_op == "Sub")


@pytest.mark.parametrize("dependency", ["constant", "helper", "source-helper", "conftest", "root-conftest", "config"])
@pytest.mark.parametrize("change", ["created", "modified", "deleted"])
def test_contract_input_lifecycle_runs_real_analysis(tmp_path, monkeypatch, dependency, change):
    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source = tmp_path / "src" / "calc.py"
    test = tmp_path / "tests" / "test_calc.py"
    source.write_text("def add(a, b):\n    return a + b\n")
    low = "0"
    high = "2"
    if dependency == "constant":
        changed = source
        low = "def add(a, b):\n    return a + b + globals().get('OFFSET', 0)\n"
        high = "OFFSET = 2\n" + low
        # The function body/hash stays identical across all transitions.
        test.write_text("from calc import add\nimport calc\ndef test_add():\n"
                        "    value = getattr(calc, 'OFFSET', 0)\n"
                        "    assert add(3, 0) == 3 + value\n"
                        "    assert add(0, value) == 2 * value\n")
    elif dependency in {"helper", "source-helper"}:
        changed = tmp_path / ("tests" if dependency == "helper" else "src") / "qa_helper.py"
        low, high = "VALUE = 0\n", "VALUE = 2\n"
        test.write_text("from calc import add\ntry:\n    from qa_helper import VALUE\n"
                        "except ImportError:\n    VALUE = 0\n"
                        "def test_add():\n    assert add(3, 0) == 3\n"
                        "    assert add(0, VALUE) == VALUE\n")
    elif dependency in {"conftest", "root-conftest"}:
        changed = (tmp_path if dependency == "root-conftest" else tmp_path / "tests") / "conftest.py"
        prefix = changed.read_text() if changed.exists() else ""
        low = prefix + "import pytest\n@pytest.fixture\ndef operand():\n    return 0\n"
        high = prefix + "import pytest\n@pytest.fixture\ndef operand():\n    return 2\n"
        # Keep src-layout setup when deleting the test-directory conftest.
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\npythonpath = ["src", "tests"]\n')
        test.write_text("import pytest\nfrom calc import add\n"
                        "def test_add(request):\n"
                        "    try:\n        value = request.getfixturevalue('operand')\n"
                        "    except pytest.FixtureLookupError:\n        value = 0\n"
                        "    assert add(3, 0) == 3\n    assert add(0, value) == value\n")
    else:
        changed = tmp_path / "pytest.ini"
        low = "[pytest]\npython_files = test_*.py\n"
        high = "[pytest]\npython_files = test_*.py check_*.py\n"
        test.write_text("from calc import add\ndef test_add():\n    assert add(3, 0) == 3\n")
        (test.parent / "check_calc.py").write_text(
            "from calc import add\ndef test_extra():\n    assert add(0, 2) == 2\n")
    if dependency == "constant" and change == "modified":
        low = "OFFSET = 0\n" + low
    if change == "created":
        if dependency == "constant":
            changed.write_text(low)
        elif changed.exists():
            changed.unlink()
    else:
        changed.write_text(high if change == "deleted" else low)
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        initial = _analyze_lifecycle(daemon, db, results, progress)
        hashes = {s.id: s.source_hash for s in db.all_symbols()}
        daemon._last_change_at = None
        if change == "deleted":
            if dependency == "constant":
                changed.write_text(low)
            else:
                changed.unlink()
        else:
            changed.write_text(high)
        daemon._scan_for_changes(db)
        assert db.get_dirty_symbols(), daemon.out.getvalue()
        assert daemon._last_change_at is not None
        after = _analyze_lifecycle(daemon, db, results, progress)
        assert {s.id: s.source_hash for s in db.all_symbols()} == hashes
        if dependency != "constant":
            assert _sub_status(initial) == ("killed" if change == "deleted" else "survived")
            assert _sub_status(after) == ("survived" if change == "deleted" else "killed")
        # Helpers and fixtures are dependencies, never standalone test selections.
        assert all(selection is None or str(changed) not in selection
                   for selection, _ in results)
        daemon._last_change_at = None
        progress.clear()
        assert daemon._scan_for_changes(db) == []
        assert daemon._last_change_at is None
        assert not db.get_dirty_symbols()


@pytest.mark.parametrize("file_kind", ["source", "test"])
@pytest.mark.parametrize("clock", ["same", "backward"])
def test_content_changes_ignore_timestamp_direction(tmp_path, monkeypatch, file_kind, clock):
    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source = tmp_path / "src" / "calc.py"
    test = tmp_path / "tests" / "test_calc.py"
    factor = 0 if file_kind == "source" else 1
    value = 2 if file_kind == "source" else 0
    source.write_text(f"FACTOR = {factor}\ndef add(a, b):\n    return a + b * FACTOR\n")
    test.write_text(f"from calc import add, FACTOR\nVALUE = {value}\ndef test_add():\n"
                    "    assert add(3, 0) == 3\n"
                    "    assert add(0, VALUE) == VALUE * FACTOR\n")
    changed = source if file_kind == "source" else test
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        initial = _analyze_lifecycle(daemon, db, results, progress)
        assert _sub_status(initial) == "survived"
        before = changed.stat()
        content = changed.read_bytes()
        replacement = content.replace(b"FACTOR = 0", b"FACTOR = 2") if file_kind == "source" else content.replace(b"VALUE = 0", b"VALUE = 2")
        assert replacement != content and len(replacement) == len(content)
        changed.write_bytes(replacement)
        stamp = before.st_mtime_ns if clock == "same" else before.st_mtime_ns - 100_000_000_000
        os.utime(changed, ns=(before.st_atime_ns, stamp))
        assert changed.stat().st_mtime_ns == stamp
        assert changed.stat().st_size == before.st_size
        daemon._last_change_at = None
        daemon._scan_for_changes(db)
        assert db.get_dirty_symbols(), (file_kind, clock)
        assert daemon._last_change_at is not None
        after = _analyze_lifecycle(daemon, db, results, progress)
        assert _sub_status(after) == "killed"
        # A rewrite/timestamp-only change must neither collect nor analyze again.
        daemon._last_change_at = None
        changed.write_bytes(replacement)
        os.utime(changed, ns=(before.st_atime_ns, stamp - 100_000_000_000))
        calls = []
        original = subprocess.run
        def record(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)
        monkeypatch.setattr(subprocess, "run", record)
        assert daemon._scan_for_changes(db) == []
        assert daemon._scan_for_changes(db) == []
        assert calls == []
        assert daemon._last_change_at is None
        assert not db.get_dirty_symbols()


def test_daemon_uses_normal_custom_discovery_and_real_coverage_selection(tmp_path, monkeypatch):
    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source = tmp_path / "src" / "calc.py"
    source.write_text("def add(a: int | None, b: int) -> int:\n    return (a or 0) + b\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = test_*.py check_*.py\n")
    (tmp_path / "tests" / "test_stdlib.py").write_text(
        "def test_stdlib():\n    assert len([1]) == 1\n")
    custom = tmp_path / "tests" / "check_calc.py"
    # The test must actually read the PEP 649 lazy annotations (3.14+),
    # matching describe_annotation_policy: only then do ``int | None``
    # annotation mutants (``&``/``^``) raise and get killed instead of
    # surviving unobserved.
    custom.write_text(
        "import typing\n"
        "from calc import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
        "    assert typing.get_type_hints(add) == "
        "{'a': int | None, 'b': int, 'return': int}\n"
    )
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        result = _analyze_lifecycle(daemon, db, results, progress)
        assert all(r.status == "killed" for r in result.results)
        annotation = [r for r in result.results if r.mutant.replacement_op in {"BitAnd", "BitXor"}]
        assert len(annotation) == 2
        assert all(r.status == "killed" for r in annotation)
        # Executable body mutants retain actual coverage-derived custom nodeIDs.
        body = [r for r in result.results if r.mutant.replacement_op == "Sub"]
        assert len(body) == 1
        assert body[0].test_ids_run == ["tests/check_calc.py::test_add"]
        assert results[0][0] is None


def test_restart_reconciles_absent_in_scope_history_only(tmp_path, monkeypatch):
    from pytest_leela.engine import Engine

    monkeypatch.chdir(tmp_path)
    selected = tmp_path / "src" / "selected"
    selected.mkdir(parents=True)
    tests = tmp_path / "tests"
    tests.mkdir()
    monkeypatch.syspath_prepend(str(tmp_path / "src"))
    monkeypatch.syspath_prepend(str(selected))
    gone = selected / "gone.py"
    keep = selected / "keep.py"
    outside = tmp_path / "src" / "outside.py"
    omitted = tmp_path / "src" / "omitted.py"
    for path in [gone, keep, outside, omitted]:
        path.write_text(f"def {path.stem}(a, b):\n    return a + b\n")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["src", "src/selected"]\n')
    test = tests / "test_all.py"
    test.write_text("from gone import gone\nfrom keep import keep\n"
                    "from outside import outside\nfrom omitted import omitted\n"
                    "def test_all():\n    assert gone(2, 3) == 5\n"
                    "    assert keep(2, 3) == 5\n    assert outside(2, 3) == 5\n"
                    "    assert omitted(2, 3) == 5\n")
    database = tmp_path / "owned.db"
    with IndexDB(database) as db:
        result = Engine(index=db, use_coverage=False).run(
            [str(p) for p in [gone, keep, outside, omitted]], test_dir=str(tests))
        assert result.results and all(r.status != "error" for r in result.results)
        symbols = {Path(s.file).stem: s for s in db.all_symbols()}
        assert len(symbols) == 4 and all(s.state == STATE_CLEAN for s in symbols.values())
        # Persisted relationship rows, plus real mutant history, verify both cascades.
        for sym in symbols.values():
            db._execute("INSERT INTO symbol_tests (symbol_id, test_id) VALUES (?, ?)",
                        (sym.id, "tests/test_all.py::test_all"))
        retained = [tuple(r) for r in db._execute(
            "SELECT * FROM mutants WHERE symbol_id != ? ORDER BY id", (symbols["gone"].id,)).fetchall()]
        retained_symbols = [tuple(r) for r in db._execute(
            "SELECT * FROM symbols WHERE id != ? ORDER BY id", (symbols["gone"].id,)).fetchall()]
    gone.unlink()
    omitted.unlink()  # Absent but outside the newly narrowed configured scope.
    test.write_text("from keep import keep\nfrom outside import outside\n"
                    "def test_all():\n    assert keep(2, 3) == 5\n"
                    "    assert outside(2, 3) == 5\n")
    daemon = LeelaDaemon(tmp_path, target_dirs=("src/selected",), test_dir=tests, out=io.StringIO())
    with IndexDB(database) as db:
        daemon._initial_build(db)
        assert db.get_symbol(symbols["gone"].id) is None
        assert {s.id for s in db.all_symbols()} == {symbols[n].id for n in ["keep", "outside", "omitted"]}
        assert all(s.state == STATE_CLEAN for s in db.all_symbols())
        assert [tuple(r) for r in db._execute("SELECT * FROM mutants ORDER BY id").fetchall()] == retained
        assert [tuple(r) for r in db._execute("SELECT * FROM symbols ORDER BY id").fetchall()] == retained_symbols
        assert db._execute("SELECT COUNT(*) FROM symbol_tests WHERE symbol_id = ?", (symbols["gone"].id,)).fetchone()[0] == 0
        assert db._execute("SELECT COUNT(*) FROM symbol_tests").fetchone()[0] == 3
        assert db._execute("PRAGMA foreign_key_check").fetchall() == []
        assert daemon._scan_for_changes(db) == []


@pytest.mark.parametrize("failure", ["import", "early-success-exit"])
def test_unknown_collection_keeps_real_analysis_pending_and_recovers(tmp_path, monkeypatch, failure):
    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source = tmp_path / "src" / "calc.py"
    source.write_text("def add(a, b):\n    return a + b\n")
    test = tmp_path / "tests" / "test_calc.py"
    good = "from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n"
    test.write_text(good)
    # A real indexed out-of-scope source must survive conservative invalidation.
    foreign = tmp_path / "elsewhere" / "foreign.py"
    foreign.parent.mkdir()
    foreign.write_text("def foreign(a, b):\n    return a + b\n")
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        _analyze_lifecycle(daemon, db, results, progress)
        db.reconcile_file(str(foreign), foreign.read_text())
        foreign_id = next(s.id for s in db.all_symbols() if s.file == str(foreign))
        db.mark_state(foreign_id, STATE_CLEAN)
        if failure == "import":
            test.write_text("import missing_real_collection_dependency\n" + good)
        else:
            (tmp_path / "conftest.py").write_text(
                "import pytest\ndef pytest_sessionstart(session):\n"
                "    pytest.exit('configuration stopped collection', returncode=0)\n")
            test.write_text(good + "\n# trigger test-tree collection\n")
        daemon._last_change_at = None
        daemon._scan_for_changes(db)
        assert db.get_dirty_symbols()
        assert db.get_state(foreign_id) == STATE_CLEAN
        assert "scoped collection failed" in daemon.out.getvalue()
        if failure == "import":
            # Do not turn unrelated collection failures into mutant kills.
            count = len(results)
            daemon._maybe_spawn_reanalysis(db)
            daemon._reanalyzer_thread.join(timeout=30)
            assert not daemon._reanalyzer_thread.is_alive()
            assert len(results) == count
            assert "BaselineFailure" in daemon.out.getvalue()
            assert db.get_dirty_symbols()
        else:
            (tmp_path / "conftest.py").unlink()
        test.write_text(good + "    assert add(0, 2) == 2\n")
        daemon._scan_for_changes(db)
        # Full Engine run is legitimate only after the unmutated suite recovers.
        _analyze_lifecycle(daemon, db, results, progress)
        assert db.get_state(foreign_id) == STATE_CLEAN


@pytest.mark.parametrize("changes_cwd", [False, True])
def test_selected_nested_pytest_config_is_polled(tmp_path, monkeypatch, changes_cwd):
    """Nested tests/pytest.ini changes the pytest rootdir.

    Coverage-derived nodeIDs are emitted relative to that nested rootdir
    (``test_calc.py::test_add``). The Engine replays them from the project
    root, so they must be normalized to project-root-relative form
    (``tests/test_calc.py::test_add``). Without normalization the baseline
    run hits ``USAGE_ERROR`` because the project root has no
    ``test_calc.py`` file.
    """
    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source = tmp_path / "src" / "calc.py"
    source.write_text("def add(a, b):\n    return a + b\n")
    tests = tmp_path / "tests"
    config = tests / "pytest.ini"
    arguments = "monkeypatch, tmp_path" if changes_cwd else ""
    chdir = "    monkeypatch.chdir(tmp_path)\n" if changes_cwd else ""
    weak = (
        "from calc import add\n"
        f"def test_add({arguments}):\n"
        f"{chdir}"
        "    assert add(3, 0) == 3\n"
    )
    strong = weak + "    assert add(0, 2) == 2\n"
    test = tests / "test_calc.py"
    test.write_text(weak)
    (tests / "check_calc.py").write_text(
        "from calc import add\ndef test_extra():\n    assert add(0, 2) == 2\n")
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        initial = _analyze_lifecycle(daemon, db, results, progress)
        assert _sub_status(initial) == "survived"
        config.write_text("[pytest]\npython_files = test_*.py\n")
        daemon._initial_build(db)
        assert config in daemon._contract_paths
        assert not db.get_dirty_symbols()
        # Force re-analysis under the nested config. The weak test still
        # survives, but we prove the replay succeeds with normalized
        # project-root-relative nodeIDs.
        symbol_id = next(s.id for s in db.all_symbols())
        db.mark_symbols_dirty({symbol_id})
        daemon._last_change_at = time.monotonic()
        nested = _analyze_lifecycle(daemon, db, results, progress)
        assert _sub_status(nested) == "survived"
        # Coverage map stores project-root-relative nodeIDs so replay from
        # the project root succeeds; without normalization pytest would
        # emit test_calc.py::test_add and the baseline would USAGE_ERROR.
        assert nested.coverage_map is not None
        assert "tests/test_calc.py::test_add" in nested.coverage_map.test_times
        # Strengthen the test so the next run kills the Add -> Sub mutant.
        test.write_text(strong)
        daemon._last_change_at = None
        daemon._scan_for_changes(db)
        assert db.get_dirty_symbols()
        after = _analyze_lifecycle(daemon, db, results, progress)
        assert _sub_status(after) == "killed"
        assert "tests/test_calc.py::test_add" in after.coverage_map.test_times
        config.write_text("[pytest]\npython_files = test_*.py check_*.py\n")
        daemon._last_change_at = None
        events = daemon._scan_for_changes(db)
        assert any(e.file_path == str(config) and e.kind == "modified" for e in events)
        assert db.get_dirty_symbols()
        assert daemon._last_change_at is not None


# F7 uses the lifecycle's real coverage subprocess, Engine and in-process runner.
def _join_daemon_worker(daemon):
    assert daemon._reanalyzer_thread is not None, daemon.out.getvalue()
    daemon._reanalyzer_thread.join(30)
    assert not daemon._reanalyzer_thread.is_alive(), daemon.out.getvalue()


def _gate_inputs(root, producer):
    source = root / "src/calc.py"
    test = root / "tests/test_calc.py"
    source.write_text("def add(a, b):\n    return a + b\n")
    weak = "from calc import add\ndef test_add():\n    assert add(3, 0) == 3\n"
    strong = weak + "    assert add(0, 2) == 2\n"
    test.write_text(weak)
    if producer == "test":
        changed, replacement = test, strong
    elif producer == "helper":
        changed = test.parent / "qa_helper.py"
        changed.write_text("VALUE = 0\n")
        test.write_text("from calc import add\nfrom qa_helper import VALUE\n"
                        "def test_add():\n    assert add(3, 0) == 3\n"
                        "    assert add(0, VALUE) == VALUE\n")
        replacement = "VALUE = 2\n"
    elif producer == "config":
        changed = root / "pytest.ini"
        changed.write_text("[pytest]\npython_files = test_*.py\n")
        (test.parent / "check_calc.py").write_text(
            "from calc import add\ndef test_extra():\n    assert add(0, 2) == 2\n")
        replacement = "[pytest]\npython_files = test_*.py check_*.py\n"
    elif producer == "deleted-test":
        changed = test.parent / "test_extra.py"
        changed.write_text("from calc import add\ndef test_extra():\n    assert add(0, 2) == 2\n")
        replacement = None
    else:
        changed = source
        replacement = "def add(a, b):\n    return a + b + 0\n"
    return source, changed, replacement


@pytest.mark.parametrize("producer", ["source", "test", "helper", "config", "deleted-test"])
def test_analysis_owns_inputs_until_real_runner_publication(tmp_path, monkeypatch, producer):
    import threading
    import pytest_leela.engine as engine_module
    from pytest_leela.daemon import DaemonStatus

    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source, changed, replacement = _gate_inputs(tmp_path, producer)
    foreign = tmp_path / "foreign/untouched.py"
    foreign.parent.mkdir()
    foreign.write_text("def unrelated(a, b):\n    return a + b\n")
    daemon = LeelaDaemon(tmp_path, test_dir=tmp_path / "tests", target_dirs=("src", "foreign"),
                         reanalyze_debounce=0, engine_factory=daemon.engine_factory, out=io.StringIO())
    paused, release, scan_started, scan_done, reads_done = [threading.Event() for _ in range(5)]
    real_runner = engine_module.run_tests_for_mutant
    runner_results, failures, early_reads = [], [], []
    threads = []
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        initial = _analyze_lifecycle(daemon, db, results, progress)
        assert _sub_status(initial) == ("killed" if producer == "deleted-test" else "survived")

        def foreign_history():
            return (db._execute("SELECT * FROM symbols WHERE file = ?", (str(foreign),)).fetchall(),
                    db._execute("SELECT * FROM mutants WHERE symbol_id IN "
                                "(SELECT id FROM symbols WHERE file = ?)", (str(foreign),)).fetchall())

        retained = foreign_history()
        assert retained[0] and retained[1]
        # Restart under a narrower approved watch scope with real prior history.
        daemon = LeelaDaemon(tmp_path, test_dir=tmp_path / "tests", reanalyze_debounce=0,
                             engine_factory=daemon.engine_factory, out=io.StringIO())
        daemon._initial_build(db)
        # A genuine source byte change requests a new pass without changing its
        # function hash. The edit below then arrives while that pass publishes.
        source.write_text(source.read_text() + "# requested pass\n")
        daemon._scan_for_changes(db)
        before_hashes = {s.id: s.source_hash for s in db.all_symbols()}
        signatures = (daemon._source_signatures.copy(), daemon._test_signatures.copy(),
                      daemon._contract_signatures.copy())

        def pause_actual_result(*args, **kwargs):
            result = real_runner(*args, **kwargs)
            runner_results.append(result)
            if not paused.is_set():
                assert result.status != "error"
                paused.set()
                assert release.wait(15), "test did not release actual runner result"
            return result

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", pause_actual_result)
        original_iter = daemon._iter_source_files

        def observe_input_reads():
            if threading.current_thread().name == "real-poll" and not release.is_set():
                early_reads.append(True)
            yield from original_iter()

        monkeypatch.setattr(daemon, "_iter_source_files", observe_input_reads)

        def scan():
            scan_started.set()
            try:
                daemon._scan_for_changes(db)
            except BaseException as exc:
                failures.append(exc)
            finally:
                scan_done.set()

        def read_status():
            try:
                assert DaemonStatus.from_db(db).total_symbols == 2
                assert db.all_symbols()
                assert not db._conn.in_transaction
                with IndexDB(tmp_path / "owned.db") as reader:
                    # This separate connection also stays readable during user code.
                    assert DaemonStatus.from_db(reader).total_symbols == 2
            except BaseException as exc:
                failures.append(exc)
            finally:
                reads_done.set()

        try:
            daemon._maybe_spawn_reanalysis(db)
            assert paused.wait(15), daemon.out.getvalue()
            if replacement is None:
                changed.unlink()
            else:
                changed.write_text(replacement)
            poll = threading.Thread(target=scan, name="real-poll")
            threads.append(poll)
            poll.start()
            assert scan_started.wait(5)
            # Bounded observation only, never a sleep used to release the worker.
            scan_done.wait(.2)
            assert (daemon._source_signatures, daemon._test_signatures,
                    daemon._contract_signatures) == signatures, "busy poll consumed a change"
            reader = threading.Thread(target=read_status)
            threads.append(reader)
            reader.start()
            assert reads_done.wait(5), "analysis held the SQLite lock/transaction"
        finally:
            release.set()
            _join_daemon_worker(daemon)
            for thread in threads:
                thread.join(30)
                assert not thread.is_alive()
        assert not failures, failures
        assert not early_reads, "poll read files before analysis released ownership"
        assert foreign_history() == retained
        assert runner_results
        assert db.get_dirty_symbols(), "old completion erased the newer request"
        assert daemon._last_change_at > daemon._last_reanalysis_at
        assert not daemon._reanalyzing
        if producer != "source":
            assert {s.id: s.source_hash for s in db.all_symbols()} == before_hashes
        # A new relevant edit while ALREADY pending still advances scheduling;
        # reporting's transition count may be zero, but this is not idle input.
        requested_at = daemon._last_change_at
        conftest = tmp_path / "tests/conftest.py"
        conftest.write_text(conftest.read_text() + "# repeated pending request\n")
        daemon._scan_for_changes(db)
        assert daemon._last_change_at > requested_at
        assert db.get_dirty_symbols()
        assert daemon._scan_for_changes(db) == []
        after = _analyze_lifecycle(daemon, db, results, progress)
        assert after.target_sources[str(source)] == source.read_text()
        assert foreign_history() == retained
        if producer != "source":
            assert _sub_status(after) == ("survived" if producer == "deleted-test" else "killed")
        count = len(results)
        assert daemon._scan_for_changes(db) == []
        daemon._maybe_spawn_reanalysis(db)
        assert len(results) == count
        assert not db.get_dirty_symbols()


def test_analysis_defers_whole_same_thread_callback_scan(tmp_path, monkeypatch):
    import pytest_leela.engine as engine_module

    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source, _, replacement = _gate_inputs(tmp_path, "source")
    real_runner = engine_module.run_tests_for_mutant
    callbacks, consumed, reporting_requests = [], [], []
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        signatures = daemon._source_signatures.copy()

        class RequestingOutput(io.StringIO):
            def write(self, message):
                if "modified" in message and str(source) in message and not reporting_requests:
                    reporting_requests.append(message)
                    source.write_text(replacement + "# changed during drain\n")
                    # A supported output callback requests another FULL scan
                    # while the first deferred scan is being reported.
                    assert daemon._scan_for_changes(db) == []
                    assert daemon._scan_for_changes(db) == []
                return super().write(message)

        daemon.out = RequestingOutput()

        def request_from_real_runner(*args, **kwargs):
            result = real_runner(*args, **kwargs)
            if not callbacks:
                callbacks.append(result)
                source.write_text(replacement)
                # Both calls must defer the WHOLE scan, including input reads.
                assert daemon._scan_for_changes(db) == []
                assert daemon._scan_for_changes(db) == []
                consumed.append(daemon._source_signatures != signatures)
            return result

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", request_from_real_runner)
        daemon._maybe_spawn_reanalysis(db)
        _join_daemon_worker(daemon)
        assert callbacks, daemon.out.getvalue()
        assert consumed == [False]
        assert len(reporting_requests) == 1
        assert source.read_text().endswith("# changed during drain\n")
        assert db.get_dirty_symbols(), "callback request was overwritten by old completion"
        assert daemon._source_signatures[str(source)] == daemon._signature(source.read_bytes())
        assert daemon._last_change_at > daemon._last_reanalysis_at
        assert str(source) in daemon.out.getvalue(), "deferred change was not reported"
        _analyze_lifecycle(daemon, db, results, progress)


def test_analysis_cache_hit_completion_keeps_later_source_request(tmp_path, monkeypatch):
    import threading

    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source, _, replacement = _gate_inputs(tmp_path, "source")
    paused, release, started, done = [threading.Event() for _ in range(4)]
    hits, failures = [], []
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        _analyze_lifecycle(daemon, db, results, progress)
        # A restart discovers a NEW symbol but retains the actual old cache.
        # No direct cache verdict or symbol-state fabrication.
        (tmp_path / "src/new.py").write_text("def identity(value):\n    return value\n")
        daemon = LeelaDaemon(tmp_path, test_dir=tmp_path / "tests", reanalyze_debounce=0,
                             engine_factory=daemon.engine_factory, out=io.StringIO())
        daemon._initial_build(db)
        original = db.get_cached_result

        def pause_actual_hit(*args, **kwargs):
            result = original(*args, **kwargs)
            if result is not None and not hits:
                hits.append(result)
                paused.set()
                assert release.wait(15)
            return result

        monkeypatch.setattr(db, "get_cached_result", pause_actual_hit)
        before = daemon._source_signatures.copy()

        def scan():
            started.set()
            try:
                daemon._scan_for_changes(db)
            except BaseException as exc:
                failures.append(exc)
            finally:
                done.set()

        poll = threading.Thread(target=scan)
        try:
            daemon._maybe_spawn_reanalysis(db)
            assert paused.wait(15), daemon.out.getvalue()
            source.write_text(replacement)
            poll.start()
            assert started.wait(5)
            done.wait(.2)
            assert daemon._source_signatures == before, "cache-hit pass did not own its inputs"
        finally:
            release.set()
            _join_daemon_worker(daemon)
            if poll.ident is not None:
                poll.join(30)
                assert not poll.is_alive()
        assert hits and not failures
        assert any(e.status == "cache-hit" for e in progress)
        assert db.get_dirty_symbols()
        result = _analyze_lifecycle(daemon, db, results, progress)
        assert result.target_sources[str(source)] == replacement



def test_analysis_baseline_failure_releases_and_drains_callback_request(tmp_path, monkeypatch):
    from pytest_leela.engine import Engine, BaselineFailure

    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source, test, strong = _gate_inputs(tmp_path, "test")
    test.write_text("from calc import add\ndef test_add():\n    assert add(2, 3) == 999\n")
    baseline = Engine._check_baseline
    failures, consumed = [], []
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        before = daemon._test_signatures.copy()

        def actual_failed_baseline(*args, **kwargs):
            try:
                return baseline(*args, **kwargs)
            except BaselineFailure as exc:
                failures.append(exc)
                test.write_text(strong)
                daemon._scan_for_changes(db)
                daemon._scan_for_changes(db)
                consumed.append(daemon._test_signatures != before)
                raise

        with monkeypatch.context() as m:
            m.setattr(Engine, "_check_baseline", staticmethod(actual_failed_baseline))
            daemon._maybe_spawn_reanalysis(db)
            _join_daemon_worker(daemon)
        assert failures and consumed == [False], daemon.out.getvalue()
        assert "no mutants were run" in daemon.out.getvalue()
        assert not results and not progress
        assert not db._execute("SELECT * FROM mutants").fetchall()
        assert db.get_dirty_symbols()
        assert not daemon._reanalyzing
        assert daemon._scan_for_changes(db) == []
        result = _analyze_lifecycle(daemon, db, results, progress)
        assert result.target_sources[str(source)] == source.read_text()


def test_analysis_real_runner_error_retains_null_verdict_and_later_poll(tmp_path, monkeypatch):
    import json
    import threading
    import pytest_leela.engine as engine_module

    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source = tmp_path / "src/calc.py"
    source.write_text("def limit():\n    return 1 + 1\n")
    test = tmp_path / "tests/test_calc.py"
    test.write_text("import pytest\nimport calc\n"
                    "pytestmark = pytest.mark.skipif(calc.limit() != 2, reason='mutated')\n"
                    "def test_limit():\n    assert calc.limit() == 2\n")
    paused, release, started, done = [threading.Event() for _ in range(4)]
    real_runner = engine_module.run_tests_for_mutant
    errors, consumed, failures = [], [], []
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        before = daemon._test_signatures.copy()

        def pause_actual_error(*args, **kwargs):
            result = real_runner(*args, **kwargs)
            if result.status == "error" and not errors:
                errors.append(result)
                paused.set()
                assert release.wait(15)
            return result

        def scan():
            started.set()
            try:
                daemon._scan_for_changes(db)
            except BaseException as exc:
                failures.append(exc)
            finally:
                done.set()

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", pause_actual_error)
        poll = threading.Thread(target=scan)
        try:
            daemon._maybe_spawn_reanalysis(db)
            assert paused.wait(15), daemon.out.getvalue()
            test.write_text("import calc\ndef test_limit():\n    assert calc.limit() == 2\n")
            poll.start()
            assert started.wait(5)
            done.wait(.2)
            consumed.append(daemon._test_signatures != before)
        finally:
            release.set()
            _join_daemon_worker(daemon)
            if poll.ident is not None:
                poll.join(30)
                assert not poll.is_alive()
        assert consumed == [False] and not failures
        assert errors[0].error == "no tests ran" and errors[0].tests_run == 0
        rows = db._execute("SELECT state, killed, killing_test, details FROM mutants WHERE killed IS NULL").fetchall()
        assert rows
        for state, killed, killing_test, raw in rows:
            details = json.loads(raw)
            assert state == "dirty" and killed is None and killing_test is None
            assert details["last_error"]["message"] == "no tests ran"
            assert details["last_error"]["tests_run"] == 0
        assert db.get_dirty_symbols()
        assert not daemon._reanalyzing
        assert daemon._scan_for_changes(db) == []
        _analyze_lifecycle(daemon, db, results, progress)


def test_analysis_raised_real_cache_failure_releases_pending_request(tmp_path, monkeypatch):
    import py_compile
    from pathlib import Path
    from unittest.mock import patch
    import pytest_leela.engine as engine_module

    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source, helper, _ = _gate_inputs(tmp_path, "helper")
    real_runner = engine_module.run_tests_for_mutant
    failures, consumed = [], []
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        before = daemon._source_signatures.copy()

        def fail_actual_cache_removal(*args, **kwargs):
            if failures:
                return real_runner(*args, **kwargs)
            cache = Path(py_compile.compile(str(helper), doraise=True))
            unlink = Path.unlink

            def denied(path, *args, **kwargs):
                if path == cache:
                    raise PermissionError("active dependency cache unavailable")
                return unlink(path, *args, **kwargs)

            try:
                with patch.object(Path, "unlink", denied):
                    return real_runner(*args, **kwargs)
            except PermissionError as exc:
                failures.append(exc)
                source.write_text(source.read_text() + "# newer request\n")
                daemon._scan_for_changes(db)
                daemon._scan_for_changes(db)
                consumed.append(daemon._source_signatures != before)
                raise

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", fail_actual_cache_removal)
        daemon._maybe_spawn_reanalysis(db)
        _join_daemon_worker(daemon)
        assert failures and consumed == [False], daemon.out.getvalue()
        assert "active dependency cache unavailable" in daemon.out.getvalue()
        assert not results
        assert not db._execute("SELECT * FROM mutants").fetchall()
        assert db.get_dirty_symbols()
        assert any(e.status == "error" for e in progress)
        assert daemon._scan_for_changes(db) == []
        _analyze_lifecycle(daemon, db, results, progress)


def test_analysis_partial_real_run_releases_and_drains_request(tmp_path, monkeypatch):
    import pytest_leela.engine as engine_module
    import pytest_leela.resources as resources

    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source, _, replacement = _gate_inputs(tmp_path, "source")
    original_factory = daemon.engine_factory
    real_runner = engine_module.run_tests_for_mutant
    executions, consumed = [], []

    def limited_factory(db, callback):
        engine = original_factory(db, callback)
        run = engine.run

        def limited_run(*args, **kwargs):
            return run(*args, limits=resources.ResourceLimits(max_memory_percent=50), **kwargs)
        engine.run = limited_run
        return engine

    daemon.engine_factory = limited_factory
    # Model the supported memory-pressure early exit, not mutant outcomes.
    monkeypatch.setattr(resources, "check_memory_usage", lambda: 100.0 if executions else 0.0)
    with IndexDB(tmp_path / "owned.db") as db:
        daemon._initial_build(db)
        before = daemon._source_signatures.copy()

        def actual_result_then_pressure(*args, **kwargs):
            result = real_runner(*args, **kwargs)
            executions.append(result)
            source.write_text(replacement)
            daemon._scan_for_changes(db)
            daemon._scan_for_changes(db)
            consumed.append(daemon._source_signatures != before)
            return result

        with monkeypatch.context() as m:
            m.setattr(engine_module, "run_tests_for_mutant", actual_result_then_pressure)
            daemon._maybe_spawn_reanalysis(db)
            _join_daemon_worker(daemon)
        assert len(executions) == 1 and consumed == [False], daemon.out.getvalue()
        partial = results[-1][1]
        assert partial.mutants_tested == 1 < partial.total_mutants
        assert not partial.errors
        assert db.get_dirty_symbols()
        assert not daemon._reanalyzing
        daemon.engine_factory = original_factory
        assert daemon._scan_for_changes(db) == []
        after = _analyze_lifecycle(daemon, db, results, progress)
        assert after.target_sources[str(source)] == replacement


@pytest.mark.parametrize("retry", ["poll", "shutdown"])
def test_analysis_failed_deferred_scan_preserves_request_and_releases(tmp_path, monkeypatch, retry):
    """A consumed constant-only prefix must stay dirty after a later failure."""
    import json

    daemon, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    earlier = tmp_path / "src/a_boundary.py"
    later = tmp_path / "src/z_later.py"
    old = "BOUNDARY = 5\ndef beyond(value):\n    return value > BOUNDARY\n"
    earlier.write_text(old)
    original_later = "MARKER = 'stable'\n"
    later.write_text(original_later)
    (tmp_path / "tests/test_boundary.py").write_text(
        "from a_boundary import beyond\ndef test_boundary():\n    assert beyond(4) is False\n")
    walk = daemon._iter_source_files
    requested, failures, consumed, close_states = [], [], [], []
    trace = {"retry": retry, "walk": []}

    def actual_ordered_walk():
        # Retain the real discovered paths; explicitly control their order,
        # rather than assuming rglob/creation/alphabetical ordering.
        paths = list(walk())
        assert set(paths) == {earlier, later}
        for path in sorted(paths, key=[earlier, later].index):
            if requested:
                trace["walk"].append(path.name)
            yield path

    def gte(result):
        return next(r for r in result.results if r.mutant.replacement_op == "GtE")

    class RequestingOutput(io.StringIO):
        def write(self, message):
            if message.startswith("[analyze] done.") and not requested:
                requested.append(message)
                before = daemon._source_signatures.copy()
                earlier.write_text(earlier.read_text().replace("BOUNDARY = 5", "BOUNDARY = 4"))
                later.write_bytes(b"\xff")
                assert daemon._scan_for_changes(db_holder[0]) == []
                assert daemon._scan_for_changes(db_holder[0]) == []
                consumed.append(daemon._source_signatures != before)
            return super().write(message)

    worker = daemon._run_reanalysis

    def observe_worker_failure(*args):
        try:
            worker(*args)
        except UnicodeDecodeError as exc:
            failures.append(exc)

    db_holder = []

    def exercise(db):
        db_holder.append(db)
        initial = _analyze_lifecycle(daemon, db, results, progress)
        assert gte(initial).status == "survived"
        trace["initial_gte"] = gte(initial).status
        hashes = {s.id: s.source_hash for s in db.all_symbols()}
        # Real changed bytes request a second real pass. Function hashes stay
        # unchanged, and its completion callback then makes the adversarial edit.
        earlier.write_text(old + "# request completion callback\n")
        daemon._scan_for_changes(db)
        assert db.get_dirty_symbols()
        daemon.out = RequestingOutput()
        daemon._maybe_spawn_reanalysis(db)
        _join_daemon_worker(daemon)
        assert len(results) == 2 and gte(results[-1][1]).status == "survived"
        assert len(failures) == 1 and failures[0].object == b"\xff"
        assert consumed == [False]
        assert trace["walk"] == [earlier.name, later.name]
        assert daemon._scan_pending
        assert not daemon._application_owned and not daemon._reanalyzing
        assert {s.id: s.source_hash for s in db.all_symbols()} == hashes
        # Earlier valid input WAS consumed before the later decode failed.
        assert daemon._source_signatures[str(earlier)] == daemon._signature(earlier.read_bytes())
        assert daemon._source_signatures[str(later)] == daemon._signature(original_later.encode())
        trace.update({"exception": repr(failures[0]), "prefix_consumed": True,
                      "hashes_unchanged": True, "dirty_after_failure": db.get_dirty_symbols(),
                      "old_pass_gte": gte(results[-1][1]).status})
        later.write_text(original_later)
        if retry == "poll":
            # Both signatures now match: retry has no new event to rediscover.
            assert daemon._scan_for_changes(db) == []
            assert not daemon._scan_pending
            trace["dirty_after_retry"] = db.get_dirty_symbols()
            assert db.get_dirty_symbols(), "consumed constant-only prefix lost its invalidation"
            followup = _analyze_lifecycle(daemon, db, results, progress)
            assert gte(followup).status == "killed"
            assert followup.target_sources[str(earlier)] == earlier.read_text()
            assert any(e.status == "killed" and (e.op or "").endswith(":GtE") for e in progress)
            trace["followup_gte"] = gte(followup).status
        daemon.stop()

    close = IndexDB.close

    def observe_close(db):
        assert daemon._reanalyzer_thread is not None and not daemon._reanalyzer_thread.is_alive()
        assert not daemon._scan_pending and not daemon._application_owned
        thread = daemon._reanalyzer_thread
        daemon._maybe_spawn_reanalysis(db)
        assert daemon._reanalyzer_thread is thread, "scheduled after stop"
        close_states.append(db.get_dirty_symbols())
        close(db)

    try:
        with monkeypatch.context() as m:
            m.setattr(daemon, "_iter_source_files", actual_ordered_walk)
            m.setattr(daemon, "_run_reanalysis", observe_worker_failure)
            m.setattr(daemon, "_loop", exercise)
            m.setattr(IndexDB, "close", observe_close)
            daemon.run()
        assert close_states
        if retry == "shutdown":
            trace["dirty_after_shutdown"] = close_states[-1]
            assert close_states[-1], "shutdown forgot the consumed constant-only prefix"
            resumed = LeelaDaemon(tmp_path, test_dir=tmp_path / "tests", reanalyze_debounce=0,
                                  engine_factory=daemon.engine_factory, out=io.StringIO())
            with IndexDB(daemon.index_path) as db:
                resumed._initial_build(db)
                assert db.get_dirty_symbols()
                followup = _analyze_lifecycle(resumed, db, results, progress)
                assert gte(followup).status == "killed"
                assert followup.target_sources[str(earlier)] == earlier.read_text()
                assert any(e.status == "killed" and (e.op or "").endswith(":GtE") for e in progress)
                trace["followup_gte"] = gte(followup).status
        else:
            assert not close_states[-1]
    finally:
        trace["consumed_during_callback"] = consumed
        (tmp_path / "qa_partial_scan_trace.json").write_text(json.dumps(trace, indent=2))
        print("P1_TRACE " + json.dumps(trace))


@pytest.mark.parametrize("transient_invalid_save", [False, True])
def test_analysis_shutdown_drains_requested_scan_before_close(tmp_path, monkeypatch, transient_invalid_save):
    import threading
    import pytest_leela.engine as engine_module

    template, results, progress = _lifecycle_daemon(tmp_path, monkeypatch)
    source, _, replacement = _gate_inputs(tmp_path, "source")
    daemon = LeelaDaemon(tmp_path, test_dir=tmp_path / "tests", reanalyze_debounce=0,
                         engine_factory=template.engine_factory, out=io.StringIO())
    paused, release, closed = [threading.Event() for _ in range(3)]
    real_runner = engine_module.run_tests_for_mutant
    requests, consumed, scan_failures, run_failures, at_close = [], [], [], [], []
    original_worker = daemon._run_reanalysis
    original_close = IndexDB.close

    def actual_result_then_request(*args, **kwargs):
        result = real_runner(*args, **kwargs)
        if not requests:
            requests.append(result)
            before = daemon._source_signatures.copy()
            if transient_invalid_save:
                source.write_bytes(b"\xff")
            else:
                source.write_text(replacement)
            daemon._scan_for_changes(db_holder[0])
            daemon._scan_for_changes(db_holder[0])
            consumed.append(daemon._source_signatures != before)
            paused.set()
            assert release.wait(15)
        return result

    def observe_worker(*args):
        try:
            original_worker(*args)
        except UnicodeDecodeError as exc:
            scan_failures.append(exc)
            # Complete a real transient editor save. Shutdown must drain the
            # retained full-scan request AFTER this worker has finished.
            source.write_text(replacement)

    db_holder = []

    def poll_then_wait_for_stop(db):
        # Control the run-loop boundary, not Engine outcomes or DB mutations.
        # All requests still enter the real daemon scan and scheduler.
        db_holder.append(db)
        daemon._scan_for_changes(db)
        daemon._maybe_spawn_reanalysis(db)
        assert daemon._stop.wait(15)

    def inspect_before_close(db):
        worker = daemon._reanalyzer_thread
        assert worker is not None and not worker.is_alive()
        assert daemon._stop.is_set() and not daemon._reanalyzing
        assert consumed == [False], "shutdown callback consumed inputs during analysis"
        assert not daemon._scan_pending and not daemon._application_owned
        assert daemon._source_signatures[str(source)] == daemon._signature(source.read_bytes())
        at_close.append(db.get_dirty_symbols())
        assert at_close[-1], "shutdown lost the newer dirty request"
        daemon._maybe_spawn_reanalysis(db)
        assert daemon._reanalyzer_thread is worker, "spawned a worker after stop"
        original_close(db)
        closed.set()

    def run():
        try:
            daemon.run()
        except BaseException as exc:
            run_failures.append(exc)

    thread = threading.Thread(target=run)
    with monkeypatch.context() as m:
        m.setattr(engine_module, "run_tests_for_mutant", actual_result_then_request)
        m.setattr(daemon, "_run_reanalysis", observe_worker)
        m.setattr(daemon, "_loop", poll_then_wait_for_stop)
        m.setattr(IndexDB, "close", inspect_before_close)
        thread.start()
        try:
            assert paused.wait(15), daemon.out.getvalue()
            daemon.stop()  # must not acquire the pass-length ownership gate
            assert daemon._stop.is_set() and not closed.is_set()
            assert thread.is_alive()
        finally:
            daemon.stop()
            release.set()
            thread.join(30)
            assert not thread.is_alive()
    assert not run_failures, run_failures
    assert closed.is_set() and at_close
    assert consumed == [False]
    assert bool(scan_failures) is transient_invalid_save
    assert len(results) == 1
    assert not daemon._reanalyzer_thread.is_alive()
    # A different owner can resume the persisted pending work after shutdown.
    resumed = LeelaDaemon(tmp_path, test_dir=tmp_path / "tests", reanalyze_debounce=0,
                          engine_factory=template.engine_factory, out=io.StringIO())
    with IndexDB(daemon.index_path) as db:
        resumed._initial_build(db)
        assert db.get_dirty_symbols()
        after = _analyze_lifecycle(resumed, db, results, progress)
        assert after.target_sources[str(source)] == replacement


def describe_clean_to_dirty_verbose_transition():
    def it_handles_a_clean_symbol_before_a_dirty_symbol(tmp_path):
        captured = _run_verbose_reanalysis(
            tmp_path,
            [
                _symbol_start("clean", lineno=10, n=1),
                _symbol_start("dirty", lineno=20, n=1),
                _mutant("dirty", "killed", lineno=21),
            ],
            pending=["dirty"],
        )
        assert "[analyze]   dirty" in captured
