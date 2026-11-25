"""Fail-closed, content-bound protocol for the two outer-process CI lane.

No test execution or GitHub credentials here. A complete result is not proof
unless it matches the independently expected plan/context and exact shard cover.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

PLAN = "leela.ci-plan/v1"
RESULT = "leela.ci-result/v1"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate JSON field")
        return result
    return json.loads(text, object_pairs_hook=unique)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def bind(plan: dict[str, Any]) -> dict[str, Any]:
    require("plan_id" not in plan, "plan already bound")
    return plan | {"plan_id": digest(plan)}


def validate_plan(plan: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    require(plan.get("schema") == PLAN, "unsupported plan")
    require(plan.get("context") == context, "source/run/attempt/Python context mismatch")
    require(plan.get("plan_id") == digest({k: v for k, v in plan.items() if k != "plan_id"}), "plan digest mismatch")
    require(plan.get("shard_count") == 2, "exactly two CI shards required")
    modules = plan.get("modules")
    require(isinstance(modules, list) and bool(modules) and all(isinstance(m, str) and m for m in modules) and modules == sorted(set(modules)), "missing/duplicate native target module scope")
    rows = plan.get("mutants")
    require(isinstance(rows, list) and bool(rows), "empty/missing mutation plan")
    ids = [r.get("id") for r in rows]
    require(all(integer(i) for i in ids) and ids == list(range(len(rows))), "noncanonical/duplicate mutant IDs")
    for row in rows:
        require(isinstance(row.get("site"), dict) and bool(row["site"]), "missing mutant site")
        tests = row.get("tests")
        require(isinstance(tests, list) and bool(tests) and all(isinstance(t, str) and t for t in tests), "missing exact test selection")
        require(len(tests) == len(set(tests)), "duplicate selected test")
        require(isinstance(row.get("test_times"), dict), "missing native timeout inputs")
        require(set(row["test_times"]) == set(tests), "timeout/test selection mismatch")
        require(all(finite(t) for t in row["test_times"].values()), "invalid native timeout input")
    shards = plan.get("shards")
    require(isinstance(shards, list) and len(shards) == 2, "missing shard plan")
    require(all(isinstance(s, list) and all(integer(i) for i in s) for s in shards), "invalid shard IDs")
    flat = [i for s in shards for i in s]
    require(len(flat) == len(set(flat)) and sorted(flat) == ids, "shard cover missing/duplicate/foreign mutant")
    require(len(ids) < 2 or all(shards), "empty avoidable shard")


def balanced(rows: list[dict[str, Any]], weights: Mapping[str, float]) -> tuple[list[list[int]], list[float]]:
    """Stable-identity LPT; absent sites use an explicit per-file median upstream."""
    bins: list[list[int]] = [[], []]
    totals = [0.0, 0.0]
    weighted = []
    for row in rows:
        key = digest(row["site"])
        weight = weights.get(key, 1.0)
        require(finite(weight) and weight > 0, "invalid prior runtime")
        weighted.append((float(weight), row["id"]))
    for weight, mutant_id in sorted(weighted, key=lambda r: (-r[0], r[1])):
        index = 0 if totals[0] <= totals[1] else 1
        bins[index].append(mutant_id)
        totals[index] += weight
    return bins, totals


def validate_result(bundle: Mapping[str, Any], plan: Mapping[str, Any], index: int, count: int) -> dict[int, dict[str, Any]]:
    require(bundle.get("schema") == RESULT and bundle.get("complete") is True, "incomplete result")
    require(bundle.get("plan_id") == plan["plan_id"] and bundle.get("context") == plan["context"], "stale/foreign result")
    require(bundle.get("shard_index") == index and type(bundle.get("shard_index")) is int, "wrong shard index")
    require(bundle.get("shard_count") == count and type(bundle.get("shard_count")) is int, "wrong shard count")
    require(bundle.get("runtime") == plan.get("runtime") and bool(bundle.get("runtime")), "runtime drift")
    require(bundle.get("modules") == plan["modules"], "native target-set scope drift")
    require(bundle.get("baseline_passed") is True and integer(bundle.get("baseline_tests_run"), 1), "unproven baseline")
    require(integer(bundle.get("start_ns"), 1) and integer(bundle.get("end_ns"), 1), "missing execution interval")
    require(bundle["end_ns"] >= bundle["start_ns"] and finite(bundle.get("wall_seconds")), "invalid interval")
    expected_ids = list(range(len(plan["mutants"]))) if count == 1 else plan["shards"][index]
    rows = bundle.get("results")
    require(isinstance(rows, list), "missing result rows")
    ids = [row.get("id") for row in rows]
    require(all(integer(i) for i in ids) and len(ids) == len(set(ids)) and sorted(ids) == sorted(expected_ids), "result cover missing/duplicate/foreign mutant")
    expected = {r["id"]: r for r in plan["mutants"]}
    previous_end = bundle["start_ns"]
    for row in rows:
        require(integer(row.get("start_ns"), 1) and integer(row.get("end_ns"), 1), "missing mutant interval")
        require(previous_end <= row["start_ns"] <= row["end_ns"] <= bundle["end_ns"], "nonserial/invalid mutant interval")
        previous_end = row["end_ns"]
        require(row.get("site") == expected[row["id"]]["site"], "mutant site mismatch")
        require(row.get("selected_tests") == expected[row["id"]]["tests"], "test selection drift")
        actual = row.get("actual_tests")
        # Preserve phase reports; a call plus teardown may repeat one node.
        require(isinstance(actual, list) and all(isinstance(t, str) and t in row["selected_tests"] for t in actual), "missing/foreign actual test phase ID")
        require(row.get("status") in ("killed", "survived", "error"), "invalid verdict")
        require(type(row.get("timeout")) is bool, "missing timeout classification")
        require(isinstance(row.get("killing_tests"), list), "missing killing evidence")
        require(all(isinstance(t, str) for t in row["killing_tests"]), "invalid killing evidence")
        require(row["timeout"] == ("<timeout>" in row["killing_tests"]), "timeout evidence mismatch")
        require(finite(row.get("runner_seconds")), "missing runner elapsed")
        require(row["status"] == "killed" and row.get("error") is None and not row["timeout"], "survivor/error/timeout refuses CI")
        require(bool(row["killing_tests"]), "killed without evidence")
    return {row["id"]: row for row in rows}


def aggregate(plan: dict[str, Any], bundles: list[dict[str, Any]], context: dict[str, Any], serial: dict[str, Any] | None = None, require_serial: bool = False) -> dict[str, Any]:
    validate_plan(plan, context)
    require(len(bundles) == 2, "missing/duplicate shard artifact")
    by_index = {b.get("shard_index"): b for b in bundles}
    require(len(by_index) == 2 and set(by_index) == {0, 1}, "missing/duplicate shard artifact")
    combined: dict[int, dict[str, Any]] = {}
    for index in range(2):
        rows = validate_result(by_index[index], plan, index, 2)
        require(not (combined.keys() & rows.keys()), "duplicate cross-shard mutant")
        combined.update(rows)
    require(sorted(combined) == list(range(len(plan["mutants"]))), "incomplete aggregate")
    require(not require_serial or serial is not None, "missing exact-source serial reference")
    phase_differences = None
    if serial is not None:
        reference = validate_result(serial, plan, 0, 1)
        phase_differences = [i for i, row in combined.items() if row["actual_tests"] != reference[i]["actual_tests"]]
        for mutant_id, row in combined.items():
            other = reference[mutant_id]
            require((row["status"], row["timeout"], row.get("error")) == (other["status"], other["timeout"], other.get("error")), "per-ID verdict parity mismatch")
    overlap = max(0, min(b["end_ns"] for b in bundles) - max(b["start_ns"] for b in bundles)) / 1e9
    mutant_overlap = sum(max(0, min(a["end_ns"], b["end_ns"]) - max(a["start_ns"], b["start_ns"])) for a in bundles[0]["results"] for b in bundles[1]["results"]) / 1e9
    require(not require_serial or mutant_overlap > 0, "no observed concurrent mutant execution")
    return {"environment_differences_from_plan": [b["shard_index"] for b in bundles if b.get("environment") != plan.get("environment")], "runtime_identity": "exact executable/dependency fingerprint; OS/image/kernel are separately reported provenance, not controlled-condition proof", "actual_test_phase_id_differences": phase_differences, "observed_execution_elapsed_seconds": (max(b["end_ns"] for b in bundles) - min(b["start_ns"] for b in bundles)) / 1e9, "serial_summed_runner_seconds": None if serial is None else sum(r["runner_seconds"] for r in serial["results"]), "actual_mutant_utc_overlap_seconds": mutant_overlap,"schema": "leela.ci-aggregate/v1", "plan_id": plan["plan_id"], "context": context, "mutants": len(combined), "ids": sorted(combined), "serial_parity": serial is not None, "observed_utc_overlap_seconds": overlap, "overlap_clock_caveat": "Different hosted UTC clocks; corroborate with provider job intervals, not a clock-sync proof.", "summed_execution_worker_seconds": sum(b["wall_seconds"] for b in bundles), "summed_runner_seconds": sum(r["runner_seconds"] for r in combined.values()), "serial_execution_worker_seconds": None if serial is None else serial["wall_seconds"], "provider_job_setup_teardown": "Obtain completed job start/end metadata; execution fields alone exclude action setup/post-upload teardown."}
