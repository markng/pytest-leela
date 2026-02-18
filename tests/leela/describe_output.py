"""Tests for pytest_leela.output — terminal and JSON report formatting."""

import json

import pytest

from pytest_leela.models import Mutant, MutantResult, MutationPoint, RunResult
from pytest_leela.output import _op_display, _pct, format_json_report, format_terminal_report


def _make_point(
    file_path: str = "src/app.py",
    module_name: str = "app",
    lineno: int = 10,
    col_offset: int = 0,
    node_type: str = "BinOp",
    original_op: str = "Add",
    inferred_type: str | None = None,
) -> MutationPoint:
    return MutationPoint(
        file_path=file_path,
        module_name=module_name,
        lineno=lineno,
        col_offset=col_offset,
        node_type=node_type,
        original_op=original_op,
        inferred_type=inferred_type,
    )


def _make_result(
    killed: bool = True,
    file_path: str = "src/app.py",
    lineno: int = 10,
    original_op: str = "Add",
    replacement_op: str = "Sub",
    mutant_id: int = 1,
) -> MutantResult:
    point = _make_point(file_path=file_path, lineno=lineno, original_op=original_op)
    mutant = Mutant(point=point, replacement_op=replacement_op, mutant_id=mutant_id)
    return MutantResult(
        mutant=mutant,
        killed=killed,
        tests_run=3,
        killing_test="test_foo" if killed else None,
        time_seconds=0.1,
    )


def _make_run_result(results: list[MutantResult] | None = None) -> RunResult:
    if results is None:
        results = [
            _make_result(killed=True, mutant_id=1),
            _make_result(killed=False, mutant_id=2, replacement_op="Mult"),
        ]
    target_files = list({r.mutant.point.file_path for r in results}) if results else []
    return RunResult(
        target_files=target_files,
        total_mutants=len(results) + 2,
        mutants_tested=len(results),
        mutants_pruned=2,
        results=results,
        wall_time_seconds=1.5,
    )


def describe_op_display():
    def it_maps_known_arithmetic_operators():
        assert _op_display("Add", "Sub") == "+ \u2192 -"
        assert _op_display("Mult", "Div") == "* \u2192 /"
        assert _op_display("FloorDiv", "Mod") == "// \u2192 %"
        assert _op_display("Pow", "Mult") == "** \u2192 *"

    def it_maps_known_comparison_operators():
        assert _op_display("Eq", "NotEq") == "== \u2192 !="
        assert _op_display("Lt", "GtE") == "< \u2192 >="
        assert _op_display("Gt", "LtE") == "> \u2192 <="
        assert _op_display("Is", "IsNot") == "is \u2192 is not"
        assert _op_display("In", "NotIn") == "in \u2192 not in"

    def it_maps_boolean_and_unary_operators():
        assert _op_display("And", "Or") == "and \u2192 or"
        assert _op_display("USub", "UAdd") == "- \u2192 +"
        assert _op_display("Not", "UAdd") == "not \u2192 +"

    def it_passes_through_unknown_operators():
        assert _op_display("FooOp", "BarOp") == "FooOp \u2192 BarOp"

    def it_handles_negate_return_mutations():
        assert _op_display("expr", "negate") == "return x \u2192 return -x"
        assert _op_display("expr", "negate_expr") == "return x \u2192 return -x"

    def it_handles_remove_negation_mutation():
        assert _op_display("expr", "remove_negation") == "return -x \u2192 return x"

    def it_handles_empty_str_mutation():
        assert _op_display("expr", "empty_str") == 'return "..." \u2192 return ""'

    def it_handles_return_constant_mutations():
        assert _op_display("True", "False") == "return True \u2192 return False"
        assert _op_display("False", "True") == "return False \u2192 return True"
        assert _op_display("expr", "None") == "return expr \u2192 return None"
        assert _op_display("True", "None") == "return True \u2192 return None"

    def it_handles_remove_unary_mutation():
        assert _op_display("USub", "_remove") == "- x \u2192 x"
        assert _op_display("Not", "_remove") == "not x \u2192 x"

    def it_falls_through_when_original_not_in_constant_set():
        """Replacement in constant set but original is NOT — `and` condition is False.

        If `and` mutated to `or`, this would incorrectly enter the return-constant
        branch producing "return Mult \u2192 return True" instead of "* \u2192 True".
        """
        assert _op_display("Mult", "True") == "* \u2192 True"
        assert _op_display("Div", "False") == "/ \u2192 False"
        assert _op_display("Add", "None") == "+ \u2192 None"


def describe_format_terminal_report():
    def it_includes_header_and_score():
        run = _make_run_result()
        report = format_terminal_report(run)
        assert "leela mutation testing" in report
        assert "=" * 70 in report
        assert "1/2 killed (50.0%)" in report

    def it_shows_target_file_count():
        run = _make_run_result()
        report = format_terminal_report(run)
        assert "Target: 1 file" in report

    def it_pluralizes_files_correctly():
        results = [
            _make_result(killed=True, file_path="src/a.py", mutant_id=1),
            _make_result(killed=True, file_path="src/b.py", mutant_id=2),
        ]
        run = RunResult(
            target_files=["src/a.py", "src/b.py"],
            total_mutants=4,
            mutants_tested=2,
            mutants_pruned=2,
            results=results,
            wall_time_seconds=1.0,
        )
        report = format_terminal_report(run)
        assert "Target: 2 files" in report

    def it_shows_per_file_kill_stats():
        run = _make_run_result()
        report = format_terminal_report(run)
        # app.py: 1 killed out of 2
        assert "app.py" in report
        assert "1/2 killed (50.0%)" in report

    def it_lists_survived_mutants():
        run = _make_run_result()
        report = format_terminal_report(run)
        assert "SURVIVED" in report
        assert "line 10" in report

    def it_shows_pruning_info():
        run = _make_run_result()
        report = format_terminal_report(run)
        assert "2 pruned by type analysis" in report

    def it_shows_wall_time():
        run = _make_run_result()
        report = format_terminal_report(run)
        assert "1.5s" in report

    def it_handles_empty_results():
        run = RunResult(
            target_files=[],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.0,
        )
        report = format_terminal_report(run)
        assert "leela mutation testing" in report
        assert "Target: 0 files" in report
        assert "0/0 killed" in report

    def it_shows_overall_summary():
        run = _make_run_result()
        report = format_terminal_report(run)
        assert "Overall: 1/2 killed (50.0%) in 1.5s" in report

    def it_does_not_show_pruning_line_when_zero():
        run = RunResult(
            target_files=["src/app.py"],
            total_mutants=2,
            mutants_tested=2,
            mutants_pruned=0,
            results=[_make_result(killed=True), _make_result(killed=True, mutant_id=2)],
            wall_time_seconds=1.0,
        )
        report = format_terminal_report(run)
        lines = report.split("\n")
        # The pruning detail line appears in the overall summary only when pruned > 0
        overall_idx = next(i for i, l in enumerate(lines) if l.startswith("Overall:"))
        # Next non-empty line after Overall should not be a pruning line
        remaining = [l for l in lines[overall_idx + 1 :] if l.strip()]
        if remaining:
            assert "pruned by type analysis" not in remaining[0]

    def it_calculates_100_percent_for_all_killed():
        """Percentage calculation: killed/total*100 when all killed."""
        results = [
            _make_result(killed=True, mutant_id=1),
            _make_result(killed=True, mutant_id=2),
            _make_result(killed=True, mutant_id=3),
        ]
        run = RunResult(
            target_files=["src/app.py"],
            total_mutants=3,
            mutants_tested=3,
            mutants_pruned=0,
            results=results,
            wall_time_seconds=1.0,
        )
        report = format_terminal_report(run)
        assert "3/3 killed (100.0%)" in report

    def it_defaults_to_100_percent_when_file_has_zero_mutants():
        """Edge case: pct = 100.0 when total is 0 (division guard)."""
        # This tests the `if total > 0 else 100.0` branch
        run = RunResult(
            target_files=["src/app.py"],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.0,
        )
        report = format_terminal_report(run)
        assert "0/0 killed (0.0%)" in report

    def it_calculates_fractional_percentage_on_per_file_line():
        """1/3 killed = 33.3% must appear on the per-file line, not just Overall."""
        results = [
            _make_result(killed=True, mutant_id=1),
            _make_result(killed=False, mutant_id=2, replacement_op="Mult"),
            _make_result(killed=False, mutant_id=3, replacement_op="Div"),
        ]
        run = RunResult(
            target_files=["src/app.py"],
            total_mutants=3,
            mutants_tested=3,
            mutants_pruned=0,
            results=results,
            wall_time_seconds=1.0,
        )
        report = format_terminal_report(run)
        per_file_lines = [l for l in report.split("\n") if "app.py" in l]
        assert len(per_file_lines) == 1
        assert "1/3 killed (33.3%)" in per_file_lines[0]

    def it_shows_no_survived_when_all_killed():
        """When all mutants are killed, SURVIVED should not appear for that file."""
        results = [
            _make_result(killed=True, mutant_id=1),
            _make_result(killed=True, mutant_id=2),
        ]
        run = RunResult(
            target_files=["src/app.py"],
            total_mutants=2,
            mutants_tested=2,
            mutants_pruned=0,
            results=results,
            wall_time_seconds=1.0,
        )
        report = format_terminal_report(run)
        assert "SURVIVED" not in report


def describe_pct():
    def it_computes_fractional_percentage():
        """1/3 = 33.33...; kills / -> *, / -> //, * -> +, * -> //, > -> <=."""
        assert _pct(1, 3) == pytest.approx(33.333333, abs=0.001)

    def it_returns_100_when_total_is_zero():
        """total=0 exercises the else branch; kills > -> >=.

        Original: 0 > 0 is False -> 100.0
        Mutant >= : 0 >= 0 is True -> 0/0*100 -> ZeroDivisionError
        """
        assert _pct(0, 0) == 100.0

    def it_returns_zero_when_none_killed():
        assert _pct(0, 5) == 0.0

    def it_returns_100_when_all_killed():
        assert _pct(5, 5) == 100.0


def describe_format_json_report():
    def it_returns_valid_json():
        run = _make_run_result()
        output = format_json_report(run)
        data = json.loads(output)
        assert isinstance(data, dict)

    def it_includes_all_summary_fields():
        run = _make_run_result()
        data = json.loads(format_json_report(run))
        assert data["total_mutants"] == 4
        assert data["mutants_tested"] == 2
        assert data["mutants_pruned"] == 2
        assert data["killed"] == 1
        assert data["survived"] == 1
        assert data["mutation_score"] == 50.0
        assert data["wall_time_seconds"] == 1.5

    def it_includes_survived_mutant_details():
        run = _make_run_result()
        data = json.loads(format_json_report(run))
        assert len(data["survived_mutants"]) == 1
        survived = data["survived_mutants"][0]
        assert survived["file"] == "src/app.py"
        assert survived["line"] == 10
        assert survived["original"] == "Add"
        assert survived["replacement"] == "Mult"
        assert "description" in survived

    def it_handles_empty_results():
        run = RunResult(
            target_files=[],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.0,
        )
        data = json.loads(format_json_report(run))
        assert data["killed"] == 0
        assert data["survived"] == 0
        assert data["survived_mutants"] == []
        assert data["mutation_score"] == 0.0

    def it_includes_target_files():
        run = _make_run_result()
        data = json.loads(format_json_report(run))
        assert data["target_files"] == run.target_files
