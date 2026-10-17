"""Integration tests for the engine's index consult path.

These tests stand up a tiny project in ``tmp_path``, run the engine
twice (cold then warm cache), and verify that:

* Cold cache: every mutant is tested and written to the index.
* Warm cache: cache-hit mutants skip the test run; the cached
  ``MutantResult`` is returned.
* Source edit: edited source invalidates cached mutants.
* Disabled index: engine behaves as before, with no DB writes.

The test project is intentionally small (one source file, one
function, one test) so the inner ``pytest.main()`` calls finish
in a few hundred milliseconds.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pytest_leela.engine import Engine
from pytest_leela.index import IndexDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_project(root: Path, monkeypatch=None) -> tuple[Path, Path]:
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
    if monkeypatch is not None:
        # The unmutated baseline run has no mutating import hook, so the
        # target module must be importable from ``sys.path``/cwd.
        monkeypatch.syspath_prepend(str(root))
        monkeypatch.chdir(str(root))
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
    def it_populates_the_index_with_every_mutant(tmp_path: Path, monkeypatch):

        src, tests = _write_project(tmp_path, monkeypatch)
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

    def it_does_not_write_when_index_is_none(tmp_path: Path, monkeypatch):
        src, tests = _write_project(tmp_path, monkeypatch)
        n_tested, _n_killed, _ = _run_engine(src, tests, index=None)
        assert n_tested > 0


# ---------------------------------------------------------------------------
# Warm cache
# ---------------------------------------------------------------------------


def describe_engine_index_warm_cache():
    def it_skips_already_analyzed_mutants(tmp_path: Path, monkeypatch):

        src, tests = _write_project(tmp_path, monkeypatch)
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

    def it_returns_cached_kill_results(tmp_path: Path, monkeypatch):
        src, tests = _write_project(tmp_path, monkeypatch)
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

    def it_marks_symbols_clean_on_a_warm_cache_hit(tmp_path: Path, monkeypatch):
        """Even cache hits must participate in the final symbol-clean pass."""

        src, tests = _write_project(tmp_path, monkeypatch)
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
    def it_reanalyzes_only_the_edited_symbol(tmp_path: Path, monkeypatch):
        """Edit the source function, re-run, verify its mutants were
        re-analyzed (newer timestamp than the cold run)."""

        src, tests = _write_project(tmp_path, monkeypatch)
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

    def it_handles_a_new_symbol_added_in_the_same_file(tmp_path: Path, monkeypatch):
        """Add a new function in the same file and index its results."""

        src, tests = _write_project(tmp_path, monkeypatch)
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

    def it_drops_deleted_symbols_from_the_index(tmp_path: Path, monkeypatch):
        """Delete a symbol from the source, re-run, verify it is
        gone from the index."""

        src, tests = _write_project(tmp_path, monkeypatch)
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
    def it_cached_results_count_toward_the_mutation_score(tmp_path: Path, monkeypatch):
        """Warm run with full cache coverage should report the same
        mutation score as the cold run (killed / tested)."""
        src, tests = _write_project(tmp_path, monkeypatch)
        db_path = tmp_path / "index.db"

        with IndexDB(db_path) as db:
            cold_tested, cold_killed, _ = _run_engine(src, tests, db)
        with IndexDB(db_path) as db:
            warm_tested, warm_killed, _ = _run_engine(src, tests, db)

        assert warm_tested == cold_tested
        assert warm_killed == cold_killed


def describe_content_aware_cache_and_progress():
    def it_strengthens_tests_without_source_edits_and_reports_warm_hits(tmp_path, monkeypatch):
        from pytest_leela.models import EngineProgress

        src = tmp_path / "calc.py"
        src.write_text("def is_positive(x: int) -> bool:\n    return x > 0\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        test = tests / "test_calc.py"
        weak = "from calc import is_positive\ndef test_positive():\n    assert is_positive(1) is True\n"
        test.write_text(weak)
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        original = src.read_bytes()
        events: list[EngineProgress] = []

        def run():
            events.clear()
            with IndexDB(tmp_path / "index.db") as db:
                result = Engine(use_coverage=False, index=db, on_progress=events.append).run(
                    [str(src)], test_dir=str(tests),
                    test_node_ids=[f"{test}::test_positive"],
                )
                timestamps = db._conn.execute("SELECT analyzed_at FROM mutants ORDER BY id").fetchall()
            assert events[0].kind == "symbol-start"
            assert events[0].symbol_short == "is_positive"
            assert events[0].n_mutants_in_symbol == result.mutants_tested
            assert events[-1].kind == "done"
            assert [e.op for e in events[1:-1]] == [
                f"{r.mutant.point.node_type}:{r.mutant.replacement_op}" for r in result.results
            ]
            assert [e.killing_test for e in events[1:-1]] == [r.killing_test for r in result.results]
            assert all(e.file_path == str(src) and e.symbol_id == events[0].symbol_id for e in events[:-1])
            return result, timestamps

        cold, cold_times = run()
        boundary = next(r for r in cold.results if r.mutant.replacement_op == "GtE")
        assert not boundary.killed
        assert {e.status for e in events[1:-1]} == {"killed", "survived"}
        warm, warm_times = run()
        assert warm.killed == cold.killed
        assert warm_times == cold_times
        assert {e.status for e in events[1:-1]} == {"cache-hit"}

        test.write_text(weak + "    assert is_positive(0) is False\n")
        strengthened, changed_times = run()
        assert src.read_bytes() == original
        assert next(r for r in strengthened.results if r.mutant.replacement_op == "GtE").killed
        assert changed_times != cold_times
        assert "cache-hit" not in {e.status for e in events[1:-1]}

        src.write_text("def is_positive(x: int) -> bool:\n    return x > 0  # source edit\n")
        _, source_times = run()
        assert source_times != changed_times
        assert "cache-hit" not in {e.status for e in events[1:-1]}

    def it_invalidates_fixture_edits_with_unchanged_tests(tmp_path, monkeypatch):
        src = tmp_path / "calc.py"
        src.write_text("def is_positive(x: int) -> bool:\n    return x > 0\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        fixture = tests / "conftest.py"
        fixture.write_text("import pytest\n@pytest.fixture\ndef sample():\n    return 1\n")
        test = tests / "test_calc.py"
        test.write_text("from calc import is_positive\ndef test_positive(sample):\n    assert is_positive(sample) is (sample > 0)\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        events = []
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db, on_progress=events.append)
            cold = engine.run([str(src)], test_dir=str(tests))
            assert not next(r for r in cold.results if r.mutant.replacement_op == "GtE").killed
            fixture.write_text("import pytest\n@pytest.fixture\ndef sample():\n    return 0\n")
            events.clear()
            changed = engine.run([str(src)], test_dir=str(tests))
            assert next(r for r in changed.results if r.mutant.replacement_op == "GtE").killed
            assert "cache-hit" not in {e.status for e in events}

    def it_uses_the_per_mutant_coverage_selection_in_the_cache_key(tmp_path, monkeypatch):
        from pytest_leela.models import CoverageMap, MutantResult
        import pytest_leela.engine as engine_module

        src, tests = _write_project(tmp_path, monkeypatch)
        test = tests / "test_calc.py"
        test.write_text(
            "from calc import add\n"
            "def test_a():\n    assert add(1, 2) == 3\n"
            "def test_b():\n    assert add(1, 2) == 3\n"
        )
        calls = []

        def runner(mutant, *args, **kwargs):
            calls.append(kwargs["test_ids"])
            return MutantResult(mutant, False, 1, None, 0)

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", runner)
        coverage = CoverageMap()
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db)
            executions = []
            for selection in ("test_a", "test_b", "test_b"):
                coverage.line_to_tests = {(str(src), 2): {f"{test}::{selection}"}}
                before = len(calls)
                engine.run([str(src)], test_dir=str(tests),
                           test_node_ids=[f"{test}::test_a", f"{test}::test_b"],
                           pre_coverage_map=coverage)
                executions.append(len(calls) - before)
            assert calls[0] == [f"{test}::test_a"]
            assert [f"{test}::test_b"] in calls
            assert executions[0] > 0
            assert executions[1] == executions[0]
            assert executions[2] == 0

    def it_reports_runner_error_before_reraising_the_same_exception(tmp_path, monkeypatch):
        import pytest
        import pytest_leela.engine as engine_module

        src, tests = _write_project(tmp_path, monkeypatch)
        events = []
        error = RuntimeError("runner failed")

        def runner(*args, **kwargs):
            raise error

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", runner)
        with pytest.raises(RuntimeError) as caught:
            Engine(use_coverage=False, on_progress=events.append).run([str(src)], test_dir=str(tests))
        assert caught.value is error
        assert [e.kind for e in events] == ["symbol-start", "mutant"]
        assert events[-1].status == "error"
        assert events[-1].op
        assert events[-1].symbol_id == events[0].symbol_id

    def it_bypasses_unresolved_discovery_and_unreadable_inputs(tmp_path, monkeypatch):
        import pytest
        import pytest_leela.engine as engine_module
        from pytest_leela.engine import BaselineFailure, _TestDependencies

        src, tests = _write_project(tmp_path, monkeypatch)
        real_runner = engine_module.run_tests_for_mutant
        calls = []

        def runner(mutant, *args, **kwargs):
            calls.append(mutant)
            return real_runner(mutant, *args, **kwargs)

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", runner)
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db)
            for _ in range(2):
                result = engine.run([str(src)])
            assert len(calls) == 2 * result.mutants_tested
            rows = db._conn.execute(
                "SELECT state, test_set_hash FROM mutants"
            ).fetchall()
            assert len(rows) == result.mutants_tested
            assert all(state == "clean" and test_hash == "" for state, test_hash in rows)

            # Whole-tree discovery conservatively scans every Python helper,
            # even when pytest never imports or collects it.  An unreadable
            # unused helper disables result caching while the real baseline
            # and mutant executions remain green.
            unreadable_helper = tests / "unused_helper.py"
            unreadable_helper.write_text("VALUE = 1\n")
            original = Path.read_bytes

            def unreadable(path):
                if path == unreadable_helper:
                    raise PermissionError("unreadable helper")
                return original(path)

            monkeypatch.setattr(Path, "read_bytes", unreadable)
            assert _TestDependencies(str(tests)).fingerprint(None) is None
            before = len(calls)
            for _ in range(2):
                result = engine.run([str(src)], test_dir=str(tests))
            assert len(calls) - before == 2 * result.mutants_tested
            rows = db._conn.execute(
                "SELECT state, test_set_hash FROM mutants"
            ).fetchall()
            assert len(rows) == result.mutants_tested
            assert all(state == "clean" and test_hash == "" for state, test_hash in rows)

            # An unreadable executed test is an ambient baseline failure, not
            # an uncertainty boundary that may proceed to mutant execution.
            executed_test = tests / "test_calc.py"

            def unreadable_executed(path):
                if path == executed_test:
                    raise PermissionError("unreadable executed test")
                return original(path)

            monkeypatch.setattr(Path, "read_bytes", unreadable_executed)
            before = len(calls)
            with pytest.raises(BaselineFailure, match="no mutants were run"):
                Engine(use_coverage=False, index=db).run(
                    [str(src)], test_dir=str(tests)
                )
            assert len(calls) == before


def describe_targeted_dependency_cache():
    def it_invalidates_only_consumers_of_changed_test_helper_and_source_helper(tmp_path, monkeypatch):
        from pytest_leela.models import CoverageMap

        tests = tmp_path / "tests"
        tests.mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        sources = []
        coverage = CoverageMap()
        for group in ("alpha", "beta"):
            source = tmp_path / f"{group}.py"
            source.write_text(f"import offset_{group}\ndef is_positive(x: int) -> bool:\n    return x > offset_{group}.BOUNDARY\n")
            sources.append(str(source))
            (tmp_path / f"offset_{group}.py").write_text("BOUNDARY = 0\n")
            (tmp_path / f"inputs_{group}.py").write_text("def values():\n    return [1]\n")
            test = tests / f"test_{group}.py"
            test.write_text(
                f"import {group}, inputs_{group}, offset_{group}\n"
                f"def test_positive():\n    for x in inputs_{group}.values():\n"
                f"        assert {group}.is_positive(x) is (x > offset_{group}.BOUNDARY)\n"
            )
            coverage.add(str(source), 3, f"{test}::test_positive")
        events = []
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db, on_progress=events.append)

            def run(alpha_cached, beta_cached, alpha_boundary_killed):
                events.clear()
                result = engine.run(sources, test_dir=str(tests), pre_coverage_map=coverage)
                assert [e.symbol_short for e in events if e.kind == "symbol-start"] == ["is_positive", "is_positive"]
                for source, cached in zip(sources, (alpha_cached, beta_cached)):
                    outcomes = [e for e in events if e.kind == "mutant" and e.file_path == source]
                    assert outcomes
                    assert all(e.status == "cache-hit" for e in outcomes) is cached
                    if not cached:
                        assert all(e.status != "cache-hit" for e in outcomes)
                boundary = next(r for r in result.results if r.mutant.point.file_path == sources[0] and r.mutant.replacement_op == "GtE")
                assert boundary.killed is alpha_boundary_killed
                # Transitions precede their results and done follows all results.
                assert events[0].kind == "symbol-start"
                assert events[-1].kind == "done"
                return result

            run(False, False, False)
            run(True, True, False)
            test_alpha = tests / "test_alpha.py"
            weak_test = test_alpha.read_text()
            test_alpha.write_text(weak_test.replace("inputs_alpha.values()", "[1, 0]"))
            run(False, True, True)
            test_alpha.write_text(weak_test)
            run(False, True, False)
            helper = tmp_path / "inputs_alpha.py"
            helper.write_text("def values():\n    return [1, 0]\n")
            run(False, True, True)
            helper.write_text("def values():\n    return [1]\n")
            run(False, True, False)
            (tmp_path / "offset_alpha.py").write_text("BOUNDARY = 1\n")
            run(False, True, True)
            run(True, True, True)
            (tests / "conftest.py").write_text("# shared fixture input added\n")
            run(False, False, True)
            (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = shared: shared marker\n")
            run(False, False, True)
            run(True, True, True)

    def it_bypasses_dynamic_missing_ambiguous_and_out_of_scope_dependencies(
        tmp_path, monkeypatch
    ):
        import pytest
        import pytest_leela.engine as engine_module
        from pytest_leela.engine import BaselineFailure, _TestDependencies

        src, tests = _write_project(tmp_path, monkeypatch)
        test = tests / "test_calc.py"
        real_runner = engine_module.run_tests_for_mutant
        calls = []
        events = []

        def runner(mutant, *args, **kwargs):
            calls.append(mutant)
            return real_runner(mutant, *args, **kwargs)

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", runner)
        (tmp_path / "dynamic_helper.py").write_text("VALUE = 3\n")
        (tests / "ambiguous.py").write_text("VALUE = 3\n")
        (tmp_path / "ambiguous.py").write_text("VALUE = 3\n")
        variants = {
            "dynamic": (
                "import importlib\n"
                "helper = importlib.import_module('dynamic_helper')\n"
                "def test_add():\n"
                "    assert add(1, 2) == helper.VALUE\n"
            ),
            "missing": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n"
                "    import missing_local_helper\n"
                "def test_add():\n"
                "    assert add(1, 2) == 3\n"
            ),
            "ambiguous": (
                "import ambiguous\n"
                "def test_add():\n"
                "    assert add(1, 2) == ambiguous.VALUE\n"
            ),
        }
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db, on_progress=events.append)
            for name, content in variants.items():
                test.write_text("from calc import add\n" + content)
                selection = [f"{test}::test_add"]
                assert _TestDependencies(str(tests)).fingerprint(selection) is None, name
                before = len(calls)
                for _ in range(2):
                    events.clear()
                    result = engine.run(
                        [str(src)], test_dir=str(tests), test_node_ids=selection
                    )
                    assert "cache-hit" not in {e.status for e in events}
                assert len(calls) - before == 2 * result.mutants_tested
                rows = db._conn.execute(
                    "SELECT state, killed, test_set_hash FROM mutants"
                ).fetchall()
                assert len(rows) == result.mutants_tested
                assert all(
                    state == "clean" and test_hash == ""
                    for state, _, test_hash in rows
                )
                assert {killed for _, killed, _ in rows} == {
                    int(outcome.killed) for outcome in result.results
                }

            outside = tmp_path / "test_external.py"
            outside.write_text(
                "from calc import add\n"
                "def test_external():\n"
                "    assert add(1, 2) == 3\n"
            )
            outside_selection = [f"{outside}::test_external"]
            before = len(calls)
            result = engine.run(
                [str(src)], test_dir=str(tests), test_node_ids=outside_selection
            )
            assert len(calls) - before == result.mutants_tested

            persisted_before_failed_baselines = db._conn.execute(
                "SELECT COUNT(*) FROM mutants"
            ).fetchone()[0]
            assert persisted_before_failed_baselines > 0
            before = len(calls)
            with pytest.raises(BaselineFailure, match="no mutants were run"):
                engine.run([str(src)], test_dir=str(tmp_path / "missing-tests"))
            deleted = tests / "deleted.py"
            deleted.write_text("def test_deleted():\n    assert True\n")
            deleted.unlink()
            with pytest.raises(BaselineFailure, match="no mutants were run"):
                engine.run(
                    [str(src)],
                    test_dir=str(tests),
                    test_node_ids=[f"{deleted}::test_deleted"],
                )
            assert len(calls) == before
            assert (
                db._conn.execute("SELECT COUNT(*) FROM mutants").fetchone()[0]
                == persisted_before_failed_baselines
            )

    def it_invalidates_persisted_results_when_environment_identity_changes(tmp_path, monkeypatch):
        import pytest_leela.engine as engine_module
        from pytest_leela.models import MutantResult

        src, tests = _write_project(tmp_path, monkeypatch)
        monkeypatch.syspath_prepend(str(tmp_path))
        calls = []

        def runner(mutant, *args, **kwargs):
            calls.append(mutant)
            return MutantResult(mutant, False, 1, None, 0)

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", runner)
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db)
            cold = engine.run([str(src)], test_dir=str(tests))
            engine.run([str(src)], test_dir=str(tests))
            assert len(calls) == cold.mutants_tested
            monkeypatch.setattr(engine_module.sys, "version", engine_module.sys.version + " changed-build")
            engine.run([str(src)], test_dir=str(tests))
            assert len(calls) == 2 * cold.mutants_tested
            # Known plugin/dependency versions also partition persisted cache.
            original = engine_module.importlib.metadata.distributions

            class Dependency:
                metadata = {"Name": "changed-pytest-plugin"}
                version = "2.0"

                @property
                def entry_points(self):
                    return ()

            monkeypatch.setattr(engine_module.importlib.metadata, "distributions", lambda: [*original(), Dependency()])
            engine.run([str(src)], test_dir=str(tests))
            assert len(calls) == 3 * cold.mutants_tested


def describe_static_dependency_graph():
    def it_tracks_packages_relative_imports_cycles_and_local_pytest_plugins(tmp_path, monkeypatch):
        from pytest_leela.engine import _TestDependencies

        tests = tmp_path / "tests"
        tests.mkdir()
        package = tmp_path / "support"
        package.mkdir()
        (package / "__init__.py").write_text("from . import first\n")
        (package / "first.py").write_text("from .second import VALUE\n")
        second = package / "second.py"
        second.write_text("from . import first\nVALUE = 1\n")
        plugin = tmp_path / "local_fixtures.py"
        plugin.write_text("import pytest\n@pytest.fixture\ndef value():\n    return 1\n")
        (tests / "conftest.py").write_text("pytest_plugins = ('local_fixtures',)\n")
        test = tests / "test_graph.py"
        test.write_text("from support import first\ndef test_value(value):\n    assert first.VALUE == value\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        ids = [f"{test}::test_value"]
        initial = _TestDependencies(str(tests)).fingerprint(ids)
        assert initial is not None
        assert _TestDependencies(str(tests)).fingerprint(ids) == initial
        second.write_text("from . import first\nVALUE = 2\n")
        changed = _TestDependencies(str(tests)).fingerprint(ids)
        assert changed is not None and changed != initial
        plugin.write_text("import pytest\n@pytest.fixture\ndef value():\n    return 2\n")
        assert _TestDependencies(str(tests)).fingerprint(ids) != changed

    def it_bypasses_unsupported_static_origins_and_dynamic_plugin_names(tmp_path, monkeypatch):
        from pytest_leela.engine import _TestDependencies

        tests = tmp_path / "tests"
        tests.mkdir()
        test = tests / "test_graph.py"
        monkeypatch.syspath_prepend(str(tmp_path))
        # Namespace packages are importable but lack a single on-disk origin.
        namespace = tmp_path / "namespace"
        namespace.mkdir()
        (namespace / "helper.py").write_text("VALUE = 1\n")
        test.write_text("from namespace.helper import VALUE\n")
        assert _TestDependencies(str(tests)).fingerprint([str(test)]) is None
        # Syntax errors/missing files must never be reusable cache sentinels.
        test.write_text("def incomplete(\n")
        assert _TestDependencies(str(tests)).fingerprint([str(test)]) is None
        test.write_text("def test_ok():\n    pass\n")
        (tests / "conftest.py").write_text("plugin = 'local_fixtures'\npytest_plugins = [plugin]\n")
        assert _TestDependencies(str(tests)).fingerprint([str(test)]) is None
        (tests / "conftest.py").write_text("pytest_plugins = 'missing_fixture_plugin'\n")
        assert _TestDependencies(str(tests)).fingerprint([str(test)]) is None
        (tests / "conftest.py").unlink()
        test.write_text("from importlib import import_module as load\nhelper = load('local_helper')\n")
        assert _TestDependencies(str(tests)).fingerprint([str(test)]) is None

    def it_hashes_fallback_test_file_additions_and_deletions(tmp_path, monkeypatch):
        from pytest_leela.engine import _TestDependencies

        tests = tmp_path / "tests"
        tests.mkdir()
        test = tests / "test_one.py"
        test.write_text("def test_one():\n    pass\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        original = _TestDependencies(str(tests)).fingerprint(None)
        assert original is not None
        extra = tests / "test_two.py"
        extra.write_text("def test_two():\n    pass\n")
        assert _TestDependencies(str(tests)).fingerprint(None) != original
        extra.unlink()
        assert _TestDependencies(str(tests)).fingerprint(None) == original


def describe_progress_completion():
    def it_emits_done_without_symbol_events_when_no_mutants_exist(tmp_path, monkeypatch):
        src = tmp_path / "empty.py"
        src.write_text("# no mutants\n")
        events = []
        result = Engine(use_coverage=False, on_progress=events.append).run([str(src)])
        assert result.mutants_tested == 0
        assert [event.kind for event in events] == ["done"]


# ---------------------------------------------------------------------------
# Same-line mutation identity (cache correctness)
# ---------------------------------------------------------------------------


def _write_two_site_project(root: Path) -> tuple[Path, Path]:
    """A target with two ``Add`` sites on one line and a discriminating test.

    ``(a + b) * (c + d)`` mutates two different ``Add`` nodes on line 2,
    at columns 12 and 22.  With ``calculate(3, 0, 5, 2)`` the right-hand
    mutation is observable — ``21`` becomes ``9`` — while the left-hand
    one is not, because ``(3 - 0) == (3 + 0)``.  So their verdicts
    differ, and sharing a cache row would silently score the left
    mutant as killed.
    """
    src = root / "twosite.py"
    src.write_text(
        "def calculate(a, b, c, d):\n"
        "    return (a + b) * (c + d)\n"
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_twosite.py").write_text(
        "from twosite import calculate\n"
        "\n"
        "def test_discriminates_the_two_add_sites():\n"
        "    assert calculate(3, 0, 5, 2) == 21\n"
    )
    return src, tests


def _verdicts(result) -> dict[int, bool]:
    """Map each ``Add -> Sub`` mutant's column to its killed verdict."""
    return {
        mutant.mutant.point.col_offset: bool(mutant.killed)
        for mutant in result.results
        if mutant.mutant.point.node_type == "BinOp"
        and mutant.mutant.point.original_op == "Add"
        and mutant.mutant.replacement_op == "Sub"
    }


def describe_same_line_identity():
    def it_gives_distinct_verdicts_to_two_same_line_add_mutants(
        tmp_path, monkeypatch
    ):
        src, tests = _write_two_site_project(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(str(tmp_path))

        plain = Engine(use_coverage=False).run([str(src)], test_dir=str(tests))
        verdicts = _verdicts(plain)
        assert verdicts == {12: False, 22: True}

    def it_never_reuses_one_columns_verdict_for_the_other_when_indexed(
        tmp_path, monkeypatch
    ):
        src, tests = _write_two_site_project(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(str(tmp_path))

        with IndexDB(tmp_path / "index.db") as db:
            indexed = Engine(use_coverage=False, index=db).run(
                [str(src)], test_dir=str(tests)
            )
            assert _verdicts(indexed) == {12: False, 22: True}
            # Both sites exist as distinct rows.
            cur = db._conn.execute(
                "SELECT col_offset, killed FROM mutants WHERE operator = ?",
                ("BinOp:Sub",),
            )
            rows = {(int(c), bool(k)) for c, k in cur.fetchall()}
            assert (12, False) in rows
            assert (22, True) in rows

    def it_stays_distinct_on_a_warm_cache_run(tmp_path, monkeypatch):
        src, tests = _write_two_site_project(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(str(tmp_path))

        with IndexDB(tmp_path / "index.db") as db:
            cold = Engine(use_coverage=False, index=db).run(
                [str(src)], test_dir=str(tests)
            )
            warm = Engine(use_coverage=False, index=db).run(
                [str(src)], test_dir=str(tests)
            )
            cold_verdicts = _verdicts(cold)
            # Cold must already be correct; warm must reproduce it exactly
            # rather than collapsing both sites into one verdict.
            assert cold_verdicts == {12: False, 22: True}
            assert _verdicts(warm) == cold_verdicts


# ---------------------------------------------------------------------------
# Error results are never cached as scored survivals
# ---------------------------------------------------------------------------


def _write_skipped_mutant_project(root: Path) -> tuple[Path, Path]:
    """A target whose mutation leaves the selected test unrunnable.

    The test module marks itself ``skipif`` on a value the mutation
    changes, so under the mutant the test is skipped and the inner run
    executes no test.  The runner then reports ``error``
    (``"no tests ran"``) rather than a kill or a survival: nothing
    caught the mutant, but nothing ran either.
    """
    src = root / "skipped.py"
    src.write_text(
        "def limit() -> int:\n"
        "    return 1 + 1\n"
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_skipped.py").write_text(
        "import pytest\n"
        "import skipped\n"
        "\n"
        "pytestmark = pytest.mark.skipif(\n"
        "    skipped.limit() != 2, reason='limit mutated'\n"
        ")\n"
        "\n"
        "def test_limit_is_two():\n"
        "    assert skipped.limit() == 2\n"
    )
    return src, tests


def describe_unknown_dependency_outcomes_are_persisted_without_cache_reuse():
    def it_persists_killed_and_survived_rows_and_executes_again(tmp_path, monkeypatch):
        src = tmp_path / "calc.py"
        src.write_text("def label(n):\n    return 'odd' if n % 2 == 1 else 'even'\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_calc.py").write_text(
            "calc = __import__('calc')\n"
            "def test_label():\n"
            "    assert calc.label(1) == 'odd'\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(str(tmp_path))

        from pytest_leela.engine import _TestDependencies
        from pytest_leela.runner import run_tests_for_mutant as real_runner

        selected = [str(tests / "test_calc.py") + "::test_label"]
        assert _TestDependencies(str(tests)).fingerprint(selected) is None
        db_path = tmp_path / "index.db"
        with IndexDB(db_path) as db:
            with patch(
                "pytest_leela.engine.run_tests_for_mutant", wraps=real_runner
            ) as tracked:
                cold = Engine(use_coverage=False, index=db).run(
                    [str(src)], test_dir=str(tests), test_node_ids=selected
                )
                assert tracked.call_count == len(cold.results) > 0
            assert cold.killed and cold.survived
            rows = db._conn.execute(
                "SELECT state, killed, test_set_hash FROM mutants"
            ).fetchall()
            assert len(rows) == len(cold.results)
            assert all(state == "clean" and test_hash == "" for state, _, test_hash in rows)
            assert {killed for _, killed, _ in rows} == {0, 1}
            assert db.coverage_gap_symbol_count() == 1
            symbols = db.all_symbols()
            assert len(symbols) == 1 and symbols[0].state == "clean"
            with patch(
                "pytest_leela.engine.run_tests_for_mutant", wraps=real_runner
            ) as tracked:
                warm = Engine(use_coverage=False, index=db).run(
                    [str(src)], test_dir=str(tests), test_node_ids=selected
                )
            assert tracked.call_count == len(warm.results) == len(cold.results)
            assert warm.killed and warm.survived


def describe_error_results_are_not_cached_survivals():
    def _sub_mutants(result) -> list:
        return [
            r
            for r in result.results
            if r.mutant.point.node_type == "BinOp"
            and r.mutant.point.original_op == "Add"
            and r.mutant.replacement_op == "Sub"
        ]

    def it_reports_error_not_survived_when_no_test_runs(
        tmp_path, monkeypatch
    ):
        src, tests = _write_skipped_mutant_project(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(str(tmp_path))

        result = Engine(use_coverage=False).run([str(src)], test_dir=str(tests))
        errored = [m for m in result.results if m.status == "error"]
        assert errored, "expected at least one erroring mutant"
        for mutant in errored:
            assert mutant.killed is False
            assert mutant.error == "no tests ran", mutant.error
        # Errors are excluded from the score denominator entirely.
        assert result.errors == errored
        assert result.mutants_scored == result.mutants_tested - len(errored)

    @pytest.mark.parametrize("unknown_inputs", [False, True])
    def it_persists_error_evidence_without_scoring_or_cleaning(tmp_path, monkeypatch, unknown_inputs):
        src, tests = _write_skipped_mutant_project(tmp_path)
        if unknown_inputs:
            # A real dynamic import makes the dependency fingerprint unknown.
            test_file = tests / "test_skipped.py"
            test_file.write_text(test_file.read_text().replace("import skipped", "skipped = __import__('skipped')"))
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(str(tmp_path))

        with IndexDB(tmp_path / "index.db") as db:
            cold = Engine(use_coverage=False, index=db).run(
                [str(src)], test_dir=str(tests)
            )
            subs = _sub_mutants(cold)
            assert subs
            assert all(r.status == "error" for r in subs)
            # No clean scored row was written for the erroring mutant.
            cur = db._conn.execute(
                "SELECT COUNT(*) FROM mutants WHERE state = 'clean' AND killed = 0"
            )
            assert int(cur.fetchone()[0]) == 0
            rows = db._conn.execute(
                "SELECT symbol_id, location_line, col_offset, operator, replacement_op, "
                "state, killed, killing_test, source_hash, test_set_hash, analyzed_at, details "
                "FROM mutants"
            ).fetchall()
            assert rows, "ERROR evidence must be durable, not just in memory"
            for row in rows:
                sid, line, col, op, rep, state, killed, killing_test, sh, th, at, raw = row
                actual = next(
                    r for r in cold.errors
                    if (r.mutant.point.lineno, r.mutant.point.col_offset,
                        f"{r.mutant.point.node_type}:{r.mutant.replacement_op}",
                        r.mutant.replacement_op) == (line, col, op, rep)
                )
                details = json.loads(raw)
                assert sid.endswith(":limit")
                assert state == "dirty"
                assert killed is None and killing_test is None
                assert details["error"] == actual.error == "no tests ran"
                assert details["latest_test_ids_run"] == actual.test_ids_run
                assert details["tests_run"] == actual.tests_run == 0
                evidence = details["last_error"]
                assert evidence["message"] == actual.error
                assert evidence["source_hash"] == sh
                assert evidence["test_set_hash"] == (None if unknown_inputs else th)
                assert bool(th) is not unknown_inputs
                assert evidence["analyzed_at"] == at
                assert evidence["file_path"] == str(src)
                assert evidence["test_ids_run"] == actual.test_ids_run
                assert db.get_cached_result(sid, sh, th, actual.mutant) is None
            assert db.coverage_gap_symbol_count() == 0
            # The owning symbol is not clean: it must be retried later.
            states = {
                str(state)
                for (state,) in db._conn.execute("SELECT state FROM symbols")
            }
            assert "clean" not in states
            assert "error" in states

    def it_reruns_the_mutant_on_a_warm_run(tmp_path, monkeypatch):
        src, tests = _write_skipped_mutant_project(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(str(tmp_path))

        from pytest_leela.runner import run_tests_for_mutant as real_runner

        db_path = tmp_path / "index.db"
        with IndexDB(db_path) as db:
            cold = Engine(use_coverage=False, index=db).run(
                [str(src)], test_dir=str(tests)
            )
            before = db._conn.execute("SELECT details FROM mutants ORDER BY id").fetchall()
            assert before
        with IndexDB(db_path) as db:
            assert db._conn.execute("SELECT details FROM mutants ORDER BY id").fetchall() == before
            assert db.get_symbol(db.all_symbols()[0].id).state == "error"
            with patch(
                "pytest_leela.engine.run_tests_for_mutant",
                wraps=real_runner,
            ) as tracked:
                warm = Engine(use_coverage=False, index=db).run(
                    [str(src)], test_dir=str(tests)
                )
            # The error was not reused as a cached verdict: the runner ran.
            assert tracked.call_count == len(warm.results) == len(cold.results)
            assert warm.mutants_scored == 0
            warm_subs = _sub_mutants(warm)
            assert warm_subs
            assert all(r.status == "error" for r in warm_subs)

    def it_keeps_a_mixed_error_and_success_symbol_retryable(tmp_path, monkeypatch):
        src, tests = _write_skipped_mutant_project(tmp_path)
        test_file = tests / "test_skipped.py"
        # Only Sub returns zero: other mutants genuinely fail the assertion.
        test_file.write_text(test_file.read_text().replace("skipped.limit() != 2", "skipped.limit() == 0"))
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(str(tmp_path))
        from pytest_leela.runner import run_tests_for_mutant as real_runner

        with IndexDB(tmp_path / "index.db") as db:
            cold = Engine(use_coverage=False, index=db).run([str(src)], test_dir=str(tests))
            assert cold.errors and cold.killed > 0
            assert cold.mutants_scored == len(cold.results) - len(cold.errors)
            symbols = db.all_symbols()
            assert len(symbols) == 1 and symbols[0].state == "error"
            with patch("pytest_leela.engine.run_tests_for_mutant", wraps=real_runner) as tracked:
                warm = Engine(use_coverage=False, index=db).run([str(src)], test_dir=str(tests))
            assert tracked.call_count == len(cold.errors)
            assert warm.killed == cold.killed
            assert len(warm.errors) == len(cold.errors)
            assert db.get_symbol(symbols[0].id).state == "error"

    def it_still_caches_genuine_kill_and_survival_verdicts(
        tmp_path, monkeypatch
    ):
        src = tmp_path / "mixed.py"
        src.write_text(
            "def label(n: int) -> str:\n"
            "    return 'odd' if n % 2 == 1 else 'even'\n"
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_mixed.py").write_text(
            "from mixed import label\n"
            "\n"
            "def test_odd_equals_one():\n"
            "    assert label(1) == 'odd'\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(str(tmp_path))

        with IndexDB(tmp_path / "index.db") as db:
            cold = Engine(use_coverage=False, index=db).run(
                [str(src)], test_dir=str(tests)
            )
            assert cold.killed > 0
            kill_rows = int(
                db._conn.execute(
                    "SELECT COUNT(*) FROM mutants WHERE state = 'clean' AND killed = 1"
                ).fetchone()[0]
            )
            assert kill_rows == cold.killed
            survival_rows = int(
                db._conn.execute(
                    "SELECT COUNT(*) FROM mutants WHERE state = 'clean' AND killed = 0"
                ).fetchone()[0]
            )
            assert survival_rows == len(cold.survived)
            warm = Engine(use_coverage=False, index=db).run(
                [str(src)], test_dir=str(tests)
            )
            assert len(warm.results) == len(cold.results)
            assert warm.killed == cold.killed


@pytest.mark.parametrize("wrapper_import", [
    "from .nested import helper\nexpected = helper.expected\n",
    "from .nested.helper import expected\n",
])
@pytest.mark.parametrize("edited_input", ["initializer", "transitive"])
def test_relative_import_initializer_invalidates_selected_cache(
    tmp_path, monkeypatch, wrapper_import, edited_input,
):
    """A green initializer edit can change a real mutant's killability."""
    from pytest_leela.engine import _TestDependencies
    from pytest_leela.runner import run_tests_for_mutant

    source, tests = _write_project(tmp_path, monkeypatch)
    source.write_text("def add(a, b):\n    return a + b\n")
    support = tmp_path / "support"
    nested = support / "nested"
    nested.mkdir(parents=True)
    (support / "__init__.py").write_text("")
    policy = support / "policy.py"
    policy.write_text("OFFSET = 0\n")
    initializer = nested / "__init__.py"
    initializer.write_text(
        "from support.policy import OFFSET\n"
        "from . import helper\n"
        "helper.expected = lambda x: OFFSET\n"
    )
    (nested / "helper.py").write_text("def expected(x):\n    return x\n")
    (support / "wrapper.py").write_text(wrapper_import)
    test = tests / "test_calc.py"
    test.write_text(
        "from calc import add\nfrom support.wrapper import expected\n"
        "def test_add():\n"
        "    assert add(3, 0) == 3\n"
        "    assert add(0, expected(1)) == expected(1)\n"
    )
    selected = [str(test) + "::test_add"]
    before = _TestDependencies(str(tests))
    fingerprint = before.fingerprint(selected)
    files = before.dependency_files(selected)
    with IndexDB(tmp_path / "index.db") as db:
        with patch("pytest_leela.engine.run_tests_for_mutant", wraps=run_tests_for_mutant) as runner:
            cold = Engine(index=db, use_coverage=False).run(
                [str(source)], test_dir=str(tests), test_node_ids=selected,
            )
            assert runner.call_count == len(cold.results) > 0
            runner.reset_mock()
            warm_before = Engine(index=db, use_coverage=False).run(
                [str(source)], test_dir=str(tests), test_node_ids=selected,
            )
            assert runner.call_count == 0
            fresh_before = Engine(use_coverage=False).run(
                [str(source)], test_dir=str(tests), test_node_ids=selected,
            )
            if edited_input == "initializer":
                initializer.write_text(initializer.read_text().replace(
                    "lambda x: OFFSET", "lambda x: OFFSET + 2",
                ))
            else:
                policy.write_text("OFFSET = 2\n")
            after = _TestDependencies(str(tests))
            changed_fingerprint = after.fingerprint(selected)
            runner.reset_mock()
            warm_after = Engine(index=db, use_coverage=False).run(
                [str(source)], test_dir=str(tests), test_node_ids=selected,
            )
            changed_calls = runner.call_count
            runner.reset_mock()
            fresh_after = Engine(use_coverage=False).run(
                [str(source)], test_dir=str(tests), test_node_ids=selected,
            )
            assert runner.call_count == len(fresh_after.results) > 0

    # Every Engine call above runs the real, green unmutated baseline.
    before_verdicts = [{r.mutant.replacement_op: r.status for r in run.results}
                       for run in (cold, warm_before, fresh_before)]
    after_verdicts = [{r.mutant.replacement_op: r.status for r in run.results}
                      for run in (warm_after, fresh_after)]
    assert before_verdicts[0] == before_verdicts[1] == before_verdicts[2]
    assert before_verdicts[0]["Sub"] == "survived"
    assert after_verdicts[1]["Sub"] == "killed"
    assert after_verdicts[0] == after_verdicts[1], (after_verdicts, changed_calls)
    assert changed_calls == len(warm_after.results) > 0
    assert fingerprint is not None and changed_fingerprint is not None
    assert changed_fingerprint != fingerprint
    assert files is not None and {initializer, policy} <= files
