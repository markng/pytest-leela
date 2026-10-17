"""Status output when a watcher commits between the summary and detail reads."""

import importlib
import json

import pytest


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
