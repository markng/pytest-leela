"""Tests for pytest_leela.models — data models for mutation testing."""

from pytest_leela.models import (
    CoverageMap,
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


def describe_mutant_result_new_fields():
    def it_defaults_test_ids_run_to_empty_list():
        result = _make_result(True)
        assert result.test_ids_run == []

    def it_defaults_killing_tests_to_empty_list():
        result = _make_result(False)
        assert result.killing_tests == []

    def it_stores_test_ids_run_when_provided():
        point = _make_point()
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=1)
        result = MutantResult(
            mutant=mutant,
            killed=True,
            tests_run=2,
            killing_test="test_a",
            time_seconds=0.1,
            test_ids_run=["test_a", "test_b"],
            killing_tests=["test_a"],
        )
        assert result.test_ids_run == ["test_a", "test_b"]
        assert result.killing_tests == ["test_a"]


def describe_run_result_new_fields():
    def it_defaults_coverage_map_to_none():
        run = RunResult(
            target_files=[],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.0,
        )
        assert run.coverage_map is None

    def it_defaults_target_sources_to_empty_dict():
        run = RunResult(
            target_files=[],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.0,
        )
        assert run.target_sources == {}

    def it_stores_coverage_map_when_provided():
        cov = CoverageMap()
        cov.add("test.py", 1, "test_foo")
        run = RunResult(
            target_files=["test.py"],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.0,
            coverage_map=cov,
        )
        assert run.coverage_map is cov
        assert run.coverage_map.tests_for("test.py", 1) == {"test_foo"}

    def it_stores_target_sources_when_provided():
        run = RunResult(
            target_files=["app.py"],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.0,
            target_sources={"app.py": "x = 1\n"},
        )
        assert run.target_sources == {"app.py": "x = 1\n"}
