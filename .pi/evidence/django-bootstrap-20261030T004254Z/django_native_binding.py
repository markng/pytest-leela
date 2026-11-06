"""Disposable one-case child/native binding diagnostic for this recovery only."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.models import Mutant
from pytest_leela.operators import DEFAULT_OPERATORS, build_allowed_keys, mutations_for
from pytest_leela.runner import ProjectModuleScope, run_control, run_tests_for_mutant
from pytest_leela.type_extractor import enrich_mutation_points

source_root = Path.cwd().resolve()
run = Path(sys.argv[1]).resolve()
project = Path(sys.argv[2]).resolve()
target = project / "shop/pricing.py"
module = "shop.pricing"
test_id = "tests/test_shop.py::test_registry_and_identity_hold"
source = target.read_text()
source_sha = hashlib.sha256(source.encode()).hexdigest()
points, enrichment = enrich_mutation_points(
    source, find_mutation_points(source, str(target), module)
)
mutants = [
    Mutant(point, replacement, index)
    for index, (point, replacement) in enumerate(
        (point, replacement)
        for point in points
        for replacement in mutations_for(
            point, True, build_allowed_keys(DEFAULT_OPERATORS)
        )
    )
]
selected = [
    mutant for mutant in mutants
    if mutant.point.node_type == "BinOp" and mutant.replacement_op == "Sub"
]
assert len(selected) == 1, [dataclasses.asdict(mutant) for mutant in mutants]
mutant = selected[0]
job = uuid.uuid4().hex
events = run / f"native-events-{job}.jsonl"
observer = source_root / ".pi/recovered-helpers/passive_timeout_observer.py"
assert hashlib.sha256(observer.read_bytes()).hexdigest() == "9facc5f24b9d55c495e954fadac8c4427eb8acdf53d3b205a7580e36e15d8f54"
sys.path.insert(0, str(observer.parent))
sys.path.insert(0, str(project))
os.environ["PYTHONPATH"] = os.pathsep.join((str(observer.parent), str(project), str(source_root / "src")))
os.environ["PYTEST_PLUGINS"] = "passive_timeout_observer"
os.environ["LEELA_PROOF_EVENTS"] = str(events)
scope = ProjectModuleScope(str(project))
times = {test_id: 0.31}

os.chdir(project)
os.environ["LEELA_PROOF_INVOCATION"] = job + "-control"
control = run_control([test_id], None, frozenset(), scope, times)
assert control.error() is None, control.error()
assert control.failures() == [], control.failures()
os.environ["LEELA_PROOF_INVOCATION"] = job + "-mutant"
result = run_tests_for_mutant(
    mutant,
    {module: source},
    {module: str(target)},
    test_ids=[test_id],
    test_times=times,
    scope=scope,
)
records = [json.loads(line) for line in events.read_text().splitlines()]
clean_calls = [
    event for event in records
    if event["invocation"] == job + "-control"
    and event["event"] == "report" and event["phase"] == "call"
]
mutant_calls = [
    event for event in records
    if event["invocation"] == job + "-mutant"
    and event["event"] == "report" and event["phase"] == "call"
]
bindings = [
    event for event in records
    if event["invocation"] == job + "-mutant"
    and event["event"] == "loader-end"
    and event["module"] == module
    and Path(event["filename"]).resolve() == target
    and event["source_sha"] == source_sha
    and event["mutant"] == dataclasses.asdict(mutant)
    and event["completed"] and event["applied"] and event["ast_changed"]
    and len(event["executed_modules"]) == 1
    and event["executed_modules"][0]["code_sha"] == event["expected_module_sha"]
    and Path(event["executed_modules"][0]["filename"]).resolve() == target
]
failed_calls = [
    event for event in mutant_calls
    if event["outcome"] == "failed" and event["nodeid"] in result.killing_tests
]
assert result.killed and result.error is None and result.killing_tests == [test_id], dataclasses.asdict(result)
assert len(clean_calls) == 1 and clean_calls[0]["outcome"] == "passed", clean_calls
assert len(bindings) == 1 and len(failed_calls) == 1, (bindings, failed_calls)
assert bindings[0]["pid"] == failed_calls[0]["pid"]
assert bindings[0]["invocation"] == failed_calls[0]["invocation"] == job + "-mutant"
# Negative schema controls: wrong job and clean PID must not join to the affected binding.
assert not [event for event in bindings if event["invocation"] == job + "-control"]
assert clean_calls[0]["pid"] != bindings[0]["pid"]
output = {
    "classification": "fresh-qualified-child-native-binding",
    "job": job,
    "sourceRoot": str(source_root),
    "project": str(project),
    "target": str(target),
    "module": module,
    "sourceSha256": source_sha,
    "observer": str(observer),
    "observerSha256": hashlib.sha256(observer.read_bytes()).hexdigest(),
    "python": sys.executable,
    "mutantDenominator": len(mutants),
    "canonicalMutants": [dataclasses.asdict(item) for item in mutants],
    "selectedMutant": dataclasses.asdict(mutant),
    "enrichment": dataclasses.asdict(enrichment),
    "testId": test_id,
    "testTimes": times,
    "control": {
        "pid": clean_calls[0]["pid"], "outcome": clean_calls[0]["outcome"],
        "collectorTotal": control.collector.total, "elapsed": control.elapsed,
    },
    "parentResult": dataclasses.asdict(result),
    "binding": bindings[0],
    "failedCall": failed_calls[0],
    "events": str(events),
    "schema": {
        "join": ["invocation/job", "pid", "nodeid in parentResult.killing_tests"],
        "loaderRequired": ["module", "filename", "source_sha", "mutant", "applied", "ast_changed", "executed module code sha"],
        "callRequired": ["phase=call", "outcome=failed"],
    },
}
(run / "native-binding.json").write_text(json.dumps(output, indent=2) + "\n")
print(json.dumps({
    "job": job,
    "mutantId": mutant.mutant_id,
    "operator": mutant.replacement_op,
    "childPid": bindings[0]["pid"],
    "cleanPid": clean_calls[0]["pid"],
    "killingTests": result.killing_tests,
    "status": result.status,
}, sort_keys=True))
