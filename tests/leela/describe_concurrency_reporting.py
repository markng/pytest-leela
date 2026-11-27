"""Real CLI proofs that reporting does not conflate settings with execution."""

import json

import pytest


def _run_leela(pytester, *args):
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "--leela", *args)


def describe_concurrency_reporting():
    @pytest.mark.parametrize("jsonl", [False, True])
    @pytest.mark.parametrize("target", [False, True])
    def it_reports_zero_for_both_kinds_of_no_work(pytester, jsonl, target):
        pytester.makepyfile(test_calc="def test_pass():\n    pass\n")
        args = []
        if target:
            pytester.makepyfile(calc="ANSWER = 42\n")
            args += ["--target", "calc.py"]
        if jsonl:
            args += ["--leela-jsonl", "events.jsonl"]

        result = _run_leela(pytester, *args)

        assert result.ret == 0
        result.stdout.fnmatch_lines([
            "Leela mutant concurrency (observed peak): 0 (no mutants executed)",
            "Mutation test dispatch model: serial calls to in-process pytest.main()",
            "Pytest test/worker concurrency during mutation: not measured",
            "Configured only: --max-cores=unset; outer pytest -n=unset",
        ])
        assert "testing mutant " not in result.stdout.str()
        path = pytester.path / "events.jsonl"
        if jsonl:
            assert path.read_text() == ""
        else:
            assert not path.exists()

    def it_reports_one_serial_mutant_despite_multiple_configured_cores(pytester):
        pytester.makepyfile(
            calc="def double(x):\n    return x * 2\n",
            test_calc="from calc import double\ndef test_double():\n    assert double(3) == 6\n",
        )
        result = _run_leela(
            pytester, "--target", "calc.py", "--max-cores", "4", "--leela-jsonl", "events.jsonl",
        )

        assert result.ret == 0
        result.stdout.fnmatch_lines([
            "Leela mutant concurrency (observed peak): 1 (serial)",
            "Mutation test dispatch model: serial calls to in-process pytest.main()",
            "Pytest test/worker concurrency during mutation: not measured",
            "Configured only: --max-cores=4; outer pytest -n=unset",
        ])
        records = [
            json.loads(line)
            for line in (pytester.path / "events.jsonl").read_text().splitlines()
        ]
        assert len(records) > 1
        assert len(records) == result.stdout.str().count("completed mutant id=")

    @pytest.mark.parametrize("workers", [0, 4, "auto"])
    def it_reports_outer_worker_options_as_configuration_only(workers):
        from unittest.mock import Mock

        from pytest_leela.plugin import LeelaPlugin

        config = Mock()
        config.getoption.return_value = workers
        summary = LeelaPlugin(config)._concurrency_summary(True, None)

        assert summary.splitlines() == [
            "Leela mutant concurrency (observed peak): 1 (serial)",
            "Mutation test dispatch model: serial calls to in-process pytest.main()",
            "Pytest test/worker concurrency during mutation: not measured",
            f"Configured only: --max-cores=unset; outer pytest -n={workers}",
        ]
