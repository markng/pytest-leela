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
        """Even cache hits must participate in the final symbol-clean pass."""

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
        """Add a new function in the same file and index its results."""

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

        src, tests = _write_project(tmp_path)
        test = tests / "test_calc.py"
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

        src, tests = _write_project(tmp_path)
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
        import pytest_leela.engine as engine_module
        from pytest_leela.models import MutantResult

        src, tests = _write_project(tmp_path)
        calls = []

        def runner(mutant, *args, **kwargs):
            calls.append(mutant)
            return MutantResult(mutant, False, 1, None, 0)

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", runner)
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db)
            for _ in range(2):
                result = engine.run([str(src)])
            assert len(calls) == 2 * result.mutants_tested
            assert db._conn.execute("SELECT COUNT(*) FROM mutants").fetchone()[0] == 0
            original = Path.read_bytes

            def unreadable(path):
                if path == tests / "test_calc.py":
                    raise PermissionError("unreadable test")
                return original(path)

            monkeypatch.setattr(Path, "read_bytes", unreadable)
            for _ in range(2):
                engine.run([str(src)], test_dir=str(tests))
            assert len(calls) == 4 * result.mutants_tested
            assert db._conn.execute("SELECT COUNT(*) FROM mutants").fetchone()[0] == 0


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

    def it_bypasses_dynamic_missing_ambiguous_and_out_of_scope_dependencies(tmp_path, monkeypatch):
        import pytest_leela.engine as engine_module
        from pytest_leela.models import MutantResult

        src, tests = _write_project(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        test = tests / "test_calc.py"
        calls = []
        events = []

        def runner(mutant, *args, **kwargs):
            calls.append(mutant)
            return MutantResult(mutant, False, 1, None, 0)

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", runner)
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db, on_progress=events.append)
            variants = [
                "import importlib\nhelper = importlib.import_module('dynamic_helper')\n",
                "import missing_local_helper\n",
            ]
            (tests / "ambiguous.py").write_text("x = 1\n")
            (tmp_path / "ambiguous.py").write_text("x = 2\n")
            variants.append("import ambiguous\n")
            for content in variants:
                test.write_text(content)
                before = len(calls)
                for _ in range(2):
                    events.clear()
                    result = engine.run([str(src)], test_dir=str(tests), test_node_ids=[f"{test}::test_add"])
                    assert "cache-hit" not in {e.status for e in events}
                assert len(calls) - before == 2 * result.mutants_tested
            outside = tmp_path / "test_external.py"
            outside.write_text("# outside explicit helper scope\n")
            engine.run([str(src)], test_dir=str(tests), test_node_ids=[str(outside)])
            engine.run([str(src)], test_dir=str(tmp_path / "missing-tests"))
            engine.run([str(src)], test_dir=str(tests), test_node_ids=[str(tests / "deleted.py")])
            assert db._conn.execute("SELECT COUNT(*) FROM mutants").fetchone()[0] == 0

    def it_invalidates_persisted_results_when_environment_identity_changes(tmp_path, monkeypatch):
        import pytest_leela.engine as engine_module
        from pytest_leela.models import MutantResult

        src, tests = _write_project(tmp_path)
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
    def it_emits_done_without_symbol_events_when_no_mutants_exist(tmp_path):
        src = tmp_path / "empty.py"
        src.write_text("# no mutants\n")
        events = []
        result = Engine(use_coverage=False, on_progress=events.append).run([str(src)])
        assert result.mutants_tested == 0
        assert [event.kind for event in events] == ["done"]
