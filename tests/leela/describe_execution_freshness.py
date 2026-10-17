"""Deterministic bytecode-freshness regressions for mutation execution.

CPython and pytest's assertion rewriter both reuse a cached ``.pyc`` while the
header's recorded ``(int(source_mtime), size)`` still matches the source.  A
same-size edit that leaves the precise source mtime unchanged — or moves it
backward (a restored copy, a rewind) — keeps that header valid, so the pre-edit
bytecode is executed even though the source changed.  A ``.pyc`` stores no
content hash, so a cache whose contents cannot be positively tied to the live
source is not trustworthy and its derived artifacts must be removed before the
selected tests run.

These tests pin mtimes only to *construct* the adversarial cached input; the
repair must succeed without advancing a fixture mtime, changing file size, or
disabling bytecode.  They cover the cases the previous mtime heuristic missed:
unchanged precise mtime, backward timestamps, a pre-existing stale helper and
pre-existing stale fixture artifacts, sibling caches that must survive, a
wholly-cached run whose origins are never touched, and fail-closed behavior when
a needed artifact cannot be removed.
"""

from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path
from unittest.mock import patch

from pytest_leela.engine import Engine
from pytest_leela.index import IndexDB


def _pytest_tag() -> str:
    from _pytest.assertion.rewrite import PYTEST_TAG

    return PYTEST_TAG


def _cached(source: Path) -> list[Path]:
    """Both exact artifacts the active loaders cache for *source*."""
    cpython_pyc = Path(importlib.util.cache_from_source(str(source)))
    stem = source.name[:-3]
    pytest_pyc = cpython_pyc.parent / f"{stem}.{_pytest_tag()}.pyc"
    return [p for p in (cpython_pyc, pytest_pyc) if p.exists()]


def _cold_run(engine: Engine, src: Path, tests: Path, **kw) -> object:
    return engine.run([str(src)], test_dir=str(tests), **kw)


def describe_execution_freshness():
    def it_recompiles_a_same_size_fixture_edit_within_one_second(tmp_path, monkeypatch):
        src = tmp_path / "calc.py"
        src.write_text("def is_positive(x: int) -> bool:\n    return x > 0\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        conftest = tests / "conftest.py"
        conftest.write_text("import pytest\n@pytest.fixture\ndef sample():\n    return 1\n")
        (tests / "test_calc.py").write_text(
            "from calc import is_positive\n"
            "def test_positive(sample):\n"
            "    assert is_positive(sample) is (sample > 0)\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        frozen = int(time.time()) - 10
        os.utime(conftest, (frozen, frozen))
        events = []
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db, on_progress=events.append)
            cold = _cold_run(engine, src, tests)
            assert not next(r for r in cold.results if r.mutant.replacement_op == "GtE").killed
            for pyc in _cached(conftest):
                os.utime(pyc, (frozen, frozen))
            conftest.write_text("import pytest\n@pytest.fixture\ndef sample():\n    return 0\n")
            os.utime(conftest, (frozen + 0.5, frozen + 0.5))
            events.clear()
            changed = _cold_run(engine, src, tests)
        assert next(r for r in changed.results if r.mutant.replacement_op == "GtE").killed
        assert "cache-hit" not in {e.status for e in events}

    def it_recompiles_when_the_precise_source_mtime_is_unchanged(tmp_path, monkeypatch):
        src = tmp_path / "calc.py"
        src.write_text("def is_positive(x: int) -> bool:\n    return x > 0\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        helper = tests / "inputs.py"
        helper.write_text("def values():\n    return [1]\n")
        (tests / "test_calc.py").write_text(
            "import inputs\n"
            "from calc import is_positive\n"
            "def test_positive():\n"
            "    for x in inputs.values():\n"
            "        assert is_positive(x) is (x > 0)\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db)
            cold = _cold_run(engine, src, tests)
            assert not next(r for r in cold.results if r.mutant.replacement_op == "GtE").killed
            # Restore the helper's precise mtime across a same-size edit, so the
            # cached .pyc header (mtime, size) still matches a stale artifact.
            stat_ns = helper.stat().st_mtime_ns
            helper.write_text("def values():\n    return [0]\n")
            os.utime(helper, ns=(stat_ns, stat_ns))
            assert helper.stat().st_size == len("def values():\n    return [1]\n")
            changed = _cold_run(engine, src, tests)
        assert next(r for r in changed.results if r.mutant.replacement_op == "GtE").killed

    def it_recompiles_when_the_source_mtime_moves_backward(tmp_path, monkeypatch):
        src = tmp_path / "calc.py"
        src.write_text("def is_positive(x: int) -> bool:\n    return x > 0\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        helper = tests / "inputs.py"
        helper.write_text("def values():\n    return [1]\n")
        (tests / "test_calc.py").write_text(
            "import inputs\n"
            "from calc import is_positive\n"
            "def test_positive():\n"
            "    for x in inputs.values():\n"
            "        assert is_positive(x) is (x > 0)\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db)
            cold = _cold_run(engine, src, tests)
            assert not next(r for r in cold.results if r.mutant.replacement_op == "GtE").killed
            helper.write_text("def values():\n    return [0]\n")
            older = helper.stat().st_mtime - 120  # restored/rewound copy
            os.utime(helper, (older, older))
            changed = _cold_run(engine, src, tests)
        assert next(r for r in changed.results if r.mutant.replacement_op == "GtE").killed

    def it_prepares_only_exact_selected_artifacts_not_same_prefix_siblings(
        tmp_path,
    ):
        import py_compile

        from pytest_leela.runner import _prepare_fresh_dependencies

        selected = tmp_path / "inputs.py"
        sibling = tmp_path / "inputs_helper.py"
        selected.write_text("def values():\n    return [1]\n")
        sibling.write_text("def unused():\n    return 0\n")
        selected_stat = (selected.stat().st_mtime_ns, selected.read_bytes())
        sibling_stat = (sibling.stat().st_mtime_ns, sibling.read_bytes())
        selected_pyc = Path(py_compile.compile(str(selected), doraise=True))
        sibling_pyc = Path(py_compile.compile(str(sibling), doraise=True))
        assert selected_pyc.stat().st_size > 16
        sibling_cache = (sibling_pyc.stat().st_mtime_ns, sibling_pyc.read_bytes())

        prepared: set[Path] = set()
        _prepare_fresh_dependencies({selected}, prepared)

        assert prepared == {selected}
        assert not selected_pyc.exists()
        assert sibling_pyc.exists()
        assert (sibling_pyc.stat().st_mtime_ns, sibling_pyc.read_bytes()) == sibling_cache
        assert (selected.stat().st_mtime_ns, selected.read_bytes()) == selected_stat
        assert (sibling.stat().st_mtime_ns, sibling.read_bytes()) == sibling_stat

    def it_runs_a_real_baseline_but_no_mutant_preparation_on_warm_cache_hits(
        tmp_path, monkeypatch
    ):
        import pytest
        import pytest_leela.engine as engine_module
        from pytest_leela.engine import BaselineFailure

        src = tmp_path / "calc.py"
        src.write_text("def is_positive(x: int) -> bool:\n    return x > 0\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        helper = tests / "inputs.py"
        helper.write_text("def expected():\n    return 1\n")
        test = tests / "test_calc.py"
        test.write_text(
            "import inputs\n"
            "from calc import is_positive\n"
            "def test_positive():\n"
            "    assert is_positive(1) is bool(inputs.expected())\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        events = []
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db, on_progress=events.append)
            _cold_run(engine, src, tests)
            source_snapshots = {
                path: (path.stat().st_mtime_ns, path.read_bytes())
                for path in (src, helper, test)
            }
            events.clear()
            with (
                patch(
                    "pytest_leela.engine.run_baseline",
                    wraps=engine_module.run_baseline,
                ) as real_baseline,
                patch("pytest_leela.engine.run_tests_for_mutant") as mutant_runner,
                patch(
                    "pytest_leela.runner._prepare_fresh_dependencies"
                ) as mutation_preparer,
            ):
                warm = _cold_run(engine, src, tests)
            real_baseline.assert_called_once()
            mutant_runner.assert_not_called()
            mutation_preparer.assert_not_called()
            assert {e.status for e in events if e.kind == "mutant"} == {"cache-hit"}
            assert warm.mutants_tested == len(warm.results)
            for path, before in source_snapshots.items():
                assert (path.stat().st_mtime_ns, path.read_bytes()) == before

            # Cached results never bypass the required baseline. A same-size,
            # same-mtime edit that makes the tests red aborts before cache hits
            # or mutant execution can be accepted.
            before = helper.stat()
            helper.write_text("def expected():\n    return 0\n")
            os.utime(helper, ns=(before.st_atime_ns, before.st_mtime_ns))
            assert helper.stat().st_size == before.st_size
            events.clear()
            with (
                patch("pytest_leela.engine.run_tests_for_mutant") as mutant_runner,
                patch(
                    "pytest_leela.runner._prepare_fresh_dependencies"
                ) as mutation_preparer,
                pytest.raises(BaselineFailure, match="no mutants were run"),
            ):
                _cold_run(engine, src, tests)
            mutant_runner.assert_not_called()
            mutation_preparer.assert_not_called()
            assert events == []

    def it_attributes_exact_cache_removal_failure_to_the_active_mutant(
        tmp_path, monkeypatch
    ):
        import py_compile
        import pytest
        import pytest_leela.engine as engine_module

        src = tmp_path / "calc.py"
        src.write_text("def is_positive(x: int) -> bool:\n    return x > 0\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        helper = tests / "inputs.py"
        helper.write_text("def values():\n    return [1]\n")
        (tests / "test_calc.py").write_text(
            "import inputs\n"
            "from calc import is_positive\n"
            "def test_positive():\n"
            "    for x in inputs.values():\n"
            "        assert is_positive(x) is (x > 0)\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        real_runner = engine_module.run_tests_for_mutant
        events = []
        cache: Path | None = None

        def failing_runner(*args, **kwargs):
            nonlocal cache
            cache = Path(py_compile.compile(str(helper), doraise=True))
            real_unlink = Path.unlink

            def fail_exact(path, *unlink_args, **unlink_kwargs):
                if path == cache:
                    raise PermissionError("cache unavailable")
                return real_unlink(path, *unlink_args, **unlink_kwargs)

            with patch.object(Path, "unlink", fail_exact):
                return real_runner(*args, **kwargs)

        monkeypatch.setattr(engine_module, "run_tests_for_mutant", failing_runner)
        with IndexDB(tmp_path / "index.db") as db:
            with pytest.raises(PermissionError, match="cache unavailable"):
                Engine(
                    use_coverage=False,
                    index=db,
                    on_progress=events.append,
                ).run([str(src)], test_dir=str(tests))
            assert cache is not None and cache.exists() and cache.stat().st_size > 16
            symbol_id = str(db._conn.execute("SELECT id FROM symbols").fetchone()[0])
            assert db.get_state(symbol_id) == "error"
            assert db._conn.execute("SELECT COUNT(*) FROM mutants").fetchone()[0] == 0
        assert [event.kind for event in events] == ["symbol-start", "mutant"]
        assert events[-1].status == "error"
        assert events[-1].symbol_id == symbol_id

    def it_recompiles_a_same_size_fixture_edit_for_node_id_selection(tmp_path, monkeypatch):
        src = tmp_path / "calc.py"
        src.write_text("def is_positive(x: int) -> bool:\n    return x > 0\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        conftest = tests / "conftest.py"
        conftest.write_text("import pytest\n@pytest.fixture\ndef sample():\n    return 1\n")
        test = tests / "test_calc.py"
        test.write_text(
            "from calc import is_positive\n"
            "def test_positive(sample):\n"
            "    assert is_positive(sample) is (sample > 0)\n"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        engine = Engine(use_coverage=False)
        selection = [f"{test}::test_positive"]
        cold = engine.run([str(src)], test_node_ids=selection)
        assert not next(r for r in cold.results if r.mutant.replacement_op == "GtE").killed
        conftest.write_text("import pytest\n@pytest.fixture\ndef sample():\n    return 0\n")
        changed = engine.run([str(src)], test_node_ids=selection)
        assert next(r for r in changed.results if r.mutant.replacement_op == "GtE").killed