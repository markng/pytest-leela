"""Small synthetic protocol controls. No mutant execution or CI result claim."""
import copy

import pytest

from ci.mutation_protocol import PLAN, RESULT, aggregate, balanced, bind, digest, load


def fixture():
    context = {"sha": "a" * 40, "run_id": "1", "attempt": "1", "python": "3.13"}
    rows = [{"id": i, "site": {"file": "subject.py", "line": i + 1, "column": 0, "original": "Lt", "replacement": "Gt"}, "tests": [f"test_subject.py::test_{i}"], "test_times": {f"test_subject.py::test_{i}": 0.1}} for i in range(4)]
    plan = bind({"schema": PLAN, "context": context, "runtime": {"python": "3.13.5", "packages": {"pytest": "9.0.3"}}, "shard_count": 2, "mutants": rows, "shards": [[0, 2], [1, 3]]})
    def result(index, count=2):
        ids = list(range(4)) if count == 1 else plan["shards"][index]
        return {"schema": RESULT, "complete": True, "context": context, "plan_id": plan["plan_id"], "runtime": plan["runtime"], "shard_index": index, "shard_count": count, "baseline_passed": True, "baseline_tests_run": 4, "start_ns": 100, "end_ns": 200, "wall_seconds": 0.1, "results": [{"id": i, "site": rows[i]["site"], "selected_tests": rows[i]["tests"], "actual_tests": list(rows[i]["tests"]), "status": "killed", "timeout": False, "error": None, "killing_tests": rows[i]["tests"], "runner_seconds": 0.01, "start_ns": 100 + ids.index(i) * 20, "end_ns": 115 + ids.index(i) * 20} for i in ids]}
    return plan, context, [result(0), result(1)], result(0, 1)


class TestProtocol:
    def test_complete_cover_and_exact_source_serial(self):
        p, c, b, s = fixture()
        report = aggregate(p, b, c, s, require_serial=True)
        assert report["ids"] == [0, 1, 2, 3]
        assert report["serial_parity"]
        assert report["summed_runner_seconds"] == pytest.approx(0.04)

    def test_missing_duplicate_extra_shard(self):
        for indices in ([0], [0, 0], [0, 1, 1], []):
            p, c, b, _ = fixture()
            with pytest.raises(ValueError): aggregate(p, [b[i] for i in indices], c)

    def test_missing_duplicate_foreign_mutant(self):
        for kind in ["missing", "duplicate", "foreign"]:
            p, c, b, _ = fixture()
            if kind == "missing": b[0]["results"].pop()
            if kind == "duplicate": b[0]["results"].append(copy.deepcopy(b[0]["results"][0]))
            if kind == "foreign": b[0]["results"][0]["id"] = 99
            with pytest.raises(ValueError): aggregate(p, b, c)

    def test_survivor_error_timeout_refuse(self):
        for status in ["survived", "error", "timeout", "hidden-timeout"]:
            p, c, b, _ = fixture(); row = b[0]["results"][0]
            if status in ("survived", "error"): row["status"] = status
            if status == "error": row["error"] = "synthetic failure"
            if "timeout" in status: row["killing_tests"] = ["<timeout>"]
            if status == "timeout": row["timeout"] = True
            with pytest.raises(ValueError): aggregate(p, b, c)

    def test_unproven_baseline_and_incomplete_refuse(self):
        for field in ["baseline_passed", "complete"]:
            p, c, b, _ = fixture(); b[1][field] = False
            with pytest.raises(ValueError): aggregate(p, b, c)

    def test_source_run_attempt_python_and_runtime_drift(self):
        for field in ["sha", "run_id", "attempt", "python"]:
            p, c, b, _ = fixture(); other = dict(c); other[field] = "foreign"
            with pytest.raises(ValueError): aggregate(p, b, other)
        p, c, b, _ = fixture(); b[1] = copy.deepcopy(b[1]); b[1]["runtime"] = {"python": "different"}
        with pytest.raises(ValueError): aggregate(p, b, c)

    def test_manifest_digest_and_test_selection_drift(self):
        p, c, b, _ = fixture(); p["mutants"][0]["tests"] = ["foreign"]
        with pytest.raises(ValueError): aggregate(p, b, c)
        p, c, b, _ = fixture(); b[1] = copy.deepcopy(b[1]); b[1]["results"][0]["selected_tests"] = ["foreign"]
        with pytest.raises(ValueError): aggregate(p, b, c)

    def test_required_serial_missing_or_foreign_or_failure(self):
        p, c, b, s = fixture()
        with pytest.raises(ValueError): aggregate(p, b, c, require_serial=True)
        s = copy.deepcopy(s); s["plan_id"] = "foreign"
        with pytest.raises(ValueError): aggregate(p, b, c, s, True)
        p, c, b, s = fixture(); s["results"][3]["status"] = "survived"
        with pytest.raises(ValueError): aggregate(p, b, c, s, True)

    def test_plan_shard_overlap_refuses_even_rebound(self):
        p, c, b, _ = fixture(); p = {k: v for k, v in p.items() if k != "plan_id"}; p["shards"] = [[0, 2], [1, 2]]; p = bind(p)
        with pytest.raises(ValueError): aggregate(p, b, c)

    def test_bool_id_nonfinite_and_duplicate_json_refuse(self):
        p, c, b, _ = fixture(); b[0]["results"][0]["id"] = False
        with pytest.raises(ValueError): aggregate(p, b, c)
        p, c, b, _ = fixture(); b[1]["wall_seconds"] = float("nan")
        with pytest.raises(ValueError): aggregate(p, b, c)
        with pytest.raises(ValueError): load('{"id": 1, "id": 2}')

    def test_overlapping_inner_execution_and_absent_parallelism_refuse(self):
        p, c, b, _ = fixture(); b[0]["results"][1]["start_ns"] = 101
        with pytest.raises(ValueError): aggregate(p, b, c)
        p, c, b, s = fixture()
        b[1]["start_ns"] = 300; b[1]["end_ns"] = 400
        for row in b[1]["results"]:
            row["start_ns"] += 200; row["end_ns"] += 200
        with pytest.raises(ValueError): aggregate(p, b, c, s, True)

    def test_native_phase_repeats_preserved_and_foreign_phase_refused(self):
        p, c, b, s = fixture(); b[0]["results"][0]["actual_tests"] *= 2
        assert aggregate(p, b, c, s)["actual_test_phase_id_differences"] == [0]
        b[0]["results"][0]["actual_tests"] = ["foreign"]
        with pytest.raises(ValueError): aggregate(p, b, c)
        p, c, b, s = fixture(); b[0]["baseline_tests_run"] = 0
        with pytest.raises(ValueError): aggregate(p, b, c)

    def test_environment_provenance_differs_but_runtime_drift_still_refuses(self):
        p, c, b, s = fixture(); b[0]["environment"] = {"image_version": "other"}
        assert aggregate(p, b, c, s)["environment_differences_from_plan"] == [0]
        b[0]["runtime"] = {"python_executable_sha256": "foreign"}
        with pytest.raises(ValueError): aggregate(p, b, c, s)

    def test_balanced_prior_site_weights_not_id_parity(self):
        p, _, _, _ = fixture(); weights = {digest(r["site"]): w for r, w in zip(p["mutants"], [8, 7, 2, 1])}
        bins, totals = balanced(p["mutants"], weights)
        assert bins == [[0, 3], [1, 2]]
        assert totals == [9, 9]
