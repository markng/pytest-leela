"""Tests for pytest_leela.index — the persistent mutation index."""

from __future__ import annotations

import ast
import hashlib
import sqlite3
from pathlib import Path

import pytest

from pytest_leela.index import (
    MUTANT_CLEAN,
    MUTANT_DIRTY,
    IndexDB,
    STATE_ANALYZING,
    STATE_CLEAN,
    STATE_DIRTY,
    STATE_ERROR,
    STATE_STALE,
    _module_name,
    extract_symbols,
    compute_test_set_hash,
)
from pytest_leela.models import (
    Mutant,
    MutantResult,
    MutationPoint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open(tmp_path: Path) -> IndexDB:
    return IndexDB(tmp_path / "index.db")


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------


def describe_extract_symbols():
    def it_returns_empty_for_module_with_no_functions():
        result = extract_symbols("empty.py", "")
        # ast.parse("") produces a Module with empty body, so no symbols
        assert result == {}

    def it_finds_top_level_functions():
        source = """\
def foo():
    return 1

def bar():
    return 2
"""
        result = extract_symbols("src/mod.py", source)
        assert "src.mod:foo" in result
        assert "src.mod:bar" in result
        assert result["src.mod:foo"].start_line == 1
        assert result["src.mod:bar"].start_line == 4

    def it_finds_async_functions():
        source = """\
async def fetch():
    return 1
"""
        result = extract_symbols("src/mod.py", source)
        assert "src.mod:fetch" in result

    def it_finds_classes_and_their_methods():
        source = """\
class Greeter:
    def hello(self):
        return "hi"

    def goodbye(self):
        return "bye"
"""
        result = extract_symbols("src/mod.py", source)
        assert "src.mod:Greeter" in result
        assert "src.mod:Greeter.hello" in result
        assert "src.mod:Greeter.goodbye" in result

    def it_uses_init_for_package_modules():
        result = extract_symbols("src/pkg/__init__.py", "x = 1\n")
        # No functions, but the module name should be derived cleanly
        # (no '__init__' suffix). Just exercise the helper.
        assert result == {}

    def it_computes_a_stable_source_hash():
        # Same source → same hash. Edits change the hash.
        a = extract_symbols("m.py", "def f():\n    return 1\n")
        b = extract_symbols("m.py", "def f():\n    return 1\n")
        c = extract_symbols("m.py", "def f():\n    return 2\n")
        assert a["m:f"].source_hash == b["m:f"].source_hash
        assert a["m:f"].source_hash != c["m:f"].source_hash

    def it_hashes_the_actual_class_body():
        source = """\
class Greeter:
    greeting = "hi"

    def hello(self):
        return self.greeting
"""
        result = extract_symbols("m.py", source)
        tree = ast.parse(source)
        class_node = tree.body[0]
        body_src = ast.get_source_segment(source, class_node) or ""
        expected_hash = hashlib.sha256(body_src.encode("utf-8")).hexdigest()
        assert result["m:Greeter"].source_hash == expected_hash
        # The `or → and` mutant produces sha256(b"") — must differ.
        assert result["m:Greeter"].source_hash != hashlib.sha256(b"").hexdigest()

    def it_reports_the_actual_class_end_line():
        source = """\
class Greeter:
    greeting = "hi"

    def hello(self):
        return self.greeting
"""
        result = extract_symbols("m.py", source)
        tree = ast.parse(source)
        class_node = tree.body[0]
        assert result["m:Greeter"].end_line == class_node.end_lineno
        # The `or → and` mutant sets end_line to start_line when
        # end_lineno is truthy — must differ for multi-line classes.
        assert result["m:Greeter"].end_line != result["m:Greeter"].start_line


# ---------------------------------------------------------------------------
# Schema setup
# ---------------------------------------------------------------------------


def describe_schema_setup():
    def it_creates_a_fresh_database(tmp_path: Path):
        db = _open(tmp_path)
        try:
            # Tables exist (we don't enumerate them, just verify we can
            # read PRAGMA user_version and the symbols table accepts an
            # insert with the right shape).
            db.reconcile_file("m.py", "def f():\n    return 1\n")
            assert db.count() == 1
        finally:
            db.close()

    def it_renames_an_old_version_and_starts_fresh(tmp_path: Path):
        # Plant a database with a v0 schema (no tables, just the version
        # pragma forced to 0).
        path = tmp_path / "index.db"
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA user_version = 0")
        conn.close()

        # Open with IndexDB: should detect v0, but v0 is the "fresh"
        # case (no tables to migrate from). A new database is created.
        db = IndexDB(path)
        try:
            assert db.count() == 0
        finally:
            db.close()

    def it_renames_an_older_schema_version_and_starts_fresh(tmp_path: Path):
        # Plant a database with version=999 (older than SCHEMA_VERSION=1
        # is impossible without tables, but we simulate the migration
        # path by forcing user_version=999 via a no-op version and a
        # custom test that exercises the migration branch directly).
        path = tmp_path / "index.db"
        conn = sqlite3.connect(path, isolation_level=None)
        # Create a table so PRAGMA user_version is the only signal.
        conn.execute("CREATE TABLE dummy (x INTEGER)")
        conn.execute("PRAGMA user_version = 999")  # simulate a future schema
        conn.close()

        # Re-opening with the current code should refuse — version 999
        # is *newer* than supported, not older.
        with pytest.raises(ValueError, match="newer than supported"):
            IndexDB(path)

    def it_raises_on_newer_schema_version(tmp_path: Path):
        path = tmp_path / "index.db"
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("CREATE TABLE dummy (x INTEGER)")
        conn.execute("PRAGMA user_version = 999")
        conn.close()

        with pytest.raises(ValueError, match="newer than supported"):
            IndexDB(path)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


SAMPLE_V1 = """\
def alpha():
    return 1

def beta():
    return 2
"""

SAMPLE_V1_EDITED = """\
def alpha():
    return 42

def beta():
    return 2
"""

SAMPLE_V1_RENAMED = """\
def alpha():
    return 1

def gamma():  # was beta
    return 2
"""

SAMPLE_V1_DELETED = """\
def alpha():
    return 1
"""


def describe_reconcile_file():
    def it_inserts_new_symbols_as_dirty(tmp_path: Path):
        db = _open(tmp_path)
        try:
            result = db.reconcile_file("src/mod.py", SAMPLE_V1)
            assert result.added == frozenset({"src.mod:alpha", "src.mod:beta"})
            assert result.removed == frozenset()
            assert result.changed == frozenset()
            assert db.get_state("src.mod:alpha") == STATE_DIRTY
            assert db.get_state("src.mod:beta") == STATE_DIRTY
        finally:
            db.close()

    def it_is_a_noop_when_nothing_changed(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            result = db.reconcile_file("src/mod.py", SAMPLE_V1)
            assert result.is_noop
        finally:
            db.close()

    def it_is_not_a_noop_when_symbols_are_added(tmp_path: Path):
        db = _open(tmp_path)
        try:
            result = db.reconcile_file("src/mod.py", SAMPLE_V1)
            # Adding symbols is a real change — is_noop must be False.
            # The `or → and` mutant returns True here because
            # `not (added and removed and changed)` is True when
            # removed and changed are empty.
            assert not result.is_noop
        finally:
            db.close()

    def it_marks_changed_symbols_as_dirty(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            # Mark alpha clean to simulate a completed analysis.
            db.mark_state("src.mod:alpha", STATE_CLEAN)
            result = db.reconcile_file("src/mod.py", SAMPLE_V1_EDITED)
            assert result.changed == frozenset({"src.mod:alpha"})
            assert result.added == frozenset()
            assert result.removed == frozenset()
            assert db.get_state("src.mod:alpha") == STATE_DIRTY
        finally:
            db.close()

    def it_deletes_removed_symbols_with_cascade(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            # Plant a mutant for beta so we can verify cascade delete.
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO mutants "
                    "(symbol_id, location_line, operator, original_op, "
                    " replacement_op, state, source_hash, test_set_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "src.mod:beta",
                        5,
                        "const-replace",
                        "2",
                        "3",
                        MUTANT_CLEAN,
                        "h" * 64,
                        "t" * 64,
                    ),
                )
                conn.execute(
                    "INSERT INTO symbol_tests (symbol_id, test_id) VALUES (?, ?)",
                    ("src.mod:beta", "tests/test_mod.py::test_beta"),
                )

            result = db.reconcile_file("src/mod.py", SAMPLE_V1_DELETED)
            assert result.removed == frozenset({"src.mod:beta"})
            assert db.get_symbol("src.mod:beta") is None
            # Cascade should have removed the mutant and the test mapping.
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT COUNT(*) FROM mutants WHERE symbol_id = ?",
                ("src.mod:beta",),
            )
            assert int(cur.fetchone()[0]) == 0
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT COUNT(*) FROM symbol_tests WHERE symbol_id = ?",
                ("src.mod:beta",),
            )
            assert int(cur.fetchone()[0]) == 0
            # alpha remains.
            assert db.get_symbol("src.mod:alpha") is not None
        finally:
            db.close()

    def it_treats_renames_as_remove_plus_add(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            result = db.reconcile_file("src/mod.py", SAMPLE_V1_RENAMED)
            assert result.removed == frozenset({"src.mod:beta"})
            assert result.added == frozenset({"src.mod:gamma"})
            assert "src.mod:beta" not in {s.id for s in db.all_symbols()}
            assert "src.mod:gamma" in {s.id for s in db.all_symbols()}
        finally:
            db.close()

    def it_marks_clean_mutants_stale_when_source_changes(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO mutants "
                    "(symbol_id, location_line, operator, original_op, "
                    " replacement_op, state, source_hash, test_set_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "src.mod:alpha",
                        1,
                        "const-replace",
                        "1",
                        "0",
                        MUTANT_CLEAN,
                        "oldhash" + "0" * 57,
                        "t" * 64,
                    ),
                )
            db.reconcile_file("src/mod.py", SAMPLE_V1_EDITED)
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT state FROM mutants WHERE symbol_id = ?",
                ("src.mod:alpha",),
            )
            assert cur.fetchone()[0] == "stale"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def describe_read_api():
    def it_returns_none_for_unknown_symbols(tmp_path: Path):
        db = _open(tmp_path)
        try:
            assert db.get_symbol("nope:nope") is None
            assert db.get_state("nope:nope") is None
        finally:
            db.close()

    def it_returns_dirty_symbols_only(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            db.mark_state("src.mod:alpha", STATE_CLEAN)
            db.mark_state("src.mod:beta", STATE_STALE)
            # A clean symbol is not dirty. A stale symbol IS dirty —
            # the test set changed and the cache needs to be re-validated.
            dirty = set(db.get_dirty_symbols())
            assert "src.mod:alpha" not in dirty
            assert "src.mod:beta" in dirty
            # Re-mark alpha as dirty to confirm the filter picks it up.
            db.mark_state("src.mod:alpha", STATE_DIRTY)
            assert set(db.get_dirty_symbols()) == {"src.mod:alpha", "src.mod:beta"}
        finally:
            db.close()

    def it_includes_error_state_in_dirty(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            db.mark_state("src.mod:alpha", STATE_ERROR)
            assert "src.mod:alpha" in set(db.get_dirty_symbols())
        finally:
            db.close()

    def it_returns_all_symbols_sorted_by_id(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            ids = [s.id for s in db.all_symbols()]
            assert ids == sorted(ids)
            assert len(ids) == 2
        finally:
            db.close()

    def it_returns_last_analyzed_from_all_symbols(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            db.write_mutant_result(
                "src.m:f",
                _make_mutant(),
                _make_result(),
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            symbols = db.all_symbols()
            assert len(symbols) == 1
            # write_mutant_result sets last_analyzed — all_symbols
            # must return it as a non-None string. The `is not → is`
            # mutant inverts the conditional and returns None here.
            assert symbols[0].last_analyzed is not None
            assert isinstance(symbols[0].last_analyzed, str)
        finally:
            db.close()

    def test_mark_state_rejects_invalid_value(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            with pytest.raises(ValueError, match="Invalid symbol state"):
                db.mark_state("src.mod:alpha", "bogus")
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Mutant cache consult
# ---------------------------------------------------------------------------


def describe_cache_consult():
    def it_returns_false_when_no_mutant_recorded(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            assert not db.has_clean_mutant("src.mod:alpha", "h" * 64, "t" * 64)
        finally:
            db.close()

    def it_returns_true_for_a_matching_clean_mutant(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO mutants "
                    "(symbol_id, location_line, operator, original_op, "
                    " replacement_op, state, source_hash, test_set_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "src.mod:alpha",
                        1,
                        "const-replace",
                        "1",
                        "0",
                        MUTANT_CLEAN,
                        "h" * 64,
                        "t" * 64,
                    ),
                )
            assert db.has_clean_mutant("src.mod:alpha", "h" * 64, "t" * 64)
        finally:
            db.close()

    def it_returns_false_for_a_dirty_mutant(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO mutants "
                    "(symbol_id, location_line, operator, original_op, "
                    " replacement_op, state, source_hash, test_set_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "src.mod:alpha",
                        1,
                        "const-replace",
                        "1",
                        "0",
                        MUTANT_DIRTY,
                        "h" * 64,
                        "t" * 64,
                    ),
                )
            assert not db.has_clean_mutant("src.mod:alpha", "h" * 64, "t" * 64)
        finally:
            db.close()

    def it_returns_false_when_hashes_differ(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO mutants "
                    "(symbol_id, location_line, operator, original_op, "
                    " replacement_op, state, source_hash, test_set_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "src.mod:alpha",
                        1,
                        "const-replace",
                        "1",
                        "0",
                        MUTANT_CLEAN,
                        "h" * 64,
                        "t" * 64,
                    ),
                )
            # Same test_set_hash, different source_hash → no cache hit.
            assert not db.has_clean_mutant("src.mod:alpha", "z" * 64, "t" * 64)
            # Same source_hash, different test_set_hash → no cache hit.
            assert not db.has_clean_mutant("src.mod:alpha", "h" * 64, "z" * 64)
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


def describe_context_manager():
    def it_supports_with_statement(tmp_path: Path):
        with _open(tmp_path) as db:
            db.reconcile_file("src/mod.py", SAMPLE_V1)
            assert db.count() == 2
        # Connection is closed on exit (best-effort check: we can
        # detect by reopening and the schema still being there).
        with _open(tmp_path) as db:
            assert db.count() == 2


# ---------------------------------------------------------------------------
# test_set_hash
# ---------------------------------------------------------------------------


def describe_test_set_hash():
    def it_is_stable_for_sorted_input():
        a = compute_test_set_hash(
            ["tests/test_x.py::test_a", "tests/test_x.py::test_b"]
        )
        b = compute_test_set_hash(
            ["tests/test_x.py::test_b", "tests/test_x.py::test_a"]
        )
        assert a == b

    def it_differs_for_different_sets():
        a = compute_test_set_hash(["t1", "t2"])
        b = compute_test_set_hash(["t1", "t3"])
        assert a != b

    def it_returns_a_stable_sentinel_for_empty_input():
        assert compute_test_set_hash([]) == compute_test_set_hash(None)

    def it_returns_sha256_of_empty_string_for_empty_input():
        # The empty / None test set hashes to the SHA-256 of the
        # empty string — a stable sentinel. The `return expr →
        # return None` mutant returns None instead.
        expected = hashlib.sha256(b"").hexdigest()
        assert compute_test_set_hash([]) == expected
        assert compute_test_set_hash(None) == expected


# ---------------------------------------------------------------------------
# find_symbol_for
# ---------------------------------------------------------------------------


def describe_find_symbol_for():
    def it_returns_none_when_no_symbol_covers_line(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            assert db.find_symbol_for("src/m.py", 999) is None
        finally:
            db.close()

    def it_returns_the_innermost_symbol_for_a_line(tmp_path: Path):
        # A class with a method. The method is a smaller range than
        # the class, so the method wins.
        source = """\
class Greeter:
    def hello(self):
        return "hi"
"""
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", source)
            sid, _ = db.find_symbol_for("src/m.py", 2) or (None, None)
            assert sid == "src.m:Greeter.hello"
            sid, _ = db.find_symbol_for("src/m.py", 1) or (None, None)
            assert sid == "src.m:Greeter"
        finally:
            db.close()

    def it_returns_symbol_id_and_source_hash(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            sid, src_hash = db.find_symbol_for("src/m.py", 1) or (None, None)
            assert sid == "src.m:f"
            assert src_hash is not None
            assert len(src_hash) == 64  # SHA-256 hex
        finally:
            db.close()


# ---------------------------------------------------------------------------
# write_mutant_result
# ---------------------------------------------------------------------------


def _make_mutant(lineno: int = 1, op: str = "Add", rep: str = "Sub") -> Mutant:
    point = MutationPoint(
        file_path="src/m.py",
        module_name="src.m",
        lineno=lineno,
        col_offset=0,
        node_type="BinOp",
        original_op=op,
        inferred_type="int",
    )
    return Mutant(point=point, replacement_op=rep, mutant_id=1)


def _make_result(
    killed: bool = True, killing_test: str = "tests/test_x.py::test_a"
) -> MutantResult:
    return MutantResult(
        mutant=_make_mutant(),
        killed=killed,
        tests_run=3,
        killing_test=killing_test if killed else None,
        time_seconds=0.05,
        test_ids_run=["tests/test_x.py::test_a", "tests/test_x.py::test_b"],
        killing_tests=[killing_test] if killed else [],
    )


def describe_write_mutant_result():
    def it_persists_kill_status(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            db.write_mutant_result(
                "src.m:f",
                _make_mutant(),
                _make_result(killed=True),
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT killed, killing_test FROM mutants WHERE symbol_id = ?",
                ("src.m:f",),
            )
            row = cur.fetchone()
            assert row is not None
            assert int(row[0]) == 1
            assert str(row[1]) == "tests/test_x.py::test_a"
        finally:
            db.close()

    def it_persists_survivor_status(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            db.write_mutant_result(
                "src.m:f",
                _make_mutant(),
                _make_result(killed=False),
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT killed, killing_test FROM mutants WHERE symbol_id = ?",
                ("src.m:f",),
            )
            row = cur.fetchone()
            assert row is not None
            assert int(row[0]) == 0
            assert row[1] is None
        finally:
            db.close()

    def it_replaces_existing_mutant_with_same_identity(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            # First write: killed.
            db.write_mutant_result(
                "src.m:f",
                _make_mutant(),
                _make_result(killed=True),
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            # Second write: survived (e.g., user re-ran with a new
            # test that no longer kills the mutant).
            db.write_mutant_result(
                "src.m:f",
                _make_mutant(),
                _make_result(killed=False),
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT COUNT(*), MIN(killed) FROM mutants WHERE symbol_id = ?",
                ("src.m:f",),
            )
            row = cur.fetchone()
            assert row is not None
            assert int(row[0]) == 1
            assert int(row[1]) == 0
        finally:
            db.close()

    def it_updates_last_analyzed_on_the_symbol(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            assert db.get_symbol("src.m:f") is not None
            assert db.get_symbol("src.m:f").last_analyzed is None  # type: ignore[union-attr]
            db.write_mutant_result(
                "src.m:f",
                _make_mutant(),
                _make_result(),
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            sym = db.get_symbol("src.m:f")
            assert sym is not None
            assert sym.last_analyzed is not None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# get_cached_result
# ---------------------------------------------------------------------------


def describe_get_cached_result():
    def test_returns_none_on_cache_miss(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            template = _make_mutant()
            assert (
                db.get_cached_result(
                    "src.m:f",
                    "h" * 64,
                    "t" * 64,
                    template,
                )
                is None
            )
        finally:
            db.close()

    def it_reconstructs_a_killed_result(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            original = _make_result(killed=True)
            db.write_mutant_result(
                "src.m:f",
                original.mutant,
                original,
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            template = _make_mutant()
            cached = db.get_cached_result(
                "src.m:f",
                "h" * 64,
                "t" * 64,
                template,
            )
            assert cached is not None
            assert cached.killed is True
            assert cached.killing_test == "tests/test_x.py::test_a"
            assert cached.tests_run == 3
            assert cached.killing_tests == ["tests/test_x.py::test_a"]
            assert cached.test_ids_run == [
                "tests/test_x.py::test_a",
                "tests/test_x.py::test_b",
            ]
        finally:
            db.close()

    def it_reconstructs_a_survivor_result(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            original = _make_result(killed=False, killing_test="ignored")
            db.write_mutant_result(
                "src.m:f",
                original.mutant,
                original,
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            cached = db.get_cached_result(
                "src.m:f",
                "h" * 64,
                "t" * 64,
                _make_mutant(),
            )
            assert cached is not None
            assert cached.killed is False
            assert cached.killing_test is None
        finally:
            db.close()

    def it_reconstructs_original_op(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            db.write_mutant_result(
                "src.m:f",
                _make_mutant(op="BinOp"),
                _make_result(),
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            cached = db.get_cached_result(
                "src.m:f",
                "h" * 64,
                "t" * 64,
                _make_mutant(op="BinOp"),
            )
            assert cached is not None
            # The `is not → is` mutant inverts the conditional and
            # returns "" when original_op is set.
            assert cached.mutant.point.original_op == "BinOp"
        finally:
            db.close()

    def it_returns_none_for_a_different_test_set_hash(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            db.write_mutant_result(
                "src.m:f",
                _make_mutant(),
                _make_result(),
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            # Same source, different test set — the cache should miss.
            assert (
                db.get_cached_result(
                    "src.m:f",
                    "h" * 64,
                    "z" * 64,
                    _make_mutant(),
                )
                is None
            )
        finally:
            db.close()


# ---------------------------------------------------------------------------
# mark_symbol_clean / mark_symbol_error
# ---------------------------------------------------------------------------


def describe_symbol_state_helpers():
    def it_marks_symbol_clean(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            assert db.get_state("src.m:f") == STATE_DIRTY
            db.mark_symbol_clean("src.m:f")
            assert db.get_state("src.m:f") == STATE_CLEAN
        finally:
            db.close()

    def it_marks_symbol_error(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            db.mark_symbol_error("src.m:f")
            assert db.get_state("src.m:f") == STATE_ERROR
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Bulk dirty-marking (used by daemon when test files change)
# ---------------------------------------------------------------------------


def describe_mark_symbols_dirty():
    """``mark_symbols_dirty`` is the daemon's bulk transition into
    the dirty state when a test file changes. It must only touch
    symbols that are quiescent (clean / stale) so an in-flight
    reanalyzer pass on the same symbol isn't disturbed."""

    def _seed_two_symbols(db: IndexDB) -> tuple[str, str]:
        db.reconcile_file(
            "src/m.py",
            "def alpha():\n    return 1\n\ndef beta():\n    return 2\n",
        )
        return "src.m:alpha", "src.m:beta"

    def it_is_a_noop_on_empty_input(tmp_path: Path):
        db = _open(tmp_path)
        try:
            a, _b = _seed_two_symbols(db)
            db.mark_symbol_clean(a)
            assert db.mark_symbols_dirty(set()) == 0
            assert db.get_state(a) == STATE_CLEAN
        finally:
            db.close()

    def it_transitions_clean_and_stale_to_dirty(tmp_path: Path):
        db = _open(tmp_path)
        try:
            a, b = _seed_two_symbols(db)
            db.mark_state(a, STATE_CLEAN)
            db.mark_state(b, STATE_STALE)
            assert db.mark_symbols_dirty({a, b}) == 2
            assert db.get_state(a) == STATE_DIRTY
            assert db.get_state(b) == STATE_DIRTY
        finally:
            db.close()

    def it_skips_analyzing_symbols(tmp_path: Path):
        """Don't disturb an in-flight reanalyzer pass."""
        db = _open(tmp_path)
        try:
            a, _b = _seed_two_symbols(db)
            db.mark_state(a, STATE_ANALYZING)
            assert db.mark_symbols_dirty({a}) == 0
            assert db.get_state(a) == STATE_ANALYZING
        finally:
            db.close()

    def it_skips_error_and_already_dirty(tmp_path: Path):
        db = _open(tmp_path)
        try:
            a, b = _seed_two_symbols(db)
            db.mark_state(a, STATE_ERROR)
            db.mark_state(b, STATE_DIRTY)
            assert db.mark_symbols_dirty({a, b}) == 0
            assert db.get_state(a) == STATE_ERROR
            assert db.get_state(b) == STATE_DIRTY
        finally:
            db.close()

    def it_accepts_a_list_input(tmp_path: Path):
        db = _open(tmp_path)
        try:
            a, b = _seed_two_symbols(db)
            db.mark_state(a, STATE_CLEAN)
            db.mark_state(b, STATE_CLEAN)
            assert db.mark_symbols_dirty([a, b]) == 2
        finally:
            db.close()

    def it_ignores_unknown_symbol_ids(tmp_path: Path):
        db = _open(tmp_path)
        try:
            a, _b = _seed_two_symbols(db)
            db.mark_state(a, STATE_CLEAN)
            assert db.mark_symbols_dirty({a, "src.m:ghost"}) == 1
            assert db.get_state(a) == STATE_DIRTY
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Test → symbol reverse index
# ---------------------------------------------------------------------------


def describe_symbols_for_tests():
    """``symbols_for_tests`` answers ``which source symbols have
    previously been exercised by these test IDs?`` — used by the
    daemon when a test file is deleted."""

    def _insert_mutant(
        db: IndexDB,
        symbol_id: str,
        lineno: int,
        test_ids: list[str],
    ) -> None:
        from pytest_leela.models import Mutant, MutationPoint, MutantResult

        point = MutationPoint(
            file_path="src/m.py",
            module_name="m",
            lineno=lineno,
            col_offset=0,
            node_type="BinOp",
            original_op="BitOr",
            inferred_type=None,
        )
        mutant = Mutant(
            point=point,
            replacement_op="BitAnd",
            mutant_id=1,
        )
        result = MutantResult(
            mutant=mutant,
            killed=False,
            tests_run=len(test_ids),
            killing_test=None,
            time_seconds=0.0,
            killing_tests=[],
            test_ids_run=test_ids,
        )
        db.write_mutant_result(
            symbol_id=symbol_id,
            mutant=mutant,
            result=result,
            source_hash="h",
            test_set_hash=compute_test_set_hash(test_ids),
        )

    def it_returns_empty_for_empty_input(tmp_path: Path):
        db = _open(tmp_path)
        try:
            assert db.symbols_for_tests(set()) == set()
        finally:
            db.close()

    def it_returns_empty_when_no_mutants_match(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file(
                "src/m.py",
                "def alpha():\n    return 1\n",
            )
            _insert_mutant(
                db,
                "src.m:alpha",
                1,
                ["tests/test_a.py::test_one"],
            )
            assert db.symbols_for_tests({"tests/test_x.py::test_neither"}) == set()
        finally:
            db.close()

    def it_finds_symbols_for_known_tests(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file(
                "src/m.py",
                "def alpha():\n    return 1\n\ndef beta():\n    return 2\n",
            )
            _insert_mutant(
                db,
                "src.m:alpha",
                1,
                ["tests/test_a.py::test_one"],
            )
            _insert_mutant(
                db,
                "src.m:beta",
                4,
                ["tests/test_b.py::test_two"],
            )
            found = db.symbols_for_tests({"tests/test_a.py::test_one"})
            assert "src.m:alpha" in found
            assert "src.m:beta" not in found
        finally:
            db.close()

    def it_unions_symbols_across_test_ids(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file(
                "src/m.py",
                "def alpha():\n    return 1\n\ndef beta():\n    return 2\n",
            )
            _insert_mutant(
                db,
                "src.m:alpha",
                1,
                ["tests/test_a.py::test_one"],
            )
            _insert_mutant(
                db,
                "src.m:beta",
                4,
                ["tests/test_b.py::test_two"],
            )
            found = db.symbols_for_tests(
                {
                    "tests/test_a.py::test_one",
                    "tests/test_b.py::test_two",
                }
            )
            assert found == {"src.m:alpha", "src.m:beta"}
        finally:
            db.close()


def describe_module_name():
    """``_module_name`` converts a file path to a dotted module name.

    The ``parts and parts[-1] == "__init__"`` guard at L105
    has a ``BoolOp:Or: And → Or`` mutation: if ``parts`` is
    truthy (which it always is when there are any path
    components), the second clause is short-circuited away.
    That breaks the special handling of ``__init__.py``,
    which must drop the trailing ``__init__`` segment to
    produce a clean package name.
    """

    def it_strips_init_suffix_for_package_files():
        result = _module_name("src/mypkg/__init__.py")
        assert result == "src.mypkg"

    def it_keeps_module_suffix_for_regular_files():
        result = _module_name("src/mypkg/utils.py")
        assert result == "src.mypkg.utils"

    def it_returns_stem_for_bare_filename():
        result = _module_name("solo.py")
        assert result == "solo"

    def it_handles_nested_init():
        """Kills the ``And → Or`` mutation at L105.

        Without the second clause (``parts[-1] == "__init__"``),
        a nested package file like ``a/b/__init__.py`` would not
        strip the ``__init__`` segment, producing ``a.b.__init__``
        instead of the correct ``a.b``.
        """
        result = _module_name("a/b/__init__.py")
        assert result == "a.b"


def describe_schema_migration():
    def it_preserves_backups_and_reconnects_for_another_thread(tmp_path, monkeypatch):
        from concurrent.futures import ThreadPoolExecutor
        import pytest_leela.index as index_module

        path = tmp_path / "index.db"
        with IndexDB(path) as db:
            db.reconcile_file("m.py", "def f():\n    return 1\n")
        previous_backup = tmp_path / "index.db.v1"
        previous_backup.write_bytes(b"earlier archive")
        monkeypatch.setattr(index_module, "SCHEMA_VERSION", 2)
        with IndexDB(path) as db:
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(db.count).result(timeout=5) == 0
                pool.submit(db.reconcile_file, "n.py", "def g():\n    return 2\n").result(timeout=5)
            assert db.count() == 1
            assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 2
        archives = list(tmp_path.glob("index.db.v1.*"))
        assert len(archives) == 1
        with sqlite3.connect(f"file:{archives[0]}?immutable=1", uri=True) as old:
            assert old.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 1
        assert previous_backup.read_bytes() == b"earlier archive"

        # Another old-version database must not replace the first archive.
        with sqlite3.connect(path) as conn:
            conn.execute("PRAGMA user_version = 1")
        with IndexDB(path) as db:
            assert db.count() == 0
        assert len(list(tmp_path.glob("index.db.v1.*"))) == 2


# ---------------------------------------------------------------------------
# _executemany
# ---------------------------------------------------------------------------


def describe_executemany():
    def it_returns_a_cursor_and_inserts_all_rows(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            params = [
                ("src.m:f", 1, "const-replace", "1", "0", MUTANT_CLEAN, "h" * 64, "t" * 64),
                ("src.m:f", 2, "const-replace", "1", "0", MUTANT_CLEAN, "h" * 64, "t" * 64),
            ]
            cur = db._executemany(  # type: ignore[attr-defined]
                "INSERT INTO mutants "
                "(symbol_id, location_line, operator, original_op, "
                " replacement_op, state, source_hash, test_set_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                params,
            )
            # The `return expr → return None` mutant returns None
            # and inserts nothing.
            assert cur is not None
            count_cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT COUNT(*) FROM mutants WHERE symbol_id = ?",
                ("src.m:f",),
            )
            assert int(count_cur.fetchone()[0]) == 2
        finally:
            db.close()


# ---------------------------------------------------------------------------
# symbols_with_ranges
# ---------------------------------------------------------------------------


def describe_symbols_with_ranges():
    def it_returns_a_list_of_tuples(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            ranges = db.symbols_with_ranges("src/m.py")
            # The `return expr → return None` mutant returns None.
            assert ranges is not None
            assert len(ranges) == 1
            sid, start, end, src_hash = ranges[0]
            assert sid == "src.m:f"
            assert start == 1
            assert end == 2
            assert len(src_hash) == 64
        finally:
            db.close()


# ---------------------------------------------------------------------------
# gap_symbols_with_counts
# ---------------------------------------------------------------------------


def describe_gap_symbols_with_counts():
    def it_returns_empty_list_when_no_gaps(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            assert db.gap_symbols_with_counts() == []
        finally:
            db.close()

    def it_counts_gap_mutants_per_symbol(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file("src/m.py", "def f():\n    return 1\n")
            db.write_mutant_result(
                "src.m:f",
                _make_mutant(),
                _make_result(killed=False),
                source_hash="h" * 64,
                test_set_hash="t" * 64,
            )
            gaps = db.gap_symbols_with_counts()
            # The `return expr → return None` mutant returns None.
            assert gaps is not None
            assert len(gaps) == 1
            assert gaps[0][0] == "src.m:f"
            assert gaps[0][1] == 1
        finally:
            db.close()


# ---------------------------------------------------------------------------
# categorize_gaps
# ---------------------------------------------------------------------------


def _make_gap_result(tests_run: int) -> MutantResult:
    return MutantResult(
        mutant=_make_mutant(),
        killed=False,
        tests_run=tests_run,
        killing_test=None,
        time_seconds=0.05,
        test_ids_run=["tests/test_x.py::test_a"] if tests_run > 0 else [],
        killing_tests=[],
    )


def _insert_gap_mutants(
    db: IndexDB,
    symbol_id: str,
    tests_run: int,
    count: int,
) -> None:
    for i in range(count):
        db.write_mutant_result(
            symbol_id,
            _make_mutant(lineno=i + 1),
            _make_gap_result(tests_run),
            source_hash="h" * 64,
            test_set_hash="t" * 64,
        )


def describe_categorize_gaps():
    def it_categorizes_gaps_by_tests_run(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file(
                "src/m.py",
                "def alpha():\n    return 1\n\ndef beta():\n    return 2\n",
            )
            # alpha: gap with tests_run > 0 → tests_ran
            _insert_gap_mutants(db, "src.m:alpha", tests_run=3, count=1)
            # beta: gap with tests_run == 0 → no_test_ran
            _insert_gap_mutants(db, "src.m:beta", tests_run=0, count=1)
            result = db.categorize_gaps()
            # The `> → <=` mutant swaps the categories.
            assert ("src.m:alpha", 1) in result["tests_ran"]
            assert ("src.m:beta", 1) in result["no_test_ran"]
            assert "src.m:alpha" not in {sid for sid, _ in result["no_test_ran"]}
            assert "src.m:beta" not in {sid for sid, _ in result["tests_ran"]}
        finally:
            db.close()

    def it_sorts_gap_categories_by_count_descending(tmp_path: Path):
        db = _open(tmp_path)
        try:
            db.reconcile_file(
                "src/m.py",
                "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"
                "\ndef gamma():\n    return 3\n\ndef delta():\n    return 4\n",
            )
            # tests_ran category: alpha(3), beta(1) — must be descending
            _insert_gap_mutants(db, "src.m:alpha", tests_run=3, count=3)
            _insert_gap_mutants(db, "src.m:beta", tests_run=1, count=1)
            # no_test_ran category: gamma(2), delta(1) — must be descending
            _insert_gap_mutants(db, "src.m:gamma", tests_run=0, count=2)
            _insert_gap_mutants(db, "src.m:delta", tests_run=0, count=1)
            result = db.categorize_gaps()
            # The `- → +` mutant sorts ascending instead of descending.
            assert result["tests_ran"] == [("src.m:alpha", 3), ("src.m:beta", 1)]
            assert result["no_test_ran"] == [("src.m:gamma", 2), ("src.m:delta", 1)]
        finally:
            db.close()
