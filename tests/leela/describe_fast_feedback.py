"""Fast feedback defers candidates, without fabricating any verdict."""

import json
import re
from unittest.mock import patch

import pytest

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.engine import Engine
from pytest_leela.fast_feedback import removal_descendants
from pytest_leela.models import CoverageMap, Mutant, MutantResult
from pytest_leela.operators import mutations_for
from pytest_leela.output import format_json_report


def _plan(**targets):
    files = {name: f"/project/{name}.py" for name in targets}
    mutants = []
    for module, source in targets.items():
        for point in find_mutation_points(source, files[module], module):
            for replacement in mutations_for(point):
                mutants.append(Mutant(point, replacement, len(mutants)))
    return mutants, removal_descendants(mutants, targets, files)


def describe_removal_descendants():
    def it_finds_strictly_contained_nodes_and_excludes_siblings():
        mutants, links = _plan(calc=(
            "def a(x):\n"
            "    return x * 6\n"
            "def b(x):\n"
            "    return x + 1\n"
        ))
        assert len(links) == 2
        for parent_id, children in links.items():
            parent = mutants[parent_id]
            assert parent.replacement_op == "None"
            assert children
            for child_id in children:
                child = mutants[child_id]
                assert child.point.node_type == "BinOp"
                assert child.point.lineno == parent.point.lineno

    def it_does_not_link_children_with_an_ambiguous_nested_selector():
        _, links = _plan(calc="def f(x):\n    return x + 2 - 1\n")
        assert links == {}

    def it_does_not_link_children_with_an_ambiguous_cross_file_selector():
        _, links = _plan(
            one="def f(x):\n    return x + 1\n",
            two="def g(x):\n    return x * 2\n",
        )
        assert links == {}

    def it_rejects_a_colliding_parent_even_when_its_child_is_unique():
        _, links = _plan(
            one="def f(x):\n    return x + 1\n",
            two="def g(x):\n    return 2\n",
        )
        assert links == {}


def describe_fast_feedback_engine():
    @pytest.mark.parametrize(
        "outcome", ["survived", "killed", "timeout", "error", "not activated", "partial", "zero", "unknown"]
    )
    def it_runs_parent_test_union_and_requires_a_clean_activated_survivor(
        tmp_path, monkeypatch, outcome
    ):
        target = tmp_path / "calc.py"
        target.write_text("def f(x):\n    return (\n        6 * x\n    )\n")
        monkeypatch.chdir(tmp_path)
        coverage = CoverageMap()
        coverage.add(str(target), 2, "parent::test")
        if outcome != "unknown":
            coverage.add(str(target), 3, "child::test")
        called = []

        def run(mutant, *args, test_ids, **kwargs):
            called.append((mutant, test_ids))
            if mutant.point.node_type != "Return":
                return MutantResult(mutant, True, 1, "child::test", 0.0)
            killed = outcome in ("killed", "timeout")
            return MutantResult(
                mutant, killed, 0 if outcome == "zero" else len(test_ids),
                "<timeout>" if outcome == "timeout" else None, 0.0,
                test_ids_run=["parent::test"] if outcome == "partial" else test_ids,
                error="inconclusive" if outcome == "error" else None,
                activated=outcome != "not activated",
            )

        with (
            patch.object(Engine, "_check_baseline"),
            patch("pytest_leela.engine.run_tests_for_mutant", side_effect=run),
        ):
            result = Engine(use_types=False).run(
                [str(target)], fast_feedback=True, pre_coverage_map=coverage
            )

        assert called[0][0].point.node_type == "Return"
        assert called[0][1] == (
            ["parent::test"] if outcome == "unknown" else ["child::test", "parent::test"]
        )
        if outcome == "unknown":
            assert {tuple(test_ids or []) for _, test_ids in called[1:]} == {()}
        assert result.mutants_tested == len(called)
        report = json.loads(format_json_report(result))
        if outcome == "survived":
            assert len(called) == 1 and len(result.deferred) == 2
            assert result.mutants_scored == 1
            assert report["complete"] is False
            assert report["mutants_deferred"] == 2
            assert {m["id"] for m in report["deferred_mutants"]} == {1, 2}
        else:
            assert len(called) == 3 and not result.deferred
            assert report["complete"] is True


def _embedded_report(pytester, filename):
    html = (pytester.path / filename).read_text()
    match = re.search(r"window\.LEELA_DATA = (.*?);\n", html, re.S)
    assert match is not None
    return json.loads(match[1])


def describe_fast_feedback_cli():
    def it_defers_only_activated_survivor_descendants_and_full_runs_them(pytester):
        pytester.makepyfile(
            calc=(
                "def observe(x):\n"
                "    return 6 * x\n"
                "\ndef sibling(x):\n"
                "    return x + 1\n"
            ),
            test_calc=(
                "from calc import observe, sibling\n"
                "def test_example():\n"
                "    observe(0)\n"
                "    assert sibling(3) == 4\n"
            ),
        )
        common = ("-q", "--target", "calc.py")
        fast = pytester.runpytest_subprocess("--leela", "--leela-html", "report.html", *common)
        full = pytester.runpytest_subprocess(
            "--leela-full", "--leela-html", "full.html", *common
        )

        assert fast.ret == full.ret == 1  # the real removal is a survivor
        fast.stdout.fnmatch_lines([
            "*testing mutant 1/* calc:2:* expr -> None*",
            "*testing mutant * calc:5:*",
            "*finished (partial)*2 deferred*",
            "*INCOMPLETE: 2 deferred*--leela-full*",
            "*DEFERRED*calc.py:2:*BinOp*enclosing return removal survived*",
        ])
        assert "deferred" not in full.stdout.str()
        full.stdout.fnmatch_lines([
            "*testing mutant * calc:2:* Mult -> FloorDiv*",
            "*finished (*runnable mutants completed*",
        ])
        assert "Overall (tested mutants only):" in fast.stdout.str()
        assert "Overall:" in full.stdout.str()
        assert fast.stdout.str().count(" testing mutant ") + 2 == full.stdout.str().count(" testing mutant ")

        data = _embedded_report(pytester, "report.html")
        assert data["summary"]["complete"] is False
        assert data["summary"]["mutants_deferred"] == 2
        assert len(data["deferred_mutants"]) == 2
        assert {m["line"] for m in data["deferred_mutants"]} == {2}
        assert all(m["after_survivor"]["line"] == 2 for m in data["deferred_mutants"])
        assert sum(m["status"] == "survived" for m in data["files"]["calc.py"]["mutants"]) == 1

        exhaustive = _embedded_report(pytester, "full.html")
        assert exhaustive["summary"]["complete"] is True
        assert exhaustive["summary"]["mutants_tested"] == 7
        assert exhaustive["deferred_mutants"] == []
        assert any(
            m["lineno"] == 2 and m["replacement"] == "//" and m["status"] == "killed"
            for m in exhaustive["files"]["calc.py"]["mutants"]
        )

    @pytest.mark.parametrize("mode", ["--leela", "--leela-full"])
    def it_fails_when_memory_stops_the_run_even_if_every_tested_mutant_is_killed(
        pytester, mode
    ):
        pytester.makepyfile(
            calc="def double(x):\n    return x * 2\n",
            test_calc="from calc import double\ndef test_double():\n    assert double(3) == 6\n",
        )
        pytester.makeconftest(
            """
            import pytest_leela.engine as engine

            def pytest_configure(config):
                if config.getoption("leela", False) or config.getoption("leela_full", False):
                    checks = iter((True, False))
                    engine.is_memory_ok = lambda limits: next(checks, False)
            """
        )
        result = pytester.runpytest_subprocess(
            mode, "--leela-html", "limited.html", "--target", "calc.py", "-q",
        )

        assert result.ret == 1
        result.stdout.fnmatch_lines([
            "*stopped at memory limit (1/3 runnable mutants completed*",
            "*INCOMPLETE: 2 remaining runnable mutants not run.*",
            "*Overall (tested mutants only): 1/1 killed*",
        ])
        report = _embedded_report(pytester, "limited.html")
        assert report["summary"]["complete"] is False
        assert report["summary"]["mutants_tested"] == report["summary"]["killed"] == 1
        assert report["summary"]["mutants_deferred"] == report["summary"]["errors"] == 0
