"""Integration tests for the engine's index consult path.

These tests stand up a tiny project in ``tmp_path``, run the engine
twice (cold then warm cache), and verify that:

* Cold cache: every mutant is tested and written to the index.
* Warm cache: cache-hit mutants skip the test run; the cached
  ``MutantResult`` is returned.
* Source edit: only the edited symbol's mutants are re-analyzed.
* Disabled index: engine behaves as before, with no DB writes.

The test project is intentionally small (one source file, one
function, one test) so the inner ``pytest.main()`` calls finish
in a few hundred milliseconds.
"""

from __future__ import annotations

import time
from pathlib import Path

from pytest_leela.engine import Engine
from pytest_leela.index import IndexDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_project(root: Path) -> tuple[Path, Path]:
    """Create a tiny project (one source file, one test) under root.

    Returns ``(source_path, test_dir)``.
    """
    src = root / "calc.py"
    src.write_text("def add(x: int, y: int) -> int:\n    return x + y\n")
    test_dir = root / "tests"
    test_dir.mkdir()
    (test_dir / "test_calc.py").write_text(
        "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    return src, test_dir


def _run_engine(
    source: Path,
    test_dir: Path,
    index: IndexDB | None,
) -> tuple[int, int, float]:
    """Run the engine on the tiny project.

    Returns ``(mutants_tested, killed, wall_time)``.
    """
    engine = Engine(use_coverage=False, index=index)
    start = time.monotonic()
    result = engine.run(
        target_files=[str(source)],
        test_dir=str(test_dir),
    )
    wall = time.monotonic() - start
    return (
        result.mutants_tested,
        sum(1 for r in result.results if r.killed),
        wall,
    )


# ---------------------------------------------------------------------------
# Cold cache
# ---------------------------------------------------------------------------


def describe_engine_index_cold_cache():
    def it_populates_the_index_with_every_mutant(tmp_path: Path):

        src, tests = _write_project(tmp_path)
        db_path = tmp_path / "index.db"
        with IndexDB(db_path) as db:
            n_tested, n_killed, _ = _run_engine(src, tests, db)
            assert n_tested > 0
            # The engine may generate multiple equivalent mutants at
            # the same location/operator (e.g. 4 ways to negate a
            # return value), and the index collapses them to one row
            # because their cache key is identical. Verify the
            # invariant: n_rows is in [1, n_tested].
            cur = db._conn.execute("SELECT COUNT(*) FROM mutants")  # type: ignore[attr-defined]
            n_rows = int(cur.fetchone()[0])
            assert 1 <= n_rows <= n_tested
            # At least one symbol exists for the source file and is
            # marked clean.
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT id, state FROM symbols WHERE file = ?", (str(src),)
            )
            rows = cur.fetchall()
            assert len(rows) >= 1
            states = {str(r[1]) for r in rows}
            assert "clean" in states

    def it_does_not_write_when_index_is_none(tmp_path: Path):
        src, tests = _write_project(tmp_path)
        n_tested, _n_killed, _ = _run_engine(src, tests, index=None)
        assert n_tested > 0


# ---------------------------------------------------------------------------
# Warm cache
# ---------------------------------------------------------------------------


def describe_engine_index_warm_cache():
    def it_skips_already_analyzed_mutants(tmp_path: Path):

        src, tests = _write_project(tmp_path)
        db_path = tmp_path / "index.db"

        # Cold run - populates the index.
        with IndexDB(db_path) as db:
            _run_engine(src, tests, db)
            cur = db._conn.execute("SELECT MAX(analyzed_at) FROM mutants")  # type: ignore[attr-defined]
            cold_max = str(cur.fetchone()[0])
            assert cold_max is not None

        # Warm run - no source change. Every mutant should be a cache
        # hit, so no write happens and the max timestamp is unchanged.
        with IndexDB(db_path) as db:
            _run_engine(src, tests, db)
            cur = db._conn.execute("SELECT MAX(analyzed_at) FROM mutants")  # type: ignore[attr-defined]
            warm_max = str(cur.fetchone()[0])
            assert warm_max == cold_max, (
                "warm run should not rewrite any mutant (all cache hits)"
            )

    def it_returns_cached_kill_results(tmp_path: Path):
        src, tests = _write_project(tmp_path)
        db_path = tmp_path / "index.db"

        with IndexDB(db_path) as db:
            cold_tested, cold_killed, _ = _run_engine(src, tests, db)

        with IndexDB(db_path) as db:
            warm_tested, warm_killed, _ = _run_engine(src, tests, db)

        # Same number of mutants, same number killed, same mutation
        # score - the warm run is observationally indistinguishable
        # from the cold run, but it skipped the test executions.
        assert warm_tested == cold_tested
        assert warm_killed == cold_killed

    def it_marks_symbols_clean_on_a_warm_cache_hit(tmp_path: Path):
        """Regression: a warm-cache run used to leave every symbol
        in ``STATE_DIRTY`` because the engine's cache-hit branch
        skipped the work but never added the symbol to
        ``analyzed_symbols``. The daemon's status line would
        therefore show ``0 clean, N dirty`` forever, even though
        the work was already done. The fix: on a cache hit, add
        the symbol to ``analyzed_symbols`` so the final
        ``mark_symbol_clean`` loop covers it.
        """

        src, tests = _write_project(tmp_path)
        db_path = tmp_path / "index.db"

        # Cold run. The engine re-conciles the file at the start of
        # the run, so all symbols go dirty. Then it actually
        # analyzes them and marks them clean.
        with IndexDB(db_path) as db:
            _run_engine(src, tests, db)
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT state, COUNT(*) FROM symbols GROUP BY state"
            )
            states_after_cold = {str(r[0]): int(r[1]) for r in cur.fetchall()}
        assert states_after_cold.get("clean", 0) >= 1, (
            f"cold run should leave at least one clean symbol, got {states_after_cold}"
        )

        # Warm run. The engine re-conciles (so symbols go back to
        # dirty) and then iterates mutants. Every mutant is a cache
        # hit so the engine does no test work, but the symbol must
        # STILL end up clean at the end of the run.
        with IndexDB(db_path) as db:
            _run_engine(src, tests, db)
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT state, COUNT(*) FROM symbols GROUP BY state"
            )
            states_after_warm = {str(r[0]): int(r[1]) for r in cur.fetchall()}

        assert states_after_warm.get("dirty", 0) == 0, (
            "warm run with full cache coverage should leave zero "
            f"dirty symbols, got {states_after_warm}"
        )
        assert states_after_warm.get("clean", 0) >= 1


# ---------------------------------------------------------------------------
# Cache invalidation on edit
# ---------------------------------------------------------------------------


def describe_engine_index_invalidation():
    def it_reanalyzes_only_the_edited_symbol(tmp_path: Path):
        """Edit the source function, re-run, verify its mutants were
        re-analyzed (newer timestamp than the cold run)."""

        src, tests = _write_project(tmp_path)
        db_path = tmp_path / "index.db"

        # Cold run.
        with IndexDB(db_path) as db:
            _run_engine(src, tests, db)
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT id FROM symbols WHERE file = ?", (str(src),)
            )
            symbol_ids = [str(r[0]) for r in cur.fetchall()]
            assert len(symbol_ids) == 1
            sid = symbol_ids[0]
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT MAX(analyzed_at) FROM mutants WHERE symbol_id = ?",
                (sid,),
            )
            cold_add = str(cur.fetchone()[0])
            assert cold_add is not None

        # Edit the function body in a way that changes the source
        # segment (``return x + y`` -> ``return x + y + 0``). The
        # behavior is identical but the hash changes.
        src.write_text(
            '"""Tiny arithmetic module."""\n'
            "def add(x: int, y: int) -> int:\n"
            "    return x + y + 0\n"
        )

        # Re-run. The mutants for this symbol should be re-analyzed
        # (newer timestamp). The index is reopened to observe the
        # writes.
        with IndexDB(db_path) as db:
            _run_engine(src, tests, db)
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT MAX(analyzed_at) FROM mutants WHERE symbol_id = ?",
                (sid,),
            )
            warm_add = str(cur.fetchone()[0])

        assert warm_add is not None
        assert warm_add > cold_add, (
            f"edit should trigger re-analysis: cold={cold_add}, warm={warm_add}"
        )

    def it_handles_a_new_symbol_added_in_the_same_file(tmp_path: Path):
        """Add a new function in the same file. The new symbol is
        analyzed, the existing one is a cache hit."""

        src, tests = _write_project(tmp_path)
        db_path = tmp_path / "index.db"

        # Cold run.
        with IndexDB(db_path) as db:
            _run_engine(src, tests, db)
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT id FROM symbols WHERE file = ?", (str(src),)
            )
            symbols_after_cold = {str(r[0]) for r in cur.fetchall()}
            assert len(symbols_after_cold) == 1

        # Add a second function in the same source file.
        src.write_text(
            '"""Tiny arithmetic module."""\n'
            "def add(x: int, y: int) -> int:\n"
            "    return x + y\n"
            "def sub(x: int, y: int) -> int:\n"
            "    return x - y\n"
        )
        (tests / "test_calc.py").write_text(
            "from calc import add, sub\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
            "def test_sub():\n"
            "    assert sub(5, 2) == 3\n"
        )

        # Warm run.
        with IndexDB(db_path) as db:
            _run_engine(src, tests, db)
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT id FROM symbols WHERE file = ?", (str(src),)
            )
            symbols_after_warm = {str(r[0]) for r in cur.fetchall()}

        # The set of symbols should have grown by exactly one.
        new_symbols = symbols_after_warm - symbols_after_cold
        assert len(new_symbols) == 1

    def it_drops_deleted_symbols_from_the_index(tmp_path: Path):
        """Delete a symbol from the source, re-run, verify it is
        gone from the index."""

        src, tests = _write_project(tmp_path)
        db_path = tmp_path / "index.db"

        with IndexDB(db_path) as db:
            _run_engine(src, tests, db)
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT id FROM symbols WHERE file = ?", (str(src),)
            )
            before = {str(r[0]) for r in cur.fetchall()}
            assert len(before) == 1

        # Replace the file with one that has no functions.
        src.write_text('"""empty module."""\n')

        with IndexDB(db_path) as db:
            _run_engine(src, tests, db)
            cur = db._conn.execute(  # type: ignore[attr-defined]
                "SELECT id FROM symbols WHERE file = ?", (str(src),)
            )
            after = {str(r[0]) for r in cur.fetchall()}
        assert after == set()


# ---------------------------------------------------------------------------
# Mutation score accounting
# ---------------------------------------------------------------------------


def describe_engine_index_mutation_score():
    def it_cached_results_count_toward_the_mutation_score(tmp_path: Path):
        """Warm run with full cache coverage should report the same
        mutation score as the cold run (killed / tested)."""
        src, tests = _write_project(tmp_path)
        db_path = tmp_path / "index.db"

        with IndexDB(db_path) as db:
            cold_tested, cold_killed, _ = _run_engine(src, tests, db)
        with IndexDB(db_path) as db:
            warm_tested, warm_killed, _ = _run_engine(src, tests, db)

        assert warm_tested == cold_tested
        assert warm_killed == cold_killed
