"""Daemon contracts for concurrent status reads and partial-file reanalysis."""

import importlib
import json

import pytest


@pytest.mark.parametrize("order", [("add", "inc"), ("inc", "add")])
def test_verbose_reanalysis_preserves_clean_symbols_after_partial_source_edit(
    tmp_path, monkeypatch, order,
):
    from pytest_leela.daemon import LeelaDaemon
    from pytest_leela.engine import Engine
    from pytest_leela.index import IndexDB
    import io

    bodies = {
        "add": "def add(x: int, y: int) -> int:\n    return x + y\n",
        "inc": "def inc(x: int) -> int:\n    return x + 1\n",
    }
    source = tmp_path / "toycalc.py"
    original = "\n".join(bodies[name] for name in order)
    source.write_text(original)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_toycalc.py").write_text(
        "from toycalc import add, inc\n"
        "def test_add():\n    assert add(2, 3) == 5\n"
        "def test_inc():\n    assert inc(2) > 2\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    output = io.StringIO()
    results = []
    errors = []

    class RecordingEngine(Engine):
        # Keep real coverage, mutation execution, indexing and progress wiring.
        # Re-raise daemon-caught failures below so RED includes the engine trace.
        def run(self, *args, **kwargs):
            try:
                result = super().run(*args, **kwargs)
            except Exception as exc:
                errors.append(exc)
                raise
            results.append(result)
            return result

    daemon = LeelaDaemon(
        tmp_path, target_dirs=(".",), test_dir=tests, out=output, verbose=True,
        engine_factory=lambda db, on_progress: RecordingEngine(
            index=db, on_progress=on_progress,
        ),
    )
    with IndexDB(daemon.index_path) as db:
        db.reconcile_file(str(source), original)
        ids = {symbol.id.rsplit(":", 1)[-1]: symbol.id for symbol in db.all_symbols()}
        daemon._run_reanalysis(db, db.get_dirty_symbols())
        if errors:
            raise errors[0]
        assert db.get_state(ids["add"]) == db.get_state(ids["inc"]) == "clean"
        clean_add = next(symbol for symbol in db.all_symbols() if symbol.id == ids["add"])
        assert results[0].coverage_map is not None
        for name in order:
            line = original.splitlines().index(bodies[name].splitlines()[1]) + 1
            assert results[0].coverage_map.tests_for(str(source), line)

        edited = original.replace("return x + 1", "return x + 2")
        source.write_text(edited)
        change = db.reconcile_file(str(source), edited)
        assert change.changed == {ids["inc"]}
        assert db.get_state(ids["add"]) == "clean"
        assert db.get_state(ids["inc"]) == "dirty"
        output.seek(0)
        output.truncate()
        daemon._reanalyzing = True
        daemon._run_reanalysis(db, db.get_dirty_symbols())
        if errors:
            raise errors[0]
        assert not daemon._reanalyzing
        assert db.get_state(ids["inc"]) == "clean"
        after_add = next(symbol for symbol in db.all_symbols() if symbol.id == ids["add"])
        assert after_add.state == clean_add.state == "clean"
        assert after_add.source_hash == clean_add.source_hash
        assert (after_add.start_line, after_add.end_line) == (
            clean_add.start_line, clean_add.end_line,
        )
        assert len(results) == 2
        assert results[1].mutants_tested > 0
        log = output.getvalue()
        assert "[analyze] done" in log
        assert "[analyze] error" not in log
        assert "[analyze]   inc " in log
        assert "[analyze]   add " not in log
        assert log.count(f"[analyze]   {ids['inc']} — ") == 1
        assert f"[analyze]   {ids['add']} — " not in log


@pytest.mark.parametrize("order", [("add", "inc"), ("inc", "add")])
def test_verbose_progress_flushes_only_pending_symbol_tallies(tmp_path, order):
    """Unit callback contract: fake engine events, real daemon and index.

    Unlike the real Engine regression above, this has no nested coverage
    collector that replaces the outer self-mutation session's line tracer.
    It tests progress filtering, not mutant execution or certification.
    """
    import io
    from pytest_leela.daemon import LeelaDaemon
    from pytest_leela.index import IndexDB
    from pytest_leela.models import EngineProgress

    source = tmp_path / "toycalc.py"
    bodies = {
        "add": "def add(x, y):\n    return x + y\n",
        "inc": "def inc(x):\n    return x + 1\n",
    }
    original = "\n".join(bodies[name] for name in order)
    source.write_text(original)
    output = io.StringIO()

    class ProgressEngine:
        def __init__(self, db, on_progress):
            self.db = db
            self.on_progress = on_progress

        def run(self, target_files, **kwargs):
            assert str(source) in target_files
            symbols = {sym.id.rsplit(":", 1)[-1]: sym for sym in self.db.all_symbols()}
            for name in order:
                sym = symbols[name]
                self.on_progress(EngineProgress(
                    kind="symbol-start", file_path=str(source),
                    lineno=sym.start_line + 1, symbol_id=sym.id,
                    symbol_short=name, n_mutants_in_symbol=1,
                ))
                self.on_progress(EngineProgress(
                    kind="mutant", file_path=str(source),
                    lineno=sym.start_line + 1, symbol_id=sym.id,
                    op="BinOp:Sub", status="killed",
                ))
            self.on_progress(EngineProgress(kind="done"))

    daemon = LeelaDaemon(
        tmp_path, target_dirs=(".",), out=output, verbose=True,
        engine_factory=ProgressEngine,
    )
    with IndexDB(daemon.index_path) as db:
        db.reconcile_file(str(source), original)
        ids = {sym.id.rsplit(":", 1)[-1]: sym.id for sym in db.all_symbols()}
        for sid in ids.values():
            db.mark_symbol_clean(sid)
        edited = original.replace("return x + 1", "return x + 2")
        source.write_text(edited)
        assert db.reconcile_file(str(source), edited).changed == {ids["inc"]}
        daemon._reanalyzing = True
        daemon._run_reanalysis(db, db.get_dirty_symbols())
        assert not daemon._reanalyzing
        # A progress-only stub must not certify unexecuted analysis work.
        assert db.get_state(ids["add"]) == "clean"
        assert db.get_state(ids["inc"]) == "dirty"
    log = output.getvalue()
    assert "[analyze] error" not in log
    assert "[analyze] done" in log
    assert "[analyze]   inc " in log
    assert "[analyze]   add " not in log
    assert log.count(f"[analyze]   {ids['inc']} — 1 mutants (1 killed)") == 1
    assert f"[analyze]   {ids['add']} — " not in log


@pytest.mark.parametrize("flag,detail", [
    ("--gaps", "gaps"),
    ("--by-file", "by_file"),
    ("--categorized", "categorized"),
])
def test_zero_gap_status_does_not_include_later_committed_details(
    tmp_path, monkeypatch, capsys, flag, detail,
):
    daemon = importlib.import_module("pytest_leela.daemon")
    from pytest_leela.index import IndexDB
    from pytest_leela.models import Mutant, MutantResult, MutationPoint

    source = tmp_path / "calc.py"
    source.write_text("def add(a, b):\n    return a + b\n")
    index_path = tmp_path / ".leela" / "index.db"
    point = MutationPoint(str(source), "calc", 2, 11, "BinOp", "Add", None)
    mutant = Mutant(point, "Sub", 0)

    with IndexDB(index_path) as writer:
        writer.reconcile_file(str(source), source.read_text())
        symbol = writer.all_symbols()[0]
        writer.write_mutant_result(
            symbol.id, mutant, MutantResult(mutant, True, 1, "test_add", 0.01),
            symbol.source_hash, "tests",
        )
        writer.mark_symbol_clean(symbol.id)
        original_count = IndexDB.coverage_gap_symbol_count
        committed = []

        def count_then_commit(reader):
            # The real read finishes before a different connection commits,
            # as can happen while the watcher updates a WAL-backed index.
            count = original_count(reader)
            assert reader is not writer
            assert count == 0
            writer.write_mutant_result(
                symbol.id, mutant, MutantResult(mutant, False, 1, None, 0.01),
                symbol.source_hash, "tests",
            )
            committed.append(True)
            return count

        monkeypatch.setattr(IndexDB, "coverage_gap_symbol_count", count_then_commit)
        assert daemon.main(["status", str(tmp_path), "--json", flag]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert committed == [True]
        assert original_count(writer) == 1
        assert payload["with_gaps"] == 0
        assert payload[detail] == (
            {"no_test_ran": [], "tests_ran": []}
            if detail == "categorized" else []
        )


def _seed_console_records(db):
    """Legitimate lifecycle states, independent symbol scores and row evidence."""
    states = ["clean", "clean", "dirty", "stale", "error", "analyzing"]
    for i, state in enumerate(states):
        db.reconcile_file(f"src/s{i}.py", f"def f{i}():\n    return {i}\n")
        db.mark_state(f"src.s{i}:f{i}", state)
    # One symbol has a score; the other five have NULL symbol scores.
    db._execute("UPDATE symbols SET mutation_score = 1 WHERE id = 'src.s0:f0'")
    for i, (state, killed) in enumerate([
        ("clean", 1), ("clean", 0), ("clean", None),
        ("stale", 1), ("dirty", 0), ("stale", None),
    ]):
        db._execute(
            "INSERT INTO mutants (symbol_id, location_line, operator, state, "
            "killed, source_hash, test_set_hash) VALUES (?, 2, 'Return', ?, ?, 's', 't')",
            (f"src.s{i}:f{i}", state, killed),
        )
    # The same test linked to two symbols is two links, not two executed tests.
    db._execute(
        "INSERT INTO symbol_tests VALUES ('src.s0:f0', 'tests::test_shared')"
    )
    db._execute(
        "INSERT INTO symbol_tests VALUES ('src.s1:f1', 'tests::test_shared')"
    )


@pytest.mark.parametrize("clean_symbols", [0, 2])
def test_console_empty_evidence_is_not_coverage(tmp_path, capsys, clean_symbols):
    daemon = importlib.import_module("pytest_leela.daemon")
    from dataclasses import asdict
    from pytest_leela.index import IndexDB

    path = tmp_path / ".leela" / "index.db"
    with IndexDB(path) as db:
        for i in range(clean_symbols):
            db.reconcile_file(f"s{i}.py", f"def f{i}():\n    return 1\n")
            db.mark_state(f"s{i}:f{i}", "clean")
        before = db._conn.total_changes
        snapshot = daemon._ConsoleStatus.from_db(db)
        assert asdict(snapshot) == {
            "total_symbols": clean_symbols, "clean": clean_symbols, "pending": 0,
            "indexed_mutants": 0, "killed": 0, "non_killed": 0, "unscored": 0,
            "stale_or_dirty_mutants": 0, "test_links": 0,
        }
        assert db._conn.total_changes == before
    assert daemon.main(["status", str(tmp_path)]) == 0  # unchanged exit semantics
    assert capsys.readouterr().out == (
        f"  status: {clean_symbols} symbols — {clean_symbols} clean, "
        "0 pending (dirty/stale/error/analyzing) | "
        "recorded indexed mutants: 0 (no results indexed) "
        "[killed=1: 0, non-killed=0: 0, unscored=NULL rows: 0; "
        "stale/dirty records: 0] | indexed test links: 0 (test coverage unknown)\n"
    )


def test_console_records_all_states_without_conflating_nulls_or_links(tmp_path, capsys):
    daemon = importlib.import_module("pytest_leela.daemon")
    from dataclasses import asdict
    from pytest_leela.index import IndexDB

    with IndexDB(tmp_path / ".leela" / "index.db") as db:
        _seed_console_records(db)
        snapshot = daemon._ConsoleStatus.from_db(db)
        assert asdict(snapshot) == {
            "total_symbols": 6, "clean": 2, "pending": 4,
            "indexed_mutants": 6, "killed": 2, "non_killed": 2, "unscored": 2,
            "stale_or_dirty_mutants": 3, "test_links": 2,
        }
        assert snapshot.clean + snapshot.pending == snapshot.total_symbols
        assert snapshot.killed + snapshot.non_killed + snapshot.unscored == 6
        assert db._execute(
            "SELECT COUNT(*) FROM symbols WHERE mutation_score IS NULL"
        ).fetchone()[0] == 5
    assert daemon.main(["status", str(tmp_path)]) == 1
    assert capsys.readouterr().out == (
        "  status: 6 symbols — 2 clean, 4 pending (dirty/stale/error/analyzing) | "
        "recorded indexed mutants: 6 [killed=1: 2, non-killed=0: 2, "
        "unscored=NULL rows: 2; stale/dirty records: 3] | "
        "indexed test links: 2 (test coverage unknown)\n"
    )


def test_console_fresh_records_still_do_not_establish_test_coverage(tmp_path):
    daemon = importlib.import_module("pytest_leela.daemon")
    from pytest_leela.index import IndexDB

    with IndexDB(tmp_path / "index.db") as db:
        _seed_console_records(db)
        db._execute("UPDATE symbols SET state = 'clean'")
        db._execute("UPDATE mutants SET state = 'clean'")
        snapshot = daemon._ConsoleStatus.from_db(db)
        assert (snapshot.total_symbols, snapshot.clean, snapshot.pending) == (6, 6, 0)
        assert snapshot.stale_or_dirty_mutants == 0
        text = snapshot.render()
        assert "no results indexed" not in text
        assert "test coverage unknown" in text
        assert "covered" not in text
        assert "survived" not in text.lower()
        assert "%" not in text


def test_console_aggregate_snapshot_survives_concurrent_wal_commit(tmp_path, monkeypatch):
    daemon = importlib.import_module("pytest_leela.daemon")
    from pytest_leela.index import IndexDB

    path = tmp_path / "index.db"
    with IndexDB(path) as reader, IndexDB(path) as writer:
        _seed_console_records(writer)
        original_execute = reader._execute
        statements = []

        def read_then_commit(sql, params=()):
            cursor = original_execute(sql, params)
            statements.append(sql)
            # The actual SELECT has started, but its row is not fetched yet.
            # A different connection can commit in WAL mode at this boundary.
            with writer.transaction():
                writer._execute("DELETE FROM mutants")
                writer._execute("DELETE FROM symbol_tests")
                writer._execute("UPDATE symbols SET state = 'clean'")
            return cursor

        monkeypatch.setattr(reader, "_execute", read_then_commit)
        snapshot = daemon._ConsoleStatus.from_db(reader)
        assert len(statements) == 1
        assert (snapshot.clean, snapshot.pending) == (2, 4)
        assert (snapshot.indexed_mutants, snapshot.killed, snapshot.non_killed,
                snapshot.unscored, snapshot.test_links) == (6, 2, 2, 2, 2)
        monkeypatch.setattr(reader, "_execute", original_execute)
        next_snapshot = daemon._ConsoleStatus.from_db(reader)
        assert (next_snapshot.clean, next_snapshot.pending) == (6, 0)
        assert (next_snapshot.indexed_mutants, next_snapshot.test_links) == (0, 0)


def test_console_counters_do_not_change_legacy_asdict_or_json(tmp_path, capsys):
    daemon = importlib.import_module("pytest_leela.daemon")
    from dataclasses import asdict
    from pytest_leela.index import IndexDB

    path = tmp_path / ".leela" / "index.db"
    with IndexDB(path) as db:
        _seed_console_records(db)
        assert asdict(daemon.DaemonStatus.from_db(db)) == {
            "total_symbols": 6, "clean": 2, "dirty": 1, "stale": 1, "error": 1,
            "pending_reanalysis": 4, "with_gaps": 1, "covered": 1,
        }
    assert daemon.main(["status", str(tmp_path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "project": str(tmp_path.resolve()), "index": str(path),
        "total_symbols": 6, "covered": 1, "with_gaps": 1,
        "pending_reanalysis": 4,
    }
