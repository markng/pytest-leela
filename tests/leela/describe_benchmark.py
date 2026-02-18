"""Tests for pytest_leela.benchmark — report formatting and plugin behavior."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from pytest_leela.benchmark import BenchmarkPlugin, _BenchmarkRow, _format_benchmark_report


def _row(
    label: str = "test",
    wall_time: float = 1.0,
    mutants_tested: int = 10,
    mutants_pruned: int = 5,
) -> _BenchmarkRow:
    return _BenchmarkRow(
        label=label,
        wall_time=wall_time,
        mutants_tested=mutants_tested,
        mutants_pruned=mutants_pruned,
    )


def describe_format_benchmark_report():
    def it_returns_a_string_not_none():
        """Kills: line 103 return expr → return None."""
        rows = [_row()]
        result = _format_benchmark_report(rows)
        assert isinstance(result, str)
        assert result is not None

    def it_contains_the_header():
        rows = [_row()]
        result = _format_benchmark_report(rows)
        assert "leela benchmark" in result
        assert "=" * 70 in result

    def it_shows_no_speedup_suffix_for_first_row():
        """Kills: line 92 is → is not.

        First row should NOT have a speedup suffix like '(1.0x)'.
        """
        rows = [_row(label="Baseline", wall_time=10.0)]
        result = _format_benchmark_report(rows)
        assert "1.0x" not in result

    def it_shows_speedup_suffix_for_subsequent_rows():
        """Kills: line 92 is → is not.

        Non-first rows SHOULD have a speedup suffix.
        """
        rows = [
            _row(label="Baseline", wall_time=10.0),
            _row(label="Optimized", wall_time=5.0),
        ]
        result = _format_benchmark_report(rows)
        assert "(2.0x)" in result

    def it_calculates_speedup_as_true_division():
        """Kills: line 91 / → * and / → //.

        baseline=10, wall_time=4 → speedup=2.5.
        If * → 40.0, if // → 2.0.
        """
        rows = [
            _row(label="Baseline", wall_time=10.0),
            _row(label="Fast", wall_time=4.0),
        ]
        result = _format_benchmark_report(rows)
        assert "(2.5x)" in result

    def it_returns_zero_speedup_for_zero_wall_time():
        """Kills: line 91 > → >=.

        wall_time=0 should give speedup=0.0, not ZeroDivisionError.
        """
        rows = [
            _row(label="Baseline", wall_time=10.0),
            _row(label="Broken", wall_time=0.0),
        ]
        result = _format_benchmark_report(rows)
        assert "(0.0x)" in result

    def it_calculates_speedup_for_positive_wall_time():
        """Kills: line 91 > → <=.

        Positive wall_time should compute baseline/wall_time, not 0.0.
        """
        rows = [
            _row(label="Baseline", wall_time=10.0),
            _row(label="Faster", wall_time=5.0),
        ]
        result = _format_benchmark_report(rows)
        assert "(2.0x)" in result
        assert "(0.0x)" not in result

    def it_shows_total_speedup_with_exactly_two_rows():
        """Kills: line 98 >= → >.

        len(rows)=2 should trigger the total speedup line.
        With > mutation, len=2 would NOT show it.
        """
        rows = [
            _row(label="Baseline", wall_time=10.0),
            _row(label="Fast", wall_time=5.0),
        ]
        result = _format_benchmark_report(rows)
        assert "Total speedup:" in result
        assert "Total speedup: 2.0x" in result

    def it_hides_total_speedup_with_one_row():
        """Kills: line 98 >= → <.

        len(rows)=1 should NOT show total speedup.
        With < mutation, len=1 < 2 is True → would wrongly show it.
        """
        rows = [_row(label="Only", wall_time=10.0)]
        result = _format_benchmark_report(rows)
        assert "Total speedup:" not in result

    def it_uses_last_row_for_total_speedup_not_second():
        """Kills: line 99 - → + (both occurrences of rows[-1]).

        With 3 rows, rows[-1] is rows[2]. Mutation to rows[+1] gives rows[1].
        Use different wall_times to distinguish.
        """
        rows = [
            _row(label="Baseline", wall_time=10.0),
            _row(label="Middle", wall_time=7.0),
            _row(label="Final", wall_time=4.0),
        ]
        result = _format_benchmark_report(rows)
        # total_speedup = 10.0 / 4.0 = 2.5
        # If mutated to rows[1]: 10.0 / 7.0 ≈ 1.4
        assert "Total speedup: 2.5x" in result

    def it_uses_last_row_wall_time_in_condition_check():
        """Kills: line 99 - → + on the condition rows[-1].wall_time > 0.

        With 3 rows where the last has wall_time=0 but the second doesn't:
        - Original: rows[-1].wall_time = 0 → > 0 is False → total_speedup = 0.0
        - Mutant:   rows[+1].wall_time = 5 → > 0 is True → baseline / 0 → crash
        """
        rows = [
            _row(label="Baseline", wall_time=10.0),
            _row(label="Middle", wall_time=5.0),
            _row(label="Final", wall_time=0.0),
        ]
        result = _format_benchmark_report(rows)
        assert "Total speedup: 0.0x" in result

    def it_calculates_total_speedup_as_true_division():
        """Kills: line 99 / → * and / → //.

        baseline=10, last wall_time=3 → total=3.3x.
        If * → 30.0x, if // → 3.0x.
        """
        rows = [
            _row(label="Baseline", wall_time=10.0),
            _row(label="Fast", wall_time=3.0),
        ]
        result = _format_benchmark_report(rows)
        assert "Total speedup: 3.3x" in result

    def it_handles_zero_wall_time_in_last_row_for_total():
        """Kills: line 99 > → >=.

        Last row wall_time=0 → total_speedup=0.0, not ZeroDivisionError.
        """
        rows = [
            _row(label="Baseline", wall_time=10.0),
            _row(label="Zero", wall_time=0.0),
        ]
        result = _format_benchmark_report(rows)
        assert "Total speedup: 0.0x" in result

    def it_computes_total_speedup_for_positive_last_row():
        """Kills: line 99 > → <=.

        Positive last-row wall_time should compute division, not 0.0.
        """
        rows = [
            _row(label="Baseline", wall_time=10.0),
            _row(label="Fast", wall_time=2.0),
        ]
        result = _format_benchmark_report(rows)
        assert "Total speedup: 5.0x" in result
        assert "Total speedup: 0.0x" not in result


def describe_BenchmarkPlugin():
    def describe_pytest_sessionfinish():
        def it_returns_early_when_exitstatus_nonzero():
            """Kills: line 29 != → ==.

            With exitstatus=1, should NOT call getoption.
            """
            config = MagicMock()
            plugin = BenchmarkPlugin(config)
            session = MagicMock()
            result = plugin.pytest_sessionfinish(session, exitstatus=1)
            assert result is None
            config.getoption.assert_not_called()

        def it_proceeds_when_exitstatus_is_zero():
            """Kills: line 29 != → ==.

            With exitstatus=0, should proceed past the guard and call getoption.
            """
            config = MagicMock()
            config.getoption.return_value = None
            plugin = BenchmarkPlugin(config)
            session = MagicMock()
            session.config = config
            session.config.rootpath = Path("/tmp/fake")

            with patch(
                "pytest_leela.benchmark._find_default_targets", return_value=[]
            ):
                plugin.pytest_sessionfinish(session, exitstatus=0)
            config.getoption.assert_called()

        def it_constructs_test_dir_using_path_division():
            """Kills: line 41 / → * and / → //.

            Path * str → TypeError, Path // str → TypeError.
            Also verifies the resulting test_dir string is correct.
            """
            config = MagicMock()
            config.getoption.return_value = "/some/target.py"
            plugin = BenchmarkPlugin(config)
            session = MagicMock()
            session.config = config
            session.config.rootpath = Path("/project/root")

            mock_result = MagicMock()
            mock_result.wall_time_seconds = 1.0
            mock_result.mutants_tested = 5
            mock_result.mutants_pruned = 2

            with (
                patch(
                    "pytest_leela.benchmark._find_target_files",
                    return_value=["/some/target.py"],
                ),
                patch("pytest_leela.benchmark.Engine") as MockEngine,
            ):
                MockEngine.return_value.run.return_value = mock_result
                plugin.pytest_sessionfinish(session, exitstatus=0)

                # Verify Engine.run was called with correct test_dir
                call_args = MockEngine.return_value.run.call_args
                test_dir_arg = call_args[0][1]
                assert test_dir_arg == str(Path("/project/root") / "tests")
