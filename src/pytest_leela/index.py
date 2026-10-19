"""Persistent index for incremental mutation testing.

Caches per-symbol mutation results in a SQLite database so the leela Engine
can skip re-running tests for symbols whose source and covering test set
have not changed since the last analysis.

The index is a service: the leela Engine is a writer, the leela CLI is a
reader, and future consumers (LSP server, CI hook) are readers and writers.

Schema migrations archive the old database and start fresh.
The index is fully derivable from
the codebase, so a forced re-run is the cost of a schema change.
"""

import ast
import hashlib
import json
import os
import sqlite3
import threading
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Optional

if TYPE_CHECKING:
    from pytest_leela.models import Mutant, MutantResult


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Per-symbol states.
STATE_CLEAN: Final[str] = "clean"
STATE_DIRTY: Final[str] = "dirty"
STATE_ANALYZING: Final[str] = "analyzing"
STATE_STALE: Final[str] = "stale"
STATE_ERROR: Final[str] = "error"

SYMBOL_STATES: Final[frozenset[str]] = frozenset(
    {STATE_CLEAN, STATE_DIRTY, STATE_ANALYZING, STATE_STALE, STATE_ERROR}
)

# Per-mutant states.
MUTANT_CLEAN: Final[str] = "clean"
MUTANT_DIRTY: Final[str] = "dirty"
MUTANT_STALE: Final[str] = "stale"

MUTANT_STATES: Final[frozenset[str]] = frozenset(
    {MUTANT_CLEAN, MUTANT_DIRTY, MUTANT_STALE}
)

# Current schema version. Bumping this triggers a "delete and re-create"
# migration on next open.
SCHEMA_VERSION: Final[int] = 1


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Symbol:
    """A stable identifier for a code unit (function, method, or class)."""

    id: str
    file: str
    start_line: int
    end_line: int
    source_hash: str
    state: str = ""
    last_analyzed: str | None = None


@dataclass(frozen=True)
class ReconcileResult:
    """What changed in the index when a file was reconciled."""

    added: frozenset[str]
    removed: frozenset[str]
    changed: frozenset[str]

    @property
    def is_noop(self) -> bool:
        return not (self.added or self.removed or self.changed)


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------


def _module_name(file_path: str) -> str:
    """Best-effort dotted module name from a file path.

    Handles the ``__init__.py`` case (the module is the parent package)
    and the case where the file has no separators (a top-level script).
    """
    p = Path(file_path)
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else p.stem


def extract_symbols(file_path: str, source: str) -> dict[str, Symbol]:
    """Extract top-level symbols from a Python source file.

    v1 captures:

    * Top-level functions and async functions
    * Top-level classes, and their methods (one level deep)
    * Nested functions are not tracked

    Returns a dict mapping stable ID (``module:qualname``) to ``Symbol``.
    """
    tree = ast.parse(source)
    module = _module_name(file_path)
    symbols: dict[str, Symbol] = {}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _add_function(symbols, module, "", node, source, file_path)
        elif isinstance(node, ast.ClassDef):
            _add_class(symbols, module, node, source, file_path)

    return symbols


def _add_function(
    symbols: dict[str, Symbol],
    module: str,
    class_qualname: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source: str,
    file_path: str,
) -> None:
    qualname = f"{class_qualname}.{node.name}" if class_qualname else node.name
    sid = f"{module}:{qualname}"
    body_src = ast.get_source_segment(source, node) or ""
    symbols[sid] = Symbol(
        id=sid,
        file=file_path,
        start_line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        source_hash=hashlib.sha256(body_src.encode("utf-8")).hexdigest(),
    )


def _add_class(
    symbols: dict[str, Symbol],
    module: str,
    node: ast.ClassDef,
    source: str,
    file_path: str,
) -> None:
    sid = f"{module}:{node.name}"
    body_src = ast.get_source_segment(source, node) or ""
    symbols[sid] = Symbol(
        id=sid,
        file=file_path,
        start_line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        source_hash=hashlib.sha256(body_src.encode("utf-8")).hexdigest(),
    )
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _add_function(symbols, module, node.name, child, source, file_path)


# ---------------------------------------------------------------------------
# Index database
# ---------------------------------------------------------------------------


SCHEMA_SQL: Final[str] = """
CREATE TABLE symbols (
    id              TEXT PRIMARY KEY,
    file            TEXT NOT NULL,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    source_hash     TEXT NOT NULL,
    test_set_hash   TEXT,
    state           TEXT NOT NULL
        CHECK (state IN ('clean', 'dirty', 'analyzing', 'stale', 'error')),
    mutation_score  REAL,
    last_analyzed   TEXT,
    details         TEXT
);

CREATE INDEX idx_symbols_state ON symbols(state);
CREATE INDEX idx_symbols_file  ON symbols(file);

CREATE TABLE mutants (
    id              INTEGER PRIMARY KEY,
    symbol_id       TEXT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    location_line   INTEGER NOT NULL,
    operator        TEXT NOT NULL,
    original_op     TEXT,
    replacement_op  TEXT,
    state           TEXT NOT NULL
        CHECK (state IN ('clean', 'dirty', 'stale')),
    killed          INTEGER,
    killing_test    TEXT,
    killing_tests   TEXT,
    source_hash     TEXT NOT NULL,
    test_set_hash   TEXT NOT NULL,
    analyzed_at     TEXT,
    details         TEXT
);

CREATE INDEX idx_mutants_state        ON mutants(state);
CREATE INDEX idx_mutants_symbol_state ON mutants(symbol_id, state);
CREATE INDEX idx_mutants_symbol_time  ON mutants(symbol_id, analyzed_at);

CREATE TABLE symbol_tests (
    symbol_id       TEXT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    test_id         TEXT NOT NULL,
    PRIMARY KEY (symbol_id, test_id)
);

CREATE INDEX idx_symbol_tests_test ON symbol_tests(test_id);
"""


class IndexDB:
    """SQLite-backed index of per-symbol mutation state.

    The database is opened in WAL mode for concurrent readers, with
    foreign keys enabled so deleting a symbol cascades to its mutants
    and test mappings.

    The context-manager protocol is supported::

        with IndexDB(".leela/index.db") as db:
            db.reconcile_file("src/foo.py", source)
    """

    def __init__(self, path: str | Path) -> None:
        self.path: Final[Path] = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            check_same_thread=False,
        )
        # Serialise all DB access — the daemon's main thread
        # (writing to the index) and the reanalysis thread
        # (reading+writing during engine runs) race on this
        # connection, and ``BEGIN`` while another transaction
        # is in flight raises ``cannot start a transaction
        # within a transaction``. The lock also keeps the
        # connection itself sane under concurrent use.
        self._lock: threading.RLock = threading.RLock()
        self._apply_pragmas()
        self._setup_schema()

    def _apply_pragmas(self) -> None:
        self._execute("PRAGMA journal_mode = WAL")
        self._execute("PRAGMA synchronous = NORMAL")
        self._execute("PRAGMA foreign_keys = ON")
        self._execute("PRAGMA temp_store = MEMORY")

    def _setup_schema(self) -> None:
        row = self._execute("PRAGMA user_version").fetchone()
        assert row is not None
        version = int(row[0])

        if version == 0:
            # Fresh database — create the schema.
            self._executescript(SCHEMA_SQL)
            self._execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return

        if version > SCHEMA_VERSION:
            raise ValueError(
                f"Index version {version} is newer than supported "
                f"version {SCHEMA_VERSION}. Upgrade pytest-leela."
            )

        if version < SCHEMA_VERSION:
            # Reserve a unique archive name; never overwrite an earlier backup.
            self._conn.close()
            fd, backup = tempfile.mkstemp(
                prefix=f"{self.path.name}.v{version}.", dir=self.path.parent
            )
            os.close(fd)
            self.path.replace(backup)
            self._conn = sqlite3.connect(
                self.path, isolation_level=None, check_same_thread=False
            )
            self._apply_pragmas()
            self._executescript(SCHEMA_SQL)
            self._execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block of operations atomically."""
        with self._lock:
            self._execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._execute("ROLLBACK")
                raise
            else:
                self._execute("COMMIT")

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Execute a single statement under the connection lock.

        All non-transactional writes use this so the daemon's
        reanalysis thread can't race the main thread on the
        shared ``sqlite3.Connection``.
        """
        with self._lock:
            return self._conn.execute(sql, params)

    def _executemany(self, sql: str, params: list[tuple[Any, ...]]) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(sql, params)

    def _executescript(self, sql: str) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executescript(sql)

    def close(self) -> None:
        """Close the underlying connection. Idempotent."""
        self._conn.close()

    def __enter__(self) -> "IndexDB":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def reconcile_file(self, file_path: str, source: str) -> ReconcileResult:
        """Diff the file's current symbols against the index.

        Symbols that are new are inserted as ``dirty``. Symbols whose
        source has changed have their hash updated and are marked
        ``dirty`` (and their clean mutants are marked ``stale``).
        Symbols that no longer exist are deleted (with cascade).

        Returns a ``ReconcileResult`` describing what changed.
        """
        new_symbols = extract_symbols(file_path, source)
        new_ids = set(new_symbols)

        with self.transaction():
            old_hashes = self._symbols_in_file(file_path)
            old_ids = set(old_hashes)

            added = new_ids - old_ids
            removed = old_ids - new_ids
            existing = new_ids & old_ids
            changed: set[str] = set()

            for sid in removed:
                # CASCADE deletes mutants and symbol_tests.
                self._execute("DELETE FROM symbols WHERE id = ?", (sid,))

            for sid in added:
                sym = new_symbols[sid]
                self._execute(
                    "INSERT INTO symbols "
                    "(id, file, start_line, end_line, source_hash, state) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        sym.id,
                        sym.file,
                        sym.start_line,
                        sym.end_line,
                        sym.source_hash,
                        STATE_DIRTY,
                    ),
                )

            for sid in existing:
                old_sym = old_hashes[sid]
                new_sym = new_symbols[sid]
                # Always update start_line/end_line first: the
                # ``source_hash`` only fingerprints the function
                # body (not its position), so adding or removing a
                # comment above a function leaves the hash
                # unchanged but moves the function's line range.
                # The engine's per-mutant symbol lookup uses
                # ``start_line <= lineno <= end_line``, so a stale
                # range silently drops mutants on the floor and the
                # symbol stays dirty forever.
                self._execute(
                    "UPDATE symbols SET start_line = ?, end_line = ? WHERE id = ?",
                    (new_sym.start_line, new_sym.end_line, sid),
                )
                # Drop mutant rows whose ``location_line`` falls
                # outside the new range — those rows reference
                # code that is no longer part of this symbol and
                # cannot be re-killed. Without this, the engine
                # regenerates mutants against the current source
                # but the stale rows keep ``n_mutants > 0`` so the
                # placeholders loop never marks the symbol clean.
                self._execute(
                    "DELETE FROM mutants "
                    "WHERE symbol_id = ? AND "
                    "(location_line < ? OR location_line > ?)",
                    (sid, new_sym.start_line, new_sym.end_line),
                )
                if old_sym != new_sym.source_hash:
                    # The source changed (not just moved). Mark the
                    # symbol dirty and invalidate its cached
                    # mutants so the engine re-runs them.
                    self._execute(
                        "UPDATE symbols SET source_hash = ?, state = ? WHERE id = ?",
                        (new_sym.source_hash, STATE_DIRTY, sid),
                    )
                    self._execute(
                        "UPDATE mutants "
                        "SET state = ? "
                        "WHERE symbol_id = ? AND state = ?",
                        (MUTANT_STALE, sid, MUTANT_CLEAN),
                    )
                    changed.add(sid)

        return ReconcileResult(
            added=frozenset(added),
            removed=frozenset(removed),
            changed=frozenset(changed),
        )

    def _symbols_in_file(self, file_path: str) -> dict[str, str]:
        """Return ``{symbol_id: source_hash}`` for symbols in this file."""
        cur = self._execute(
            "SELECT id, source_hash FROM symbols WHERE file = ?", (file_path,)
        )
        return {str(row[0]): str(row[1]) for row in cur.fetchall()}

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        """Return the symbol with the given ID, or ``None``."""
        cur = self._execute(
            "SELECT id, file, start_line, end_line, source_hash, "
            "state, last_analyzed "
            "FROM symbols WHERE id = ?",
            (symbol_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return Symbol(
            id=str(row[0]),
            file=str(row[1]),
            start_line=int(row[2]),
            end_line=int(row[3]),
            source_hash=str(row[4]),
            state=str(row[5]),
            last_analyzed=str(row[6]) if row[6] is not None else None,
        )

    def get_state(self, symbol_id: str) -> str | None:
        """Return the state of a symbol, or ``None`` if it doesn't exist."""
        cur = self._execute("SELECT state FROM symbols WHERE id = ?", (symbol_id,))
        row = cur.fetchone()
        return str(row[0]) if row is not None else None

    def get_dirty_symbols(self, files: tuple[str, ...] | None = None) -> list[str]:
        """Return IDs of symbols that need (re-)analysis.

        If ``files`` is given, only symbols belonging to one of
        those file paths are returned. This lets the engine
        scope its placeholder-cleanup pass to the files it just
        reconciled, without disturbing symbols in files that are
        out of scope for this run.
        """
        if files:
            placeholders = ",".join("?" for _ in files)
            cur = self._execute(
                f"SELECT id FROM symbols "
                f"WHERE state IN (?, ?, ?) AND file IN ({placeholders}) "
                f"ORDER BY id",
                (STATE_DIRTY, STATE_STALE, STATE_ERROR, *files),
            )
        else:
            cur = self._execute(
                "SELECT id FROM symbols WHERE state IN (?, ?, ?) ORDER BY id",
                (STATE_DIRTY, STATE_STALE, STATE_ERROR),
            )
        return [str(row[0]) for row in cur.fetchall()]

    def all_symbols(self) -> list[Symbol]:
        """Return all symbols in the index."""
        cur = self._execute(
            "SELECT id, file, start_line, end_line, source_hash, "
            "state, last_analyzed "
            "FROM symbols ORDER BY id"
        )
        return [
            Symbol(
                id=str(r[0]),
                file=str(r[1]),
                start_line=int(r[2]),
                end_line=int(r[3]),
                source_hash=str(r[4]),
                state=str(r[5]),
                last_analyzed=str(r[6]) if r[6] is not None else None,
            )
            for r in cur.fetchall()
        ]

    def count(self) -> int:
        """Return the total number of symbols in the index."""
        cur = self._execute("SELECT COUNT(*) FROM symbols")
        row = cur.fetchone()
        assert row is not None
        return int(row[0])

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def mark_state(self, symbol_id: str, state: str) -> None:
        """Update a symbol's state. Raises ``ValueError`` for invalid state."""
        if state not in SYMBOL_STATES:
            raise ValueError(
                f"Invalid symbol state {state!r}; must be one of "
                f"{sorted(SYMBOL_STATES)}"
            )
        self._execute(
            "UPDATE symbols SET state = ? WHERE id = ?",
            (state, symbol_id),
        )

    def mark_symbols_dirty(self, symbol_ids: "set[str] | list[str]") -> int:
        """Mark a set of symbols as ``STATE_DIRTY``.

        No-op for an empty input. Only transitions symbols that are
        not already dirty / analyzing / error, so that an in-flight
        reanalyzer pass isn't disturbed.

        Returns the number of symbols actually transitioned to dirty.
        """
        if not symbol_ids:
            return 0
        placeholders = ",".join("?" for _ in symbol_ids)
        cur = self._execute(
            f"UPDATE symbols SET state = ? "
            f"WHERE id IN ({placeholders}) "
            f"AND state IN (?, ?)",
            (STATE_DIRTY, *symbol_ids, STATE_CLEAN, STATE_STALE),
        )
        return cur.rowcount

    def symbols_for_tests(self, test_ids: set[str]) -> set[str]:
        """Return the set of symbol IDs previously exercised by these tests.

        Reads the ``mutants.details`` JSON ``test_ids_run`` field and
        returns the ``symbol_id`` of every mutant that was run against
        at least one of the given test IDs. Used by the daemon when a
        test file is deleted: the deleted file's test IDs go in, and
        we get back the source symbols those tests had been covering.

        Returns an empty set if the given test IDs have never been
        recorded as running any mutant.
        """
        if not test_ids:
            return set()
        result: set[str] = set()
        # ``details`` is JSON of the form ``{"test_ids_run": [...], ...}``.
        # We pull every mutant with a non-empty test_ids_run and filter
        # in Python — there's no JSON index, but mutants.count is small
        # enough (one row per (symbol, line, operator) tuple) that this
        # is fine for the daemon's per-poll workload.
        cur = self._execute(
            "SELECT symbol_id, details FROM mutants WHERE details LIKE '%test_ids_run%'"
        )
        for row in cur.fetchall():
            details_raw = row[1] or ""
            try:
                details = json.loads(details_raw)
            except (TypeError, ValueError):
                continue
            run_ids = set(details.get("test_ids_run", []))
            if run_ids & test_ids:
                result.add(str(row[0]))
        return result

    # ------------------------------------------------------------------
    # Mutant cache consult
    # ------------------------------------------------------------------

    def has_clean_mutant(
        self,
        symbol_id: str,
        source_hash: str,
        test_set_hash: str,
    ) -> bool:
        """True if a clean mutant exists for this cache key.

        The cache key is the triple ``(symbol_id, source_hash, test_set_hash)``.
        A match means the cached result is still valid and the test run
        can be skipped.
        """
        cur = self._execute(
            "SELECT 1 FROM mutants "
            "WHERE symbol_id = ? AND source_hash = ? AND test_set_hash = ? "
            "AND state = ? LIMIT 1",
            (symbol_id, source_hash, test_set_hash, MUTANT_CLEAN),
        )
        return cur.fetchone() is not None

    def find_symbol_for(self, file_path: str, lineno: int) -> tuple[str, str] | None:
        """Find the innermost symbol containing this line.

        Returns ``(symbol_id, source_hash)`` for the most specific
        (smallest range) symbol whose ``[start_line, end_line]``
        contains ``lineno``, or ``None`` if no symbol contains it.

        Used by the engine to map a mutation point to the symbol it
        belongs to, which is the cache key for incremental mutation
        testing.
        """
        cur = self._execute(
            "SELECT id, source_hash FROM symbols "
            "WHERE file = ? AND start_line <= ? AND end_line >= ? "
            "ORDER BY (end_line - start_line) ASC LIMIT 1",
            (file_path, lineno, lineno),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return (str(row[0]), str(row[1]))

    def symbols_with_ranges(self, file_path: str) -> list[tuple[str, int, int, str]]:
        """Return ``[(symbol_id, start_line, end_line, source_hash)]``.

        Sorted by start line. Used by the engine to build an in-memory
        map of ``file → symbols`` so the mutation loop can resolve
        each mutant to its containing symbol without a per-mutant
        database roundtrip.
        """
        cur = self._execute(
            "SELECT id, start_line, end_line, source_hash "
            "FROM symbols WHERE file = ? ORDER BY start_line",
            (file_path,),
        )
        return [(str(r[0]), int(r[1]), int(r[2]), str(r[3])) for r in cur.fetchall()]

    def write_mutant_result(
        self,
        symbol_id: str,
        mutant: "Mutant",
        result: "MutantResult",
        source_hash: str,
        test_set_hash: str,
    ) -> None:
        """Persist a single mutant's analysis result.

        Inserts a new row in ``mutants`` (or replaces the existing one
        for this ``(symbol_id, location_line, operator, replacement_op)``
        tuple) with state ``clean``. The result fields are JSON-encoded
        where they are list-shaped (``killing_tests``, ``details``).

        The symbol's ``last_analyzed`` timestamp is updated, but its
        ``state`` is not — the engine flips it to ``clean`` explicitly
        when all of a symbol's mutants are written.
        """
        analyzed_at = _utc_now_iso()
        operator = f"{mutant.point.node_type}:{mutant.replacement_op}"
        killing_tests_json = json.dumps(result.killing_tests)
        details_json = json.dumps(
            {
                "tests_run": result.tests_run,
                "time_seconds": result.time_seconds,
                "test_ids_run": result.test_ids_run,
            }
        )

        with self.transaction():
            self._execute(
                "DELETE FROM mutants "
                "WHERE symbol_id = ? AND location_line = ? "
                "AND operator = ? AND replacement_op = ?",
                (
                    symbol_id,
                    mutant.point.lineno,
                    operator,
                    mutant.replacement_op,
                ),
            )
            self._execute(
                "INSERT INTO mutants ("
                " symbol_id, location_line, operator, original_op,"
                " replacement_op, state, killed, killing_test,"
                " killing_tests, source_hash, test_set_hash,"
                " analyzed_at, details"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    symbol_id,
                    mutant.point.lineno,
                    operator,
                    mutant.point.original_op,
                    mutant.replacement_op,
                    MUTANT_CLEAN,
                    1 if result.killed else 0,
                    result.killing_test,
                    killing_tests_json,
                    source_hash,
                    test_set_hash,
                    analyzed_at,
                    details_json,
                ),
            )
            self._execute(
                "UPDATE symbols SET last_analyzed = ? WHERE id = ?",
                (analyzed_at, symbol_id),
            )

    def get_cached_result(
        self,
        symbol_id: str,
        source_hash: str,
        test_set_hash: str,
        mutant_template: "Mutant",
    ) -> Optional["MutantResult"]:
        """Reconstruct a ``MutantResult`` from the cache.

        Returns ``None`` if no clean mutant matches the cache key. The
        ``mutant_template`` is used to fill in fields the cache does
        not store (module_name, col_offset, inferred_type, mutant_id)
        — the cache stores the parts that determine *which* mutant
        (location, operator, replacement_op) and the analysis result.
        """
        operator = f"{mutant_template.point.node_type}:{mutant_template.replacement_op}"
        cur = self._execute(
            "SELECT location_line, original_op, replacement_op, killed,"
            "       killing_test, killing_tests, details"
            " FROM mutants"
            " WHERE symbol_id = ? AND source_hash = ? AND test_set_hash = ?"
            " AND state = ?"
            " AND location_line = ?"
            " AND operator = ? AND replacement_op = ?"
            " LIMIT 1",
            (
                symbol_id,
                source_hash,
                test_set_hash,
                MUTANT_CLEAN,
                mutant_template.point.lineno,
                operator,
                mutant_template.replacement_op,
            ),
        )
        row = cur.fetchone()
        if row is None:
            return None

        # Local import: the models module imports from the index at
        # runtime via plugin code, and we want to avoid a circular
        # import at module load.
        from pytest_leela.models import Mutant, MutantResult, MutationPoint

        (
            location_line,
            original_op,
            replacement_op,
            killed,
            killing_test,
            killing_tests_json,
            details_json,
        ) = row
        killing_tests = json.loads(killing_tests_json) if killing_tests_json else []
        details = json.loads(details_json) if details_json else {}

        point = MutationPoint(
            file_path=mutant_template.point.file_path,
            module_name=mutant_template.point.module_name,
            lineno=int(location_line),
            col_offset=mutant_template.point.col_offset,
            node_type=mutant_template.point.node_type,
            original_op=str(original_op) if original_op is not None else "",
            inferred_type=mutant_template.point.inferred_type,
        )
        mutant = Mutant(
            point=point,
            replacement_op=str(replacement_op),
            mutant_id=mutant_template.mutant_id,
        )
        return MutantResult(
            mutant=mutant,
            killed=bool(killed),
            tests_run=int(details.get("tests_run", 0)),
            killing_test=str(killing_test) if killing_test is not None else None,
            time_seconds=float(details.get("time_seconds", 0.0)),
            test_ids_run=list(details.get("test_ids_run", [])),
            killing_tests=killing_tests,
        )

    def mark_symbol_clean(self, symbol_id: str) -> None:
        """Mark a symbol as fully analyzed (state=clean).

        Intended to be called by the engine after all mutants for a
        symbol have been written. Idempotent.
        """
        self.mark_state(symbol_id, STATE_CLEAN)

    def coverage_gap_symbol_count(self) -> int:
        """Count distinct symbols with at least one coverage gap.

        A coverage gap is a mutant that the engine wrote as
        ``clean`` whose ``killed`` flag is 0 — either the test
        suite ran but didn't catch the mutation (weak coverage,
        ``tests_run > 0``) or no test exercised the code at all
        (uncovered, ``tests_run == 0``). This is the work-to-do
        count that observer mode surfaces to the operator.

        Returns 0 when every clean mutant in the index is
        killed.
        """
        cur = self._execute(
            "SELECT COUNT(DISTINCT m.symbol_id) FROM mutants m "
            "WHERE m.state = ? AND m.killed = 0",
            (MUTANT_CLEAN,),
        )
        return int(cur.fetchone()[0])

    def gap_symbols_with_counts(self) -> list[tuple[str, int]]:
        """Return ``[(symbol_id, n_uncovered_mutants), ...]``.

        Used by the ``status`` subcommand to list the symbols
        with surviving or uncovered mutants, alongside how many
        such mutants each one has — so the operator can sort
        by ``n_uncovered_mutants`` and tackle the worst first.

        Ordered by count descending, then by symbol id for
        stable output. Returns an empty list when nothing
        has gaps.
        """
        cur = self._execute(
            "SELECT m.symbol_id, COUNT(*) "
            "FROM mutants m "
            "WHERE m.state = ? AND m.killed = 0 "
            "GROUP BY m.symbol_id "
            "ORDER BY COUNT(*) DESC, m.symbol_id ASC",
            (MUTANT_CLEAN,),
        )
        return [(str(sid), int(n)) for sid, n in cur.fetchall()]

    def gap_symbols_with_files(self) -> list[tuple[str, str, int]]:
        """Return ``[(symbol_id, file_path, n_uncovered_mutants), ...]``.

        Like :meth:`gap_symbols_with_counts` but joins the
        symbol's source file so the operator knows which file
        to open when filling gaps. Used by the ``status``
        subcommand's ``--by-file`` grouping.

        Ordered by count descending, then by symbol id for
        stable output.
        """
        cur = self._execute(
            "SELECT m.symbol_id, s.file, COUNT(*) "
            "FROM mutants m JOIN symbols s ON s.id = m.symbol_id "
            "WHERE m.state = ? AND m.killed = 0 "
            "GROUP BY m.symbol_id "
            "ORDER BY COUNT(*) DESC, m.symbol_id ASC",
            (MUTANT_CLEAN,),
        )
        return [(str(sid), str(f), int(n)) for sid, f, n in cur.fetchall()]

    def categorize_gaps(self) -> dict[str, list[tuple[str, int]]]:
        """Split gap-having symbols into two categories.

        Returns a dict with two keys:

        * ``"no_test_ran"`` — symbols whose gap mutants all
          had ``tests_run == 0``. The work to do is purely
          coverage: write a test that exercises the line.
        * ``"tests_ran"`` — symbols whose gap mutants had at
          least one with ``tests_run > 0`` (the test ran but
          didn't catch the mutation). The work is to write
          a stronger assertion that distinguishes the
          mutation from the original.

        Each entry is ``[(symbol_id, n_gap_mutants), ...]``
        for that category. Ordered by count descending.
        """
        cur = self._execute(
            "SELECT m.symbol_id, m.location_line, m.details, "
            "       MAX(CASE WHEN m.killed = 0 THEN 0 ELSE 1 END) "
            "FROM mutants m "
            "WHERE m.state = ? AND m.killed = 0 "
            "GROUP BY m.symbol_id",
            (MUTANT_CLEAN,),
        )
        import json as _json

        no_test_ran: list[tuple[str, int]] = []
        tests_ran: list[tuple[str, int]] = []
        for sid, _lineno, _details, _ in cur.fetchall():
            # Get count and check if any has tests_run > 0
            cur2 = self._execute(
                "SELECT details FROM mutants "
                "WHERE symbol_id = ? AND state = ? AND killed = 0",
                (sid, MUTANT_CLEAN),
            )
            any_tests_ran = False
            total = 0
            for (details_json,) in cur2.fetchall():
                total += 1
                try:
                    d = _json.loads(details_json or "{}")
                    if int(d.get("tests_run", 0)) > 0:
                        any_tests_ran = True
                except (ValueError, TypeError):
                    pass
            target = tests_ran if any_tests_ran else no_test_ran
            target.append((str(sid), total))
        no_test_ran.sort(key=lambda x: -x[1])
        tests_ran.sort(key=lambda x: -x[1])
        return {
            "no_test_ran": no_test_ran,
            "tests_ran": tests_ran,
        }

    def gap_mutants_for_symbol(self, symbol_id: str) -> list[dict[str, Any]]:
        """Return each uncovered mutant's location_line + status.

        For a given symbol id, return a list of dicts, one per
        uncovered mutant:

        ``{"file": str, "lineno": int, "operator": str,
        "original_op": str, "replacement_op": str,
        "tests_run": int, "killing_test": str | None}``

        ``tests_run == 0`` means no test covered the mutated
        line at all (the work to do is "write a test that
        reaches this line"). ``tests_run > 0`` with
        ``killing_test is None`` means tests ran but didn't
        catch the mutation (the work is "write a stronger
        assertion that distinguishes the mutation").

        Ordered by lineno ascending.
        """
        cur = self._execute(
            "SELECT s.file, m.location_line, m.operator,"
            " m.original_op, m.replacement_op,"
            " m.details, m.killing_test "
            "FROM mutants m JOIN symbols s ON s.id = m.symbol_id "
            "WHERE m.symbol_id = ? AND m.state = ? AND m.killed = 0 "
            "ORDER BY m.location_line ASC",
            (symbol_id, MUTANT_CLEAN),
        )
        import json as _json

        out: list[dict[str, Any]] = []
        for file_path, lineno, op, orig, repl, details_json, killing in cur.fetchall():
            tests_run = 0
            try:
                details = _json.loads(details_json or "{}")
                tests_run = int(details.get("tests_run", 0))
            except (ValueError, TypeError):
                pass
            out.append(
                {
                    "file": str(file_path),
                    "lineno": int(lineno),
                    "operator": str(op),
                    "original_op": str(orig),
                    "replacement_op": str(repl),
                    "tests_run": tests_run,
                    "killing_test": (str(killing) if killing else None),
                }
            )
        return out

    def mark_symbol_error(self, symbol_id: str) -> None:
        """Mark a symbol as failed (state=error)."""
        self.mark_state(symbol_id, STATE_ERROR)


# ---------------------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------------------


def compute_test_set_hash(test_ids: list[str] | None) -> str:
    """Hash the set of test IDs that ran against a mutant.

    The result is the SHA-256 of the newline-joined, sorted test IDs.
    An empty / None test set hashes to the SHA-256 of the empty
    string, which is a stable sentinel meaning "no test set".
    """
    if not test_ids:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256("\n".join(sorted(test_ids)).encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
