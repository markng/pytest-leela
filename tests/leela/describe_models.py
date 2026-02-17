"""Tests for pytest_leela.models — data models for mutation testing."""

from pytest_leela.models import (
    Mutant,
    MutantResult,
    MutationPoint,
    RunResult,
)


def _make_point(**overrides) -> MutationPoint:
    defaults = dict(
        file_path="test.py",
        module_name="test",
        lineno=1,
        col_offset=0,
        node_type="BinOp",
        original_op="Add",
        inferred_type=None,
    )
    defaults.update(overrides)
    return MutationPoint(**defaults)


def _make_result(killed: bool, mutant_id: int = 1) -> MutantResult:
    point = _make_point()
    mutant = Mutant(point=point, replacement_op="Sub", mutant_id=mutant_id)
    return MutantResult(
        mutant=mutant,
        killed=killed,
        tests_run=3,
        killing_test="test_foo" if killed else None,
        time_seconds=0.1,
    )


def describe_run_result():
    def describe_killed():
        def it_counts_killed_mutants():
            results = [_make_result(True, 1), _make_result(False, 2), _make_result(True, 3)]
            run = RunResult(
                target_files=["test.py"],
                total_mutants=3,
                mutants_tested=3,
                mutants_pruned=0,
                results=results,
                wall_time_seconds=1.0,
            )
            assert run.killed == 2

        def it_returns_zero_when_no_results():
            run = RunResult(
                target_files=[],
                total_mutants=0,
                mutants_tested=0,
                mutants_pruned=0,
                results=[],
                wall_time_seconds=0.0,
            )
            assert run.killed == 0

    def describe_survived():
        def it_returns_only_surviving_mutants():
            survived_result = _make_result(False, 1)
            results = [_make_result(True, 2), survived_result, _make_result(True, 3)]
            run = RunResult(
                target_files=["test.py"],
                total_mutants=3,
                mutants_tested=3,
                mutants_pruned=0,
                results=results,
                wall_time_seconds=1.0,
            )
            assert run.survived == [survived_result]

        def it_returns_empty_list_when_all_killed():
            results = [_make_result(True, 1), _make_result(True, 2)]
            run = RunResult(
                target_files=["test.py"],
                total_mutants=2,
                mutants_tested=2,
                mutants_pruned=0,
                results=results,
                wall_time_seconds=1.0,
            )
            assert run.survived == []

    def describe_mutation_score():
        def it_calculates_percentage_of_killed():
            results = [_make_result(True, 1), _make_result(False, 2)]
            run = RunResult(
                target_files=["test.py"],
                total_mutants=2,
                mutants_tested=2,
                mutants_pruned=0,
                results=results,
                wall_time_seconds=1.0,
            )
            assert run.mutation_score == 50.0

        def it_returns_zero_when_no_mutants_tested():
            run = RunResult(
                target_files=[],
                total_mutants=0,
                mutants_tested=0,
                mutants_pruned=0,
                results=[],
                wall_time_seconds=0.0,
            )
            assert run.mutation_score == 0.0

        def it_returns_100_when_all_killed():
            results = [_make_result(True, 1), _make_result(True, 2), _make_result(True, 3)]
            run = RunResult(
                target_files=["test.py"],
                total_mutants=3,
                mutants_tested=3,
                mutants_pruned=0,
                results=results,
                wall_time_seconds=1.0,
            )
            assert run.mutation_score == 100.0

        def it_returns_positive_value():
            """mutation_score should never be negative (kills negation mutant)."""
            results = [_make_result(True, 1), _make_result(False, 2)]
            run = RunResult(
                target_files=["test.py"],
                total_mutants=2,
                mutants_tested=2,
                mutants_pruned=0,
                results=results,
                wall_time_seconds=1.0,
            )
            assert run.mutation_score > 0
            assert run.mutation_score == 50.0
