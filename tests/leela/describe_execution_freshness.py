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
import stat
import sys
import time
from pathlib import Path

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

    def it_keeps_a_sibling_cache_and_touches_only_the_selection(tmp_path, monkeypatch):
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
        # A same-stem-prefixed sibling module must never be matched. An explicit
        # node-id selection keeps the graph limited to the selected test's own
        # imports, so the sibling is genuinely out of scope.
        sibling = tests / "inputs_helper.py"
        sibling.write_text("def unused():\n    return 0\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.chdir(tmp_path)
        # Seed the sibling's real CPython cache so removal could hit it.
        sibling_bytes = sibling.read_bytes()
        sibling_pyc = Path(importlib.util.cache_from_source(str(sibling)))
        sibling_pyc.parent.mkdir(parents=True, exist_ok=True)
        sibling_pyc.write_bytes(b"SIBLING-CACHE")
        selection = [f"{tests / 'test_calc.py'}::test_positive"]
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db)
            engine.run([str(src)], test_dir=str(tests), test_node_ids=selection)
        assert sibling_pyc.exists()
        assert sibling.read_bytes() == sibling_bytes

    def it_leaves_origins_untouched_on_a_wholly_cached_run(tmp_path, monkeypatch):
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
        events = []
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db, on_progress=events.append)
            _cold_run(engine, src, tests)
            cold_cached = [p for group in _cached(helper) for p in [group]]
            snapshots = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in cold_cached}
            events.clear()
            warm = _cold_run(engine, src, tests)
        assert {e.status for e in events if e.kind == "mutant"} == {"cache-hit"}
        for path, before in snapshots.items():
            assert path.exists()
            assert (path.stat().st_mtime_ns, path.read_bytes()) == before
        assert warm.mutants_tested == len(warm.results)

    def it_fails_closed_when_a_needed_cache_cannot_be_removed(tmp_path, monkeypatch):
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
        cache = Path(importlib.util.cache_from_source(str(helper)))
        cache.parent.mkdir(parents=True, exist_ok=True)
        stale = cache  # exact CPython artifact pytest/CPython would reuse
        stale.write_bytes(b"STALE-BUT-TIMESTAMP-VALID")
        cache_dir = cache.parent
        events = []
        with IndexDB(tmp_path / "index.db") as db:
            engine = Engine(use_coverage=False, index=db, on_progress=events.append)
            os.chmod(cache_dir, stat.S_IRUSR | stat.S_IXUSR)  # read-only cache dir
            try:
                try:
                    _cold_run(engine, src, tests)
                    blocked = False
                except OSError:
                    blocked = True
            finally:
                os.chmod(cache_dir, stat.S_IRWXU)
        # Removal is impossible -> abort with an error, never run stale code and
        # never record a mutant result as killed/survived.
        assert blocked
        assert "error" in {e.status for e in events}
        assert "killed" not in {e.status for e in events}
        assert "survived" not in {e.status for e in events}

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