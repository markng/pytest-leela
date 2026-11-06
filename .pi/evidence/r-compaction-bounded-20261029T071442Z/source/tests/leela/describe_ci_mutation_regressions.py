"""Observable boundaries missed by the native CI self-mutation selection."""

import io
import sqlite3

import pytest

from pytest_leela.daemon import ChangeEvent, LeelaDaemon
from pytest_leela.engine import Engine, _TestDependencies
from pytest_leela.index import IndexDB
from pytest_leela.models import EngineProgress
from pytest_leela.plugin import _MutationProgressReporter
from tests.leela.describe_index import V1_SCHEMA


def test_no_test_directory_is_known_empty(tmp_path):
    daemon = LeelaDaemon(tmp_path)
    assert daemon._collect_test_files() == set()


def test_ancestor_pytest_configuration_is_not_owned_by_watcher(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    daemon = LeelaDaemon(project)
    original_paths = daemon._contract_paths.copy()
    assert daemon._collect_test_files() == set()
    assert daemon._contract_paths == original_paths


@pytest.mark.parametrize("replacement, changed", [
    ("", True),
    ("def value():\n    return 2\n", True),
    ("def value():\n    return 1\n", False),
])
def test_watch_loop_schedules_only_symbol_changes(tmp_path, monkeypatch, replacement, changed):
    src = tmp_path / "src"
    src.mkdir()
    source = src / "value.py"
    source.write_text("def value():\n    return 1\n")
    daemon = LeelaDaemon(tmp_path, out=io.StringIO())
    with IndexDB(tmp_path / "index.db") as db:
        daemon._initial_build(db)
        source.write_text(replacement)
        reconcile = db.reconcile_file(str(source), replacement)
        events = [ChangeEvent(str(source), "modified", reconcile)]
        daemon._last_change_at = None
        monkeypatch.setattr(daemon, "_scan_for_changes", lambda db: events)
        monkeypatch.setattr(daemon._stop, "wait", lambda interval: True)
        monkeypatch.setattr(daemon, "_maybe_spawn_reanalysis", lambda db: None)
        daemon._loop(db)
        assert (daemon._last_change_at is not None) == changed


def test_dependency_resolution_includes_test_root_parent(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    dependency = tmp_path / "ci_gap_dependency.py"
    dependency.write_text("")
    test = tests / "test_import.py"
    test.write_text("import ci_gap_dependency\n")
    dependencies = _TestDependencies(str(tests))
    assert dependencies.dependency_files([str(test)]) == {test, dependency}


def test_relative_child_scan_includes_executed_package_initializer(tmp_path):
    initializer = tmp_path / "__init__.py"
    initializer.write_text("")
    child = tmp_path / "child.py"
    child.write_text("")
    consumer = tmp_path / "consumer.py"
    consumer.write_text("from . import child\n")
    dependencies = _TestDependencies(str(tmp_path))
    assert dependencies._scan(consumer) == {initializer, child}


def test_existing_test_outside_root_is_not_cacheable(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("")
    dependencies = _TestDependencies(str(tests))
    assert dependencies.dependency_files([str(outside)]) is None
    assert dependencies.fingerprint([str(outside)]) is None


def test_diff_with_only_module_level_change_leaves_symbols_dirty(tmp_path, monkeypatch):
    target = tmp_path / "module.py"
    source = "VALUE = 2\ndef value():\n    return VALUE\n"
    target.write_text(source)
    monkeypatch.setattr("pytest_leela.engine.changed_lines", lambda base: {str(target): {1}})
    with IndexDB(tmp_path / "index.db") as db:
        db.reconcile_file(str(target), source)
        before = db.get_dirty_symbols()
        result = Engine(index=db, use_coverage=False).run([str(target)], diff_base="main")
        assert result.results == []
        assert db.get_dirty_symbols() == before


def test_current_schema_reopen_preserves_existing_rows(tmp_path):
    with IndexDB(tmp_path / "index.db") as db:
        db.reconcile_file("module.py", "def value():\n    return 1\n")
        before = db.all_symbols()
    with IndexDB(tmp_path / "index.db") as reopened:
        assert reopened.all_symbols() == before
        assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_interrupted_migration_rolls_back_before_propagating(tmp_path, monkeypatch):
    connection = sqlite3.connect(tmp_path / "legacy.db", isolation_level=None)
    connection.executescript(V1_SCHEMA)
    connection.execute("PRAGMA user_version = 1")

    def interrupt(self):
        self._execute("DELETE FROM symbols")
        raise KeyboardInterrupt("migration interrupted")

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connection)
    monkeypatch.setattr(IndexDB, "_migrate_v1_to_v2", interrupt)
    with pytest.raises(KeyboardInterrupt, match="migration interrupted"):
        IndexDB(tmp_path / "legacy.db")
    assert connection.in_transaction is False
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    connection.close()


@pytest.mark.parametrize("count, label", [(None, "all"), (0, "0"), (3, "3")])
def test_progress_reports_actual_selection_size(count, label):
    output = io.StringIO()
    reporter = _MutationProgressReporter(output)
    reporter(EngineProgress(
        kind="mutant-phase", phase="mutant", ordinal=1, total=2,
        file_path="module.py", lineno=4, op="Compare:NotEq", selected_tests=count,
    ))
    assert output.getvalue() == (
        f"leela: mutant 1/2 mutant module.py:4 Compare:NotEq tests={label} timeout=none\n"
    )
