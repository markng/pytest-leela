"""CLI concurrency reporting must not infer worker counts from resource settings."""

from unittest.mock import Mock

import pytest

from pytest_leela.plugin import LeelaPlugin


def _run_leela(pytester, *args):
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "--leela", *args)


def describe_concurrency_reporting():
    def it_reports_a_serial_peak_despite_a_larger_configured_cpu_limit(pytester):
        pytester.makepyfile(
            calc="def double(x):\n    return x * 2\n",
            test_calc="from calc import double\n\ndef test_double():\n    assert double(3) == 6\n",
        )

        result = _run_leela(pytester, "--target", "calc.py", "--max-cores", "4")

        assert result.ret == 0
        assert result.stdout.str().count("testing mutant ") > 1
        result.stdout.fnmatch_lines([
            "Leela mutant concurrency (observed peak): 1 (serial)",
            "Pytest test/worker concurrency during mutation: not measured",
            "Configured only: --max-cores=4; outer pytest -n=unset",
        ])

    @pytest.mark.parametrize("zero_work", ["no-targets", "no-runnable", "memory-limit"])
    def it_reports_zero_if_no_mutant_executes(pytester, zero_work):
        pytester.makepyfile(test_calc="def test_pass():\n    pass\n")
        if zero_work == "no-targets":
            args = ()
        elif zero_work == "no-runnable":
            pytester.makepyfile(calc="ANSWER = 42\n")
            args = ("--target", "calc.py")
        else:
            pytester.makepyfile(calc="def value():\n    return True\n")
            # The memory guard uses a strict comparison: no nonnegative
            # measurement can be below a configured limit of zero.
            args = ("--target", "calc.py", "--max-memory", "0")

        result = _run_leela(pytester, *args)

        assert result.ret == 0
        if zero_work == "memory-limit":
            result.stdout.fnmatch_lines([
                "*checking baseline (0/1 runnable mutants completed; *",
                "*stopped at memory limit (0/1 runnable mutants completed; *",
            ])
        result.stdout.fnmatch_lines([
            "Leela mutant concurrency (observed peak): 0 (no mutants executed)",
            "Pytest test/worker concurrency during mutation: not measured",
            "Configured only: --max-cores=unset; outer pytest -n=unset",
        ])
        assert "testing mutant " not in result.stdout.str()

    @pytest.mark.parametrize("setting", [0, 6, "auto"])
    def it_labels_the_outer_pytest_worker_setting_as_configured_only(setting):
        config = Mock()
        config.getoption.return_value = setting

        summary = LeelaPlugin(config)._concurrency_summary(3, None)

        config.getoption.assert_called_once_with("numprocesses", default=None)
        assert summary.splitlines() == [
            "Leela mutant concurrency (observed peak): 1 (serial)",
            "Pytest test/worker concurrency during mutation: not measured",
            f"Configured only: --max-cores=unset; outer pytest -n={setting}",
        ]
