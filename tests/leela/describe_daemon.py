"""Tests for pytest_leela.daemon - the filesystem watcher daemon."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from pytest_leela.daemon import (
    ChangeEvent,
    DaemonStatus,
    LeelaDaemon,
)
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

    def it_reports_missing_source_import_without_dirting(tmp_path: Path):
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

            # existing_fn should still be CLEAN — the new test
            # doesn't cover it.
            for sym in db.all_symbols():
                assert sym.state == STATE_CLEAN, (
                    f"expected CLEAN, got {sym.state} for {sym.id}"
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


def describe_run_reanalysis_emits_per_symbol_log_lines():
    """The reanalysis loop must print one summary line per symbol
    plus one line per mutant, so the operator can see *what* the
    daemon is doing — not just ``status: 30 clean, ...``."""

    def it_prints_symbol_start_summary_and_done(tmp_path, capsys):
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

            # No symbols dirtied (collection failed).
            for sym in db.all_symbols():
                assert sym.state == STATE_CLEAN

            # Daemon logged the failure.
            output = _read(daemon.out)
            assert "scoped collection failed" in output, output

    def it_reports_missing_function_import_in_the_daemon_log(tmp_path: Path):
        """When the changed test file covers 0 source lines —
        TDD test-first, or a test that only touches stdlib —
        the daemon prints a signal so the operator understands
        why nothing got dirtied."""
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

            # existing_fn still CLEAN.
            for sym in db.all_symbols():
                assert sym.state == STATE_CLEAN, (
                    f"expected CLEAN, got {sym.state} for {sym.id}"
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
        daemon._iter_test_files()
        for py in daemon._iter_test_files():
            daemon._test_mtimes[str(py)] = py.stat().st_mtime

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
