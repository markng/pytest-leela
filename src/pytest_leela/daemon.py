"""Filesystem daemon that watches a project, reports changes to the
leela index, and re-runs mutation analysis on dirty symbols.

The daemon runs as a long-lived process. It is intentionally
polling-based (no external ``watchdog`` dependency) and uses
whole-file content signatures to detect file changes. The poll interval is
configurable.

Typical usage::

    from pathlib import Path
    from pytest_leela.daemon import LeelaDaemon

    daemon = LeelaDaemon(Path("."))
    daemon.run()  # blocks until interrupted

Or via the CLI::

    python -m pytest_leela.daemon watch

The daemon prints a single line per change, plus a periodic status
line. Re-analysis happens in a background thread so the watcher
can schedule the next pass after the current pass releases input ownership.
"""

# Without postponed annotations, Python <=3.13 evaluates annotations eagerly.
# Python 3.14 defers evaluation; annotation probes must explicitly resolve hints.

import argparse
import hashlib
import json
import os
import sys as _sys
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Final

from pytest_leela.engine import Engine
from pytest_leela.index import (
    IndexDB,
    STATE_ANALYZING,
    STATE_CLEAN,
    STATE_DIRTY,
    STATE_ERROR,
    STATE_STALE,
    ReconcileResult,
)
from pytest_leela.models import EngineProgress


# Default poll interval, in seconds.
DEFAULT_POLL_INTERVAL: float = 1.0

# Project directories that are never watched.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        ".tox",
        "build",
        "dist",
        ".git",
        ".hg",
        "__pycache__",
        "node_modules",
        ".eggs",
        "target",  # leela's own test target dir, not source
        "tests",  # convention: tests live here, source lives elsewhere
        ".leela",  # the index itself
    }
)


def _default_engine_factory(
    db: IndexDB,
    on_progress: Callable[[EngineProgress], None] | None = None,
) -> Engine:
    """Module-level factory used when ``LeelaDaemon`` has no custom factory.

    A lambda cannot carry a return annotation, so mypy cannot infer
    its return type and rejects ``Callable[...] | None`` consumers of
    the result. Hoisting the body to a named function gives mypy the
    type it needs.
    """
    return Engine(index=db, on_progress=on_progress)


@dataclass(frozen=True)
class ChangeEvent:
    """A single change event reported by the daemon."""

    file_path: str
    kind: str  # "modified", "created", "deleted"
    reconcile: ReconcileResult | None = None
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def render(self) -> str:
        ts = self.at.strftime("%H:%M:%S")
        if self.reconcile is None:
            return f"  [{ts}] {self.kind:9s} {self.file_path}"
        a = len(self.reconcile.added)
        r = len(self.reconcile.removed)
        c = len(self.reconcile.changed)
        return f"  [{ts}] {self.kind:9s} {self.file_path}  (+{a} ~{c} -{r})"


@dataclass
class DaemonStatus:
    """Snapshot of the daemon's state, suitable for printing.

    The DB stores four symbol states (``clean``, ``dirty``,
    ``stale``, ``error``) where ``clean`` simply means
    "analysis is complete". For the operator's view, that
    single boolean is split into two semantic buckets:

    * ``covered`` — ``clean`` AND no surviving / uncovered
      mutants. Fully tested. Zero work.
    * ``with_gaps`` — ``clean`` AND has at least one
      surviving or uncovered mutant. Work to do: add tests.

    The internal ``clean`` / ``dirty`` / ``stale`` / ``error``
    fields are kept for callers that need the raw DB state,
    but ``render()`` shows the three buckets the operator
    actually cares about (``covered`` / ``with_gaps`` /
    ``pending_reanalysis``), so the status line tells them
    how much work is outstanding without requiring a
    glossary.
    """

    total_symbols: int
    clean: int
    dirty: int
    stale: int
    error: int
    pending_reanalysis: int
    with_gaps: int = 0
    covered: int = 0

    @classmethod
    def from_db(cls, db: IndexDB) -> "DaemonStatus":
        cur = db._execute("SELECT state, COUNT(*) FROM symbols GROUP BY state")
        counts = {str(r[0]): int(r[1]) for r in cur.fetchall()}
        clean = counts.get(STATE_CLEAN, 0)
        dirty = counts.get(STATE_DIRTY, 0)
        stale = counts.get(STATE_STALE, 0)
        error = counts.get(STATE_ERROR, 0)
        analyzing = counts.get(STATE_ANALYZING, 0)
        with_gaps = db.coverage_gap_symbol_count()
        # ``clean`` in the DB means "analysis done", not
        # "fully tested". Subtract the ones with surviving or
        # uncovered mutants to get the truly covered count.
        covered = max(0, clean - with_gaps)
        return cls(
            total_symbols=clean + dirty + stale + error + analyzing,
            clean=clean,
            dirty=dirty,
            stale=stale,
            error=error,
            pending_reanalysis=dirty + stale + error + analyzing,
            with_gaps=with_gaps,
            covered=covered,
        )

    def render(self) -> str:
        return (
            f"  status: {self.total_symbols} symbols  \u2014 "
            f"{self.covered} covered, "
            f"{self.with_gaps} with gaps, "
            f"{self.pending_reanalysis} pending re-analysis"
        )


class LeelaDaemon:
    """Watch a project, update the index, re-analyze on changes.

    The daemon walks the project once at startup to build the index
    (inserting every symbol as ``dirty``). It then polls the file
    system at ``poll_interval`` seconds; any ``.py`` file whose
    content has changed is reconciled into the index.

    A re-analysis pass runs in a background thread whenever there
    are pending dirty symbols and the previous pass has settled
    (debounce). Re-analysis uses the leela Engine against the
    project's test suite.
    """

    def __init__(
        self,
        project_root: str | Path,
        index_path: str | Path | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        reanalyze_debounce: float = 2.0,
        engine_factory: (
            Callable[[IndexDB, Callable[[EngineProgress], None] | None], Engine] | None
        ) = None,
        test_dir: str | Path | None = None,
        target_dirs: tuple[str, ...] = ("src",),
        out: IO[str] | None = None,
        verbose: bool = False,
        observer: bool = False,
    ) -> None:
        self.project_root: Final[Path] = Path(project_root).resolve()
        self.index_path: Final[Path] = (
            Path(index_path)
            if index_path is not None
            else self.project_root / ".leela" / "index.db"
        )
        self.poll_interval: Final[float] = poll_interval
        self.reanalyze_debounce: Final[float] = reanalyze_debounce
        self.engine_factory: Final[
            Callable[[IndexDB, Callable[[EngineProgress], None] | None], Engine]
        ] = engine_factory or _default_engine_factory
        self.test_dir: Final[Path | None] = (
            Path(test_dir).resolve() if test_dir is not None else None
        )
        self.target_dirs: Final[tuple[str, ...]] = target_dirs
        # Resolve the default output at construction, not at module import:
        # callers (e.g. pytest capture) routinely replace and close
        # ``sys.stdout`` between import and construction, so an import-time
        # snapshot could retain a closed stream.
        self.out: Final[IO[str]] = out if out is not None else _sys.stdout
        # ``verbose`` switches on the per-symbol / per-mutant
        # progress log. Default is quiet: just change events,
        # ``[analyze] re-analyzing N dirty symbol(s)`` and
        # ``[analyze] done`` lines. ``--verbose`` adds the
        # per-symbol headers, per-mutant outcomes, and
        # per-symbol summaries on top of that.
        self.verbose: Final[bool] = verbose
        # ``observer`` switches to a "work to do" mode. The log
        # only shows:
        #   * baseline test failures (tests that don't pass on
        #     the unmutated code),
        #   * surviving mutants (code with weak coverage —
        #     tests exist but don't catch the mutation),
        #   * uncovered code (mutants where no test ran at all,
        #     surfaced as ``status="error"`` by the engine).
        # Killed mutants are good (a test caught the mutation)
        # and are NOT printed in observer mode — they are not
        # "work to do". Cache-hit mutants and non-dirty symbols
        # are skipped for the same reasons as in ``verbose``
        # mode. ``observer`` and ``verbose`` are mutually
        # exclusive in spirit; if both are set, ``observer``
        # wins (its filter is strictly quieter).
        self.observer: Final[bool] = observer

        self._source_signatures: dict[str, str] = {}
        self._test_signatures: dict[str, str] = {}
        self._contract_signatures: dict[str, str] = {}
        self._contract_paths = {
            self.project_root / name for name in (
                "pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml",
                "pyproject.toml", "tox.ini", "setup.cfg", "conftest.py",
            )
        }
        self._collected_test_files: set[str] = set()
        # One participating daemon writer owns this index. This private seam
        # can later be replaced by writer coordination; it is not fencing for
        # arbitrary indexed Engine/IndexDB callers or other processes. SQLite
        # transactions stay short: application ownership always precedes DB locks.
        self._application_lock = threading.RLock()
        self._application_owned = False
        self._scan_pending = False
        self._stop = threading.Event()
        self._reanalyzer_thread: threading.Thread | None = None
        self._last_change_at: float | None = None
        self._last_reanalysis_at: float | None = None
        self._reanalyzing = False
        self._test_dir: Path | None = None

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _print(self, msg: str) -> None:
        self.out.write(msg + "\n")
        self.out.flush()

    def _emit_symbol_summary(
        self,
        symbol_id: str,
        tally: dict[str, dict[str, int]],
    ) -> None:
        """Print one summary line for a symbol that just finished.

        Format: ``[analyze]   <short> — N killed, M survived, ...``.
        Compact but enough for the reader to spot symbols with
        many survivors (i.e. real coverage gaps) at a glance.
        Cache-hit counts are intentionally not included: the
        log is filtered upstream to drop cache-hit mutants, so
        the tally only ever contains killed / survived / error.
        """
        counts = tally.get(symbol_id, {})
        if not counts:
            return
        total = sum(counts.values())
        parts = []
        for label in ("killed", "survived", "error"):
            n = counts.get(label, 0)
            if n:
                parts.append(f"{n} {label}")
        if not parts:
            return
        self._print(
            f"[analyze]   {symbol_id} \u2014 {total} mutants ({', '.join(parts)})"
        )

    def _print_mutant(self, ev: EngineProgress) -> None:
        """Print one line for a single mutant outcome.

        The symbol id is printed in full (module path +
        symbol name). Truncating to the trailing 16 chars
        threw away the module path, which is the part that
        disambiguates two symbols that happen to share a
        short name across modules — e.g.
        ``runner:_trace`` vs ``plugin:_trace``. Operators
        asked for the full name; lines wrap if the terminal
        is narrow, which is fine.
        """
        name = ev.symbol_id or "?"
        op = ev.op or "?"
        if ev.status == "killed":
            tail = ""
            if ev.killing_test:
                # Test node ids are long (``tests/foo/bar.py::test_x``).
                # Show only the trailing ``test_x`` for brevity.
                tail = f" by {ev.killing_test.rsplit('::', 1)[-1]}"
            self._print(f"[analyze]     {name}.L{ev.lineno} {op} killed{tail}")
        elif ev.status == "survived":
            self._print(f"[analyze]     {name}.L{ev.lineno} {op} SURVIVED")
        elif ev.status == "cache-hit":
            self._print(f"[analyze]     {name}.L{ev.lineno} {op} cache-hit")
        elif ev.status == "error":
            self._print(f"[analyze]     {name}.L{ev.lineno} {op} ERROR")

    # ------------------------------------------------------------------
    # Initial build
    # ------------------------------------------------------------------

    @staticmethod
    def _signature(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _in_source_scope(self, path: Path) -> bool:
        return any(path.is_relative_to((self.project_root / d).resolve())
                   for d in self.target_dirs) and not self._should_skip(path)

    def _scope_symbol_ids(self, db: IndexDB) -> set[str]:
        return {s.id for s in db.all_symbols() if self._in_source_scope(Path(s.file))}

    def _dirty_scope(self, db: IndexDB) -> None:
        # Whole-file/dependency changes cannot be attributed by function hashes.
        # Keep unrelated persisted scopes intact; actual Engine baseline follows.
        db.mark_symbols_dirty(self._scope_symbol_ids(db))
        self._last_change_at = time.monotonic()

    def _collect_test_files(self) -> set[str] | None:
        """Ask normal pytest collection, never guess selectable filename patterns.

        Like the existing coverage subprocess, retain project plugins/config but
        clear recursive mutation addopts. Only collect on startup or real changes.
        """
        test_dir = self._resolve_test_dir()
        if test_dir is None:
            return set()
        script = """
import json, pathlib, sys, pytest
class Collected:
    def pytest_collection_finish(self, session):
        pathlib.Path(sys.argv[1]).write_text(json.dumps({
            "files": sorted({str(item.path.resolve()) for item in session.items}),
            "config": str(session.config.inipath) if session.config.inipath else None,
        }))
status = pytest.main(["--override-ini=addopts=", "-p", "no:leela",
                     "--collect-only", "-q", sys.argv[2]], plugins=[Collected()])
sys.exit(0 if status in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED) else int(status))
"""
        with tempfile.TemporaryDirectory(prefix="leela-collection-") as tmp:
            output = Path(tmp) / "collected.json"
            try:
                subprocess.run(
                    [_sys.executable, "-c", script, str(output), str(test_dir)],
                    cwd=self.project_root, capture_output=True, text=True,
                    check=True, timeout=120,
                )
                payload = json.loads(output.read_text())
            except (OSError, subprocess.SubprocessError) as exc:
                self._print(f"[coverage] scoped collection failed: {exc!r}")
                return None
        selected_config = payload["config"]
        if selected_config is not None:
            config = Path(selected_config).resolve()
            if (config.parent == self.project_root or self._in_source_scope(config)
                    or config.is_relative_to(test_dir)):
                self._contract_paths.add(config)
        files = set(payload["files"])
        self._collected_test_files.update(files)
        return files

    def _initial_build(self, db: IndexDB) -> None:
        # Only actually absent, configured in-scope persisted files are deletions.
        # A narrower new target selection must not erase existing/foreign history.
        for file_path in {s.file for s in db.all_symbols()}:
            path = Path(file_path)
            if self._in_source_scope(path) and not path.exists():
                self._reconcile_deletion(db, file_path)
        n_files = 0
        for py in self._iter_source_files():
            abs_path = str(py)
            try:
                content = py.read_bytes()
            except OSError:
                continue
            db.reconcile_file(abs_path, content.decode())
            self._source_signatures[abs_path] = self._signature(content)
            n_files += 1
        for py in self._iter_test_files():
            try:
                self._test_signatures[str(py)] = self._signature(py.read_bytes())
            except OSError:
                continue
        if self._collect_test_files() is None:
            self._dirty_scope(db)
        for path in self._contract_paths:
            if path.is_file():
                self._contract_signatures[str(path)] = self._signature(path.read_bytes())
        self._print(f"[init] reconciled {n_files} files; {db.count()} symbols in index")
        # Treat the initial build as a "change" so the reanalyzer
        # fires on the first poll iteration. Without this, a fresh
        # start would reconcile every symbol as dirty but never
        # actually analyze them — the status line would say
        # "pending re-analysis: N" forever. We set the timestamp
        # to *before* the debounce window so the very first
        # ``_maybe_spawn_reanalysis`` call passes the debounce
        # check.
        self._last_change_at = time.monotonic() - self.reanalyze_debounce - 1.0

    def _iter_source_files(self) -> Iterator[Path]:
        for d in self.target_dirs:
            base = self.project_root / d
            if not base.is_dir():
                continue
            for py in base.rglob("*.py"):
                if not self._should_skip(py):
                    yield py

    def _should_skip(self, path: Path) -> bool:
        return any(part in _SKIP_DIRS for part in path.relative_to(self.project_root).parts)

    def _iter_test_files(self) -> Iterator[Path]:
        """Yield every ``*.py`` file under the resolved test dir.

        Bypasses the ``_SKIP_DIRS`` filter (which excludes ``tests/``
        from source discovery) — we want every test file so we can
        detect new/modified/deleted tests and trigger targeted
        re-analysis.
        """
        test_dir = self._resolve_test_dir()
        if test_dir is None or not test_dir.is_dir():
            return
        for py in test_dir.rglob("*.py"):
            yield py

    # ------------------------------------------------------------------
    # Polling and reconciliation
    # ------------------------------------------------------------------

    def _iter_all_py(self) -> Iterator[Path]:
        # Walk only the directories that are interesting: the
        # configured target_dirs and (if known) the test_dir. This
        # avoids descending into ``.git/`` or ``.venv/`` — which
        # can contain tens of thousands of files and would dominate
        # the poll cost.
        roots: list[Path] = []
        for d in self.target_dirs:
            p = self.project_root / d
            if p.is_dir():
                roots.append(p)
        test_dir = self._resolve_test_dir()
        if test_dir is not None and test_dir not in roots:
            roots.append(test_dir)
        for root in roots:
            for py in root.rglob("*.py"):
                if not self._should_skip(py):
                    yield py

    @contextmanager
    def _application_pass(self, db: IndexDB) -> Iterator[None]:
        """Serialize complete passes/scans, not filesystem edits or DB readers.

        Reentrancy is only for callback requests: _scan_for_changes defers
        rather than reconciling inside an active pass. Drain under the same
        ownership, clearing before each scan so requests during it survive.
        """
        with self._application_lock:
            self._application_owned = True
            try:
                yield
            finally:
                try:
                    while self._scan_pending:
                        self._scan_pending = False
                        try:
                            events = self._scan_owned(db)
                        except BaseException:
                            self._scan_pending = True
                            raise
                        for event in events:
                            self._print(event.render())
                finally:
                    self._application_owned = False

    def _scan_for_changes(self, db: IndexDB) -> list[ChangeEvent]:
        # Take ownership BEFORE walking/reading files or consuming signatures.
        # Other threads wait; same-thread callbacks request one whole later scan.
        with self._application_lock:
            if self._application_owned:
                self._scan_pending = True
                return []
            with self._application_pass(db):
                self._scan_pending = False
                return self._scan_owned(db)

    def _scan_owned(self, db: IndexDB) -> list[ChangeEvent]:
        """Poll configured roots by content, independent of timestamp direction."""
        events: list[ChangeEvent] = []
        seen: set[str] = set()
        for py in self._iter_source_files():
            abs_path = str(py)
            seen.add(abs_path)
            try:
                content = py.read_bytes()
            except OSError:
                continue
            signature = self._signature(content)
            prev = self._source_signatures.get(abs_path)
            if signature != prev:
                rec = db.reconcile_file(abs_path, content.decode())
                # Persist whole-input invalidation before consuming a signature:
                # a later file can fail without changing this function's hash.
                self._dirty_scope(db)
                self._source_signatures[abs_path] = signature
                events.append(ChangeEvent(abs_path, "created" if prev is None else "modified", rec))
        for old_path in list(self._source_signatures):
            if old_path not in seen:
                rec = self._reconcile_deletion(db, old_path)
                self._dirty_scope(db)
                self._source_signatures.pop(old_path)
                events.append(ChangeEvent(old_path, "deleted", rec))

        for path in self._contract_paths:
            abs_path = str(path)
            try:
                contract_signature: str | None = self._signature(path.read_bytes())
            except FileNotFoundError:
                contract_signature = None
            prev = self._contract_signatures.get(abs_path)
            if contract_signature != prev:
                if contract_signature is None:
                    self._contract_signatures.pop(abs_path)
                else:
                    self._contract_signatures[abs_path] = contract_signature
                kind = "deleted" if contract_signature is None else "created" if prev is None else "modified"
                events.append(ChangeEvent(abs_path, kind))
                self._dirty_scope(db)

        added, modified, deleted = self._scan_test_files()
        self._handle_test_file_changes(db, added, modified, deleted)
        return events

    def _is_test_file(self, abs_path: str) -> bool:
        """Return True if ``abs_path`` lives under the resolved test dir."""
        test_dir = self._resolve_test_dir()
        if test_dir is None:
            return False
        try:
            return Path(abs_path).resolve().is_relative_to(test_dir.resolve())
        except (ValueError, OSError):
            return False

    def _scan_test_files(
        self,
    ) -> tuple[list[str], list[str], list[str]]:
        """One pass of the test file tree.

        Returns three lists: ``(added, modified, deleted)`` test
        file absolute paths, in the order they were discovered. The
        daemon's main ``_scan_for_changes`` skips the test dir (it
        lives under the source-discovery skip list), so this is the
        only place we look at tests.
        """
        added: list[str] = []
        modified: list[str] = []
        seen: set[str] = set()
        for py in self._iter_test_files():
            abs_path = str(py)
            seen.add(abs_path)
            try:
                signature = self._signature(py.read_bytes())
            except OSError:
                continue
            prev = self._test_signatures.get(abs_path)
            if prev is None:
                self._test_signatures[abs_path] = signature
                added.append(abs_path)
            elif signature != prev:
                self._test_signatures[abs_path] = signature
                modified.append(abs_path)
        deleted: list[str] = []
        for old_path in list(self._test_signatures):
            if old_path not in seen:
                self._test_signatures.pop(old_path, None)
                deleted.append(old_path)
        return added, modified, deleted

    def _handle_test_file_changes(
        self,
        db: IndexDB,
        added: list[str],
        modified: list[str],
        deleted: list[str],
    ) -> None:
        """Mark source symbols dirty based on changed test files.

        New coverage supplies consumers for added/modified files. Retained
        mutant history supplies prior observed consumers for modified/deleted
        files, including functions no longer covered by the changed tests.
        Observed test IDs are conservative invalidation inputs, not proven
        line-coverage relationships.
        """
        if not (added or modified or deleted):
            return
        prior = self._collected_test_files.copy()
        # History survives restarts and includes tests no longer collectable.
        for row in db._execute(
            "SELECT details FROM mutants WHERE details LIKE '%test_ids_run%'"
        ).fetchall():
            try:
                details = json.loads(row[0])
            except (TypeError, ValueError):
                continue
            prior.update(str((self.project_root / tid.split("::", 1)[0]).resolve())
                         for tid in details.get("test_ids_run", []))
        collected = self._collect_test_files()
        if collected is None or set(added + modified + deleted) - (prior | collected):
            # Helpers/fixtures/configuration and unknown collection boundaries
            # are dependencies, not standalone pytest inputs with zero tests.
            self._dirty_scope(db)
            return
        dirty_ids: set[str] = set()

        # Added / modified: collect coverage for the changed files.
        scoped = sorted(set(added + modified))
        if scoped:
            dirty_ids |= self._dirty_symbols_from_scoped_tests(db, scoped)

        # Modified/deleted: retain consumers that new coverage cannot reveal.
        for changed_file in set(modified + deleted):
            dirty_ids |= self._dirty_symbols_from_deleted_test_file(db, changed_file)

        dirty_ids &= self._scope_symbol_ids(db)
        if not dirty_ids:
            return

        # A corrected test must retry an already-pending failed analysis too.
        self._last_change_at = time.monotonic()
        count = db.mark_symbols_dirty(dirty_ids)
        if count > 0:
            self._print(f"[coverage] test-file change dirtied {count} source symbol(s)")

    def _dirty_symbols_from_scoped_tests(
        self, db: IndexDB, test_files: list[str]
    ) -> set[str]:
        """Run pytest scoped to ``test_files`` and return the symbols
        the new/modified tests actually exercise.

        Runs pytest in a *fresh subprocess* with the coverage
        plugin, scoped to the given test files. Returns the set
        of symbol IDs whose lines were hit by any of those tests.
        The caller marks them dirty.

        Why subprocess? When the daemon is itself invoked from
        inside an existing pytest run (e.g. its own tests), an
        in-process ``pytest.main`` inherits the outer pytest's
        rootdir and config — ``pythonpath``, ``conftest.py``,
        ``pyproject.toml`` all resolve against the wrong project,
        and ``from calc import sub`` style imports fail to collect
        silently. A subprocess starts with a clean interpreter
        so ``cwd`` is the project's actual rootdir.
        """
        from pytest_leela.coverage_tracker import (
            collect_coverage_subprocess,
        )

        target_files = [str(p) for p in self._iter_source_files()]
        if not target_files:
            return set()

        try:
            cov = collect_coverage_subprocess(
                target_files=target_files,
                test_node_ids=test_files,
                cwd=str(self.project_root),
            )
        except Exception as exc:  # noqa: BLE001
            self._print(
                f"[coverage] scoped collection failed for "
                f"{len(test_files)} file(s): {exc!r}"
            )
            return self._scope_symbol_ids(db)

        if not cov.line_to_tests:
            # Tests ran but touched no source lines — TDD test-first
            # case, or tests that only exercise stdlib / external
            # libs. Nothing to dirty, but worth surfacing so the
            # operator isn't confused about why nothing happened.
            self._print(
                f"[coverage] {len(test_files)} test file(s) ran "
                f"but covered 0 source lines (TDD test-first?)"
            )
            return set()

        dirty: set[str] = set()
        for file_path, lineno in cov.line_to_tests:
            # ``line_to_tests`` carries the real path Python saw at
            # trace time (``/private/var/folders/...`` on macOS),
            # while ``db.symbols.file`` is whatever the indexer
            # stored at build time. Resolving both sides through
            # ``realpath`` keeps them comparable.
            real_path = os.path.realpath(file_path)
            found = db.find_symbol_for(real_path, lineno)
            if found is None:
                # Fall back to the raw path in case ``realpath``
                # failed (e.g. the file was deleted between
                # coverage and this lookup).
                found = db.find_symbol_for(file_path, lineno)
            if found is not None:
                dirty.add(found[0])
        return dirty

    def _dirty_symbols_from_deleted_test_file(
        self, db: IndexDB, deleted_file: str
    ) -> set[str]:
        """Recover prior observed consumers of a modified/deleted test file.

        Engine results retain their historical consumer union in mutant details;
        symbol_tests need not contain any mappings. Compare whole nodeID file
        portions against the project root, including parametrized tests and
        normalized absolute/relative paths.
        """
        changed_path = Path(deleted_file).resolve()
        recorded_ids: set[str] = set()
        for row in db._execute(
            "SELECT details FROM mutants WHERE details LIKE '%test_ids_run%'"
        ).fetchall():
            try:
                details = json.loads(row[0])
            except (TypeError, ValueError):
                # Legacy unreadable details carry no recoverable consumer IDs.
                continue
            recorded_ids.update(details.get("test_ids_run", []))
        matching_ids = {
            test_id for test_id in recorded_ids
            if (self.project_root / test_id.split("::", 1)[0]).resolve() == changed_path
        }
        return db.symbols_for_tests(matching_ids)

    def _reconcile_deletion(self, db: IndexDB, file_path: str) -> ReconcileResult:
        """Reconcile a deleted file: pass empty source, then drop
        the (now-empty) symbol set so the index is clean."""
        rec = db.reconcile_file(file_path, "")
        return rec

    # ------------------------------------------------------------------
    # Re-analysis thread
    # ------------------------------------------------------------------

    def _maybe_spawn_reanalysis(self, db: IndexDB) -> None:
        """Start a re-analysis pass if one is due.

        A pass is due when:
        * there are pending dirty/stale/error symbols, AND
        * no pass is currently running, AND
        * at least ``reanalyze_debounce`` seconds have passed since
          the last change (so we don't fire on every keystroke), AND
        * a change has actually happened *since* the last pass
          started. Without this guard, a pass that leaves symbols
          dirty (e.g. an engine that didn't write back) would
          re-spawn on every poll forever.
        """
        if self._reanalyzing:
            return
        if self._last_change_at is None:
            return
        if (time.monotonic() - self._last_change_at) < self.reanalyze_debounce:
            return
        if self._stop.is_set():
            return
        if (
            self._last_reanalysis_at is not None
            and self._last_change_at <= self._last_reanalysis_at
        ):
            return
        pending = db.get_dirty_symbols()
        if not pending:
            return
        self._last_reanalysis_at = time.monotonic()
        self._reanalyzing = True
        self._reanalyzer_thread = threading.Thread(
            target=self._run_reanalysis,
            args=(db, pending),
            daemon=True,
        )
        self._reanalyzer_thread.start()

    def _run_reanalysis(self, db: IndexDB, pending: list[str]) -> None:
        try:
            with self._application_pass(db):
                self._print(f"[analyze] re-analyzing {len(pending)} dirty symbol(s)...")
                # Observer mode: also report baseline test failures
                # so the operator sees "tests that don't pass on the
                # unmutated code" alongside the surviving / uncovered
                # mutants the engine surfaces during mutation
                # analysis. The baseline check is a separate pytest
                # invocation in a subprocess — we deliberately do not
                # use the engine's pytest.main() in-process here
                # because that would conflict with the mutating
                # import hook the engine installs below.
                if self.observer:
                    self._print("[observer] running baseline tests...")
                    for failed_id in self._check_baseline_tests():
                        self._print(f"[observer] test failing: {failed_id}")
                # The engine processes every target file (it has to,
                # because it doesn't know which file the user edited
                # without re-scanning). That means it emits progress
                # events for symbols the user didn't touch — cache
                # hits against the prior clean state. The user does
                # not want to read those. Filter the stream so the
                # log only shows:
                #
                #   * the change events themselves (printed by the
                #     poll loop, not here), and
                #   * the actual work performed against the dirty
                #     symbols in ``pending``.
                #
                # Cache-hit mutants are silently dropped — they
                # represent "nothing happened here", which is the
                # opposite of what the user wants to see. Symbols
                # that turn out to be entirely cache-hit are also
                # skipped, so the log is empty between
                # ``re-analyzing N dirty symbol(s)`` and
                # ``[analyze] done`` when no real work was needed.
                #
                # When ``self.verbose`` is False (the default) the
                # callback is a no-op — the engine still emits
                # events (they're free), but the daemon doesn't
                # print them. Only the ``re-analyzing N`` and
                # ``done`` lines remain, plus the change events
                # from the poll loop. ``--verbose`` re-enables the
                # per-symbol breakdown for debugging.
                #
                # ``--observer`` is a third mode. Its callback only
                # prints the two signals that mean "work to do":
                # ``status="survived"`` (weak coverage) and
                # ``status="error"`` (no test ran for this mutant =
                # uncovered code). Killed mutants are deliberately
                # silent — they are good news, not work. Cache-hit
                # and non-dirty symbols are dropped for the same
                # reason as in verbose mode.
                pending_set: set[str] = set(pending)
                tally: dict[str, dict[str, int]] = {}
                current: str | None = None
                # Observer mode aggregates work-to-do signals to one
                # line per symbol, so the operator sees "pieces of
                # code" rather than a torrent of per-mutant detail.
                observer_tally: dict[str, dict[str, int]] = {}
                observer_current: list[str | None] = [None]

                def on_progress(ev: EngineProgress) -> None:
                    if self.observer:
                        self._observer_progress(
                            ev,
                            pending_set,
                            observer_tally,
                            observer_current,
                        )
                        return
                    if not self.verbose:
                        return
                    nonlocal current
                    if ev.kind == "symbol-start":
                        # Flush the previous symbol's summary (if any
                        # non-cache-hit work happened for it) before
                        # moving on.
                        if current in tally and any(
                            v for k, v in tally[current].items() if k != "cache-hit"
                        ):
                            self._emit_symbol_summary(current, tally)
                        current = ev.symbol_id
                        # Skip headers for symbols the user didn't
                        # touch (the engine processes them, but they
                        # aren't part of "what I did to this change").
                        if ev.symbol_id is None or ev.symbol_id not in pending_set:
                            return
                        tally[ev.symbol_id] = {
                            "killed": 0,
                            "survived": 0,
                            "error": 0,
                        }
                        self._print(
                            f"[analyze]   {ev.symbol_short} "
                            f"({os.path.basename(ev.file_path)}:"
                            f"{ev.lineno}, {ev.n_mutants_in_symbol} "
                            f"mutants)"
                        )
                        return
                    if ev.kind == "mutant":
                        if ev.symbol_id is None:
                            return
                        # Drop cache-hit mutants. The user wants to
                        # see what the daemon *did*, not what it
                        # already knew.
                        if ev.status == "cache-hit":
                            return
                        # Drop mutants for symbols outside the dirty
                        # set (same reason as above).
                        if ev.symbol_id not in pending_set:
                            return
                        counts = tally.setdefault(
                            ev.symbol_id,
                            {
                                "killed": 0,
                                "survived": 0,
                                "error": 0,
                            },
                        )
                        counts[ev.status] = counts.get(ev.status, 0) + 1
                        self._print_mutant(ev)

                engine = self.engine_factory(db, on_progress)
                target_files = self._collect_target_files()
                if not target_files:
                    return
                test_dir = self._resolve_test_dir()
                # No invented filename selection: let Engine retain genuine
                # coverage-derived nodeIDs and normal configured pytest discovery
                # for import-time or otherwise uncovered mutation points.
                try:
                    engine.run(
                        target_files=target_files,
                        test_dir=str(test_dir) if test_dir else None,
                        test_node_ids=None,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._print(f"[analyze] error: {exc!r}")
                else:
                    if self.observer:
                        # Flush the final symbol's observer summary.
                        if observer_current[0] is not None:
                            self._emit_observer_summary(observer_current[0], observer_tally)
                    elif self.verbose and current is not None and current in tally:
                        if any(
                            v for k, v in tally[current].items() if k != "cache-hit"
                        ):
                            self._emit_symbol_summary(current, tally)
                    self._print(f"[analyze] done. {DaemonStatus.from_db(db).render()}")
        finally:
            self._reanalyzing = False

    # ------------------------------------------------------------------
    # Observer mode
    # ------------------------------------------------------------------

    def _observer_progress(
        self,
        ev: EngineProgress,
        pending_set: set[str],
        observer_tally: dict[str, dict[str, int]],
        observer_current: list[str | None],
    ) -> None:
        """Observer-mode progress filter.

        Only two event types produce output, and they are
        aggregated to one line per symbol so the operator
        sees "pieces of code that need work", not a torrent
        of per-mutant detail.

        * ``status="survived"`` — code where the test suite ran
          but didn't catch the mutation. Translation: weak test
          coverage. Action: add a test that exercises this path.
        * ``status="error"`` — code where ``tests_run == 0``.
          No test even attempted this mutation. Translation:
          uncovered code. Action: write a test that imports /
          calls this function.

        Killed mutants are deliberately silent. A killed mutant
        means the existing test suite caught the bug — there's
        no work to do. Cache-hit and non-dirty symbols are
        dropped for the same reasons as in verbose mode.
        """
        if ev.kind == "symbol-start":
            # Flush the previous symbol's summary first so the
            # output reads in source order.
            prev = observer_current[0]
            if prev is not None:
                self._emit_observer_summary(prev, observer_tally)
            observer_current[0] = ev.symbol_id
            if ev.symbol_id is None or ev.symbol_id not in pending_set:
                return
            observer_tally[ev.symbol_id] = {
                "survived": 0,
                "error": 0,
            }
            return
        if ev.kind != "mutant":
            return
        if ev.symbol_id is None:
            return
        if ev.status == "cache-hit":
            return
        if ev.symbol_id not in pending_set:
            return
        if ev.status not in ("survived", "error"):
            # killed: good news, no work to do. Skip silently.
            return
        counts = observer_tally.setdefault(ev.symbol_id, {"survived": 0, "error": 0})
        counts[ev.status] = counts.get(ev.status, 0) + 1

    def _emit_observer_summary(
        self,
        symbol_id: str,
        tally: dict[str, dict[str, int]],
    ) -> None:
        """Print one observer-mode summary for a symbol.

        Format:
        ``[observer] <symbol> \u2014 N weak coverage, M no coverage``

        The symbol id is printed in full (module path +
        symbol name). Operators asked for the full name
        rather than a truncated short form because the
        module path is what disambiguates two symbols with
        the same short name in different modules (e.g.
        ``runner:_trace`` vs ``plugin:_trace``).

        Only emitted when at least one of the two counts is
        non-zero, so symbols whose mutants were all killed
        (good) produce no output.
        """
        counts = tally.get(symbol_id, {})
        survived = counts.get("survived", 0)
        errored = counts.get("error", 0)
        if survived == 0 and errored == 0:
            return
        parts: list[str] = []
        if survived:
            parts.append(f"{survived} weak coverage")
        if errored:
            parts.append(f"{errored} no coverage")
        self._print(f"[observer] {symbol_id} \u2014 {', '.join(parts)}")

    def _check_baseline_tests(self) -> list[str]:
        """Run the project's test suite with no mutations.

        Returns the list of failing pytest node ids (e.g.
        ``"tests/foo.py::test_x"``). Returns an empty list if
        every test passes, the test directory is missing, or the
        subprocess can't be launched.

        Runs in a subprocess (not ``pytest.main()`` in-process)
        because the daemon thread shares its Python interpreter
        with the engine, which is about to install a mutating
        import hook. Letting baseline pytest run in-process
        would either short-circuit before the hook is installed
        or, worse, leave the interpreter in a half-mutated
        state for the engine. Subprocess isolation is the safe
        default.
        """
        test_dir = self._resolve_test_dir()
        if test_dir is None or not test_dir.is_dir():
            return []
        try:
            import subprocess
            import sys as _sys

            result = subprocess.run(
                [
                    _sys.executable,
                    "-m",
                    "pytest",
                    str(test_dir),
                    "--tb=no",
                    "-q",
                    "--no-header",
                ],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._print(f"[observer] baseline pytest failed to run: {exc!r}")
            return []
        if result.returncode == 0:
            return []
        failures: list[str] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("FAILED "):
                # pytest's short summary line format:
                #   FAILED tests/foo.py::test_x - assert ...
                parts = line.split()
                if len(parts) >= 2:
                    failures.append(parts[1].rstrip(" -"))
        return failures

    def _collect_target_files(self) -> list[str]:
        return [str(p) for p in self._iter_source_files()]

    def _resolve_test_dir(self) -> Path | None:
        resolved: Path | None = None
        if self.test_dir is not None:
            resolved = self.test_dir
        else:
            for candidate in ("tests", "test"):
                path = self.project_root / candidate
                if path.is_dir():
                    resolved = path
                    break
        return resolved

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Run the daemon until interrupted.

        Opens the index, builds the initial state, then enters the
        poll loop. Prints one line per change plus a status line
        every ``status_interval`` polls.
        """
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with IndexDB(self.index_path) as db:
            self._initial_build(db)
            self._print(f"[watch] {self.project_root}  index: {self.index_path}")
            self._print(DaemonStatus.from_db(db).render())
            try:
                self._loop(db)
            except KeyboardInterrupt:
                self._print("\n[stop] interrupted")
            finally:
                self.stop()
                # Engine has no cancellation API. Drain before closing its DB.
                # User tests may hang: shutdown is not globally time-bounded.
                if self._reanalyzer_thread is not None:
                    self._reanalyzer_thread.join()
                # No new worker after stop. Finish already-requested scans while
                # the DB is open; never join user code under application ownership.
                with self._application_pass(db):
                    pass

    def _loop(self, db: IndexDB) -> None:
        status_interval = 10  # every N polls
        polls = 0
        while not self._stop.is_set():
            events = self._scan_for_changes(db)
            for ev in events:
                self._print(ev.render())
                if ev.reconcile and (
                    ev.reconcile.added or ev.reconcile.removed or ev.reconcile.changed
                ):
                    self._last_change_at = time.monotonic()
            self._maybe_spawn_reanalysis(db)
            polls += 1
            if polls % status_interval == 0:
                self._print(DaemonStatus.from_db(db).render())
            # Sleep with responsiveness to Ctrl-C.
            if self._stop.wait(self.poll_interval):
                return

    def stop(self) -> None:
        """Request the daemon to stop at the next poll boundary."""
        self._stop.set()


# ---------------------------------------------------------------------------
# Script entry point: ``python -m pytest_leela.daemon [PROJECT]``
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m pytest_leela.daemon``.

    Subcommands:

    * ``watch`` (default) — run the long-lived daemon.
    * ``status`` — read the index DB and print the current
      state, then exit. No polling, no analysis. Useful for
      CI status checks, scripts, and quick eyeballing.

    Examples::

        python -m pytest_leela.daemon status
        python -m pytest_leela.daemon status /path/to/project --gaps
        python -m pytest_leela.daemon status --json
        python -m pytest_leela.daemon watch /path/to/project
    """
    import argparse  # local: avoid pulling argparse into the import graph

    parser = argparse.ArgumentParser(
        prog="leela",
        description="Leela: watch a project or report its current state.",
    )
    sub = parser.add_subparsers(dest="command")

    # ----- watch (default) ----------------------------------------------
    watch = sub.add_parser(
        "watch",
        help="Run the daemon (default if no subcommand is given).",
        description="Watch a project and re-analyze mutated symbols on change.",
    )
    watch.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Path to the project root (default: current directory).",
    )
    watch.add_argument(
        "--index",
        default=None,
        help="Path to the leela index DB (default: <project>/.leela/index.db).",
    )
    watch.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="File-system poll interval in seconds (default: %(default)s).",
    )
    watch.add_argument(
        "--reanalyze-debounce",
        type=float,
        default=2.0,
        help="Seconds to wait after a change before re-analyzing (default: %(default)s).",
    )
    watch.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "Print the per-symbol / per-mutant progress log. "
            "Default is quiet: change events, "
            "'re-analyzing N dirty symbol(s)' and 'done' lines only."
        ),
    )
    watch.add_argument(
        "--observer",
        action="store_true",
        help=(
            "Surface only 'work to do' signals: baseline test "
            "failures, surviving mutants (weak coverage), and "
            "uncovered code (no test ran for the mutation). "
            "Killed mutants and the per-symbol breakdown are "
            "suppressed. Mutually exclusive in spirit with "
            "--verbose; --observer wins if both are set."
        ),
    )

    # ----- status ------------------------------------------------------
    status_p = sub.add_parser(
        "status",
        help="Read the index and print the current state, then exit.",
        description=(
            "Report the daemon's current view of the project "
            "without running any analysis. Reads the index DB "
            "and prints the same three-bucket status line the "
            "daemon emits, plus optional detail."
        ),
    )
    status_p.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Path to the project root (default: current directory).",
    )
    status_p.add_argument(
        "--index",
        default=None,
        help="Path to the leela index DB (default: <project>/.leela/index.db).",
    )
    status_p.add_argument(
        "--gaps",
        action="store_true",
        help="Also list the symbols that have surviving or uncovered mutants.",
    )
    status_p.add_argument(
        "--by-file",
        action="store_true",
        help=(
            "Group gap-having symbols by source file, with each "
            "file's gap count. Useful for picking which file to "
            "open next. Implies --gaps."
        ),
    )
    status_p.add_argument(
        "--symbol",
        metavar="SYMBOL_ID",
        help=(
            "Show the per-mutant detail (line numbers, ops, "
            "killing test) for one specific gap-having symbol. "
            "Combined with --json for machine-readable output."
        ),
    )
    status_p.add_argument(
        "--coverage",
        action="store_true",
        help=(
            "For a --symbol query, also show the tests that "
            "cover each mutant's mutated line. Lets you see "
            "whether the gap is 'no test ran' (truly "
            "uncovered) or 'tests ran but missed' (weak "
            "assertion)."
        ),
    )
    status_p.add_argument(
        "--categorized",
        action="store_true",
        help=(
            "Split gap-having symbols into two lists: those "
            "with 'no test ran' (need new tests) and those "
            "with 'tests ran but missed' (need stronger "
            "assertions)."
        ),
    )
    status_p.add_argument(
        "--json",
        action="store_true",
        help="Emit the status as JSON instead of the human-readable line.",
    )

    # Preserve legacy PROJECT/FLAGS invocation, without dropping option-only
    # arguments or hijacking top-level help.
    tokens = list(_sys.argv[1:] if argv is None else argv)
    if not tokens or tokens[0] not in {"watch", "status", "--help", "-h"}:
        tokens.insert(0, "watch")

    args = parser.parse_args(tokens)

    if args.command == "status":
        return _status(args)
    # ``watch`` (and anything else) goes through the daemon.
    return _watch(args)


def _resolve_index_path(args: argparse.Namespace) -> Path:
    project = Path(args.project).resolve()
    if not project.is_dir():
        raise SystemExit(f"error: {project} is not a directory")
    if args.index:
        return (
            project / args.index
            if not Path(args.index).is_absolute()
            else Path(args.index)
        )
    return project / ".leela" / "index.db"


def _status(args: argparse.Namespace) -> int:
    """Print the daemon's current state and exit.

    Exit codes:

    * ``0`` — every symbol is covered (no gaps, no pending).
    * ``1`` — there is work to do (gaps or pending re-analysis).
    * ``2`` — error (no index DB yet, can't read, etc.).
    """
    import json as _json

    from .index import IndexDB

    try:
        index_path = _resolve_index_path(args)
    except SystemExit as e:
        print(str(e).removeprefix("error: "), file=_sys.stderr)
        return 2

    if not index_path.exists():
        print(
            f"error: no index at {index_path} \u2014 has the daemon ever run on this project?",
            file=_sys.stderr,
        )
        return 2

    project = Path(args.project).resolve()
    with IndexDB(index_path) as db:
        status = DaemonStatus.from_db(db)
        gaps: list[tuple[str, int]] = []
        gap_files: list[tuple[str, str, int]] = []
        show_gaps = args.gaps or args.by_file
        if show_gaps and status.with_gaps > 0:
            for sid, f, n in db.gap_symbols_with_files():
                gap_files.append((sid, f, n))
                gaps.append((sid, n))

        # Per-symbol mutant detail for ``--symbol``.
        symbol_mutants: list[dict[str, Any]] = []
        if args.symbol:
            symbol_mutants = db.gap_mutants_for_symbol(args.symbol)

        # Categorized split for ``--categorized``.
        categorized: dict[str, list[tuple[str, int]]] = {
            "no_test_ran": [],
            "tests_ran": [],
        }
        if args.categorized and status.with_gaps > 0:
            categorized = db.categorize_gaps()

    if args.json:
        payload = {
            "project": str(project),
            "index": str(index_path),
            "total_symbols": status.total_symbols,
            "covered": status.covered,
            "with_gaps": status.with_gaps,
            "pending_reanalysis": status.pending_reanalysis,
        }
        if args.gaps:
            payload["gaps"] = [
                {
                    "symbol_id": sid,
                    "file": f,
                    "n_uncovered_mutants": n,
                }
                for sid, f, n in gap_files
            ]
        if args.by_file:
            # Group gaps by source file, sorted by gap count desc.
            from collections import defaultdict

            by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for sid, f, n in gap_files:
                by_file[f].append({"symbol_id": sid, "n_uncovered_mutants": n})
            payload["by_file"] = [
                {
                    "file": f,
                    "n_gap_symbols": len(items),
                    "n_uncovered_mutants": sum(i["n_uncovered_mutants"] for i in items),
                    "symbols": sorted(
                        items,
                        key=lambda i: -i["n_uncovered_mutants"],
                    ),
                }
                for f, items in sorted(
                    by_file.items(),
                    key=lambda kv: -sum(i["n_uncovered_mutants"] for i in kv[1]),
                )
            ]
        if args.symbol:
            payload["symbol"] = {
                "symbol_id": args.symbol,
                "mutants": symbol_mutants,
            }
        if args.categorized:
            payload["categorized"] = {
                cat: [{"symbol_id": sid, "n_mutants": n} for sid, n in items]
                for cat, items in categorized.items()
            }
        print(_json.dumps(payload, indent=2))
    else:
        print(status.render())
        if args.gaps and gaps:
            print(f"  symbols with gaps ({len(gaps)}):")
            for sid, n in gaps:
                print(f"    {sid}  ({n} surviving/uncovered)")
        if args.by_file and gap_files:
            from collections import defaultdict

            file_totals: dict[str, int] = defaultdict(int)
            for _sid, f, n in gap_files:
                file_totals[f] += n
            print(f"  files with gaps ({len(file_totals)}):")
            for f, n in sorted(file_totals.items(), key=lambda kv: -kv[1]):
                print(f"    {n:3d}  {f}")
        if args.symbol:
            if symbol_mutants:
                print(f"  mutants with gaps for {args.symbol} ({len(symbol_mutants)}):")
                for m in symbol_mutants:
                    covered = (
                        "no test ran"
                        if m["tests_run"] == 0
                        else f"survived {m['tests_run']} tests"
                    )
                    print(
                        f"    {m['file']}:L{m['lineno']}  "
                        f"{m['original_op']}->{m['replacement_op']}  "
                        f"({covered})"
                    )
            else:
                print(f"  no gap mutants for {args.symbol}")
        if args.categorized:
            no_run = categorized["no_test_ran"]
            ran = categorized["tests_ran"]
            print(
                f"  no test ran ({len(no_run)} symbols, "
                f"{sum(n for _, n in no_run)} mutants):"
            )
            for sid, n in no_run[:10]:
                print(f"    {n}  {sid}")
            if len(no_run) > 10:
                print(f"    ... and {len(no_run) - 10} more")
            print(
                f"  tests ran but missed ({len(ran)} symbols, "
                f"{sum(n for _, n in ran)} mutants):"
            )
            for sid, n in ran[:10]:
                print(f"    {n}  {sid}")
            if len(ran) > 10:
                print(f"    ... and {len(ran) - 10} more")

    return 0 if status.with_gaps == 0 and status.pending_reanalysis == 0 else 1


def _watch(args: argparse.Namespace) -> int:
    """Run the long-lived daemon. Pre-subcommand behaviour."""
    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"error: {project} is not a directory", file=_sys.stderr)
        return 2
    daemon = LeelaDaemon(
        project_root=project,
        index_path=args.index,
        poll_interval=args.poll_interval,
        reanalyze_debounce=args.reanalyze_debounce,
        verbose=args.verbose,
        observer=args.observer,
    )
    daemon.run()
    return 0


if __name__ == "__main__":
    import sys as _sys

    raise SystemExit(main(_sys.argv[1:]))
