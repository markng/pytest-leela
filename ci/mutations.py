"""Actual CI entry point: native full plan, isolated shard/serial, strict aggregate.

The public plugin/default Engine remain serial. No mocked mutation verdicts.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time

from ci.mutation_protocol import PLAN, RESULT, aggregate, balanced, bind, canonical, digest, load, require, validate_plan

ENTRY_START = time.monotonic()
ENTRY_UTC = time.time_ns()


def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".partial")
    with temp.open("x") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
        stream.flush(); os.fsync(stream.fileno())
    os.link(temp, path)  # Refuse overwrite/duplicate publication.
    temp.unlink()


def git(*args):
    return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args], text=True).strip()


def source_digest():
    names = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    return digest({n: hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in names if n})


def context():
    sha = git("rev-parse", "HEAD")
    require(os.environ.get("GITHUB_SHA", sha) == sha, "checkout differs from CI source")
    minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    require(os.environ.get("LEELA_CI_PYTHON", minor) == minor, "wrong Python matrix leg")
    fixture = os.environ.get("LEELA_CI_FIXTURE") == "1"
    require(not fixture or os.environ.get("GITHUB_ACTIONS") != "true", "fixture target forbidden in hosted CI")
    return {"sha": sha, "tree": git("rev-parse", "HEAD^{tree}"), "source_digest": source_digest(), "repository": os.environ.get("GITHUB_REPOSITORY", "local"), "run_id": os.environ.get("GITHUB_RUN_ID", "local"), "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"), "python": minor, "fixture": fixture, "serial_required": os.environ.get("LEELA_SERIAL_PARITY") == "true"}


def runtime():
    import pytest_leela
    versions = {d.metadata["Name"].lower().replace("_", "-"): d.version for d in importlib.metadata.distributions()}
    # Compare executable package content, not absolute editable paths or pyc.
    code = {}
    for dist in importlib.metadata.distributions():
        for f in dist.files or ():
            if f.suffix in (".py", ".so", ".pyd") and ".." not in f.parts:
                path = Path(dist.locate_file(f))
                if path.is_file(): code[str(f)] = hashlib.sha256(path.read_bytes()).hexdigest()
    source = Path(pytest_leela.__file__).parent
    return {"image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion"), "runner_arch": os.environ.get("RUNNER_ARCH"), "gil_enabled": getattr(sys, "_is_gil_enabled", lambda: True)(), "python": sys.version, "platform": platform.system(), "machine": platform.machine(), "cache_tag": sys.implementation.cache_tag, "hash_seed": os.environ.get("PYTHONHASHSEED"), "packages": versions, "package_code": digest(code), "leela_code": digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob("*.py")})}


def targets(args):
    if os.environ.get("LEELA_CI_FIXTURE") == "1":
        require(bool(args.target), "fixture needs explicit target")
        return sorted(args.target)
    require(not args.target, "CI requires entire canonical target set")
    return sorted(str(p) for p in Path("src/pytest_leela").glob("*.py") if p.name != "__init__.py")


def site(mutant):
    point = mutant.point
    relative = Path(point.file_path).resolve().relative_to(Path.cwd().resolve()).as_posix()
    return {"file": relative, "line": point.lineno, "column": point.col_offset, "original": point.original_op, "replacement": mutant.replacement_op}


def catalog(files):
    """Cheap independent full inventory via the canonical generation functions."""
    from pytest_leela.ast_analysis import find_mutation_points
    from pytest_leela.type_extractor import enrich_mutation_points
    from pytest_leela.operators import mutations_for, build_allowed_keys
    from pytest_leela.config import load_config
    from pytest_leela.engine import _module_name_from_path
    from pytest_leela.models import Mutant
    allowed = build_allowed_keys(load_config(Path.cwd()).operators)
    rows = []
    for file in files:
        path = str(Path(file).resolve()); text = Path(path).read_text()
        points, _ = enrich_mutation_points(text, find_mutation_points(text, path, _module_name_from_path(path)))
        for point in points:
            for replacement in mutations_for(point, True, allowed_keys=allowed):
                mutant = Mutant(point, replacement, len(rows))
                raw = dataclasses.asdict(point); raw["file_path"] = site(mutant)["file"]
                rows.append({"id": mutant.mutant_id, "site": site(mutant), "point": raw, "replacement": replacement})
    return rows


def inventory_check(plan, files):
    require(plan["targets"] == files, "partial/foreign target set")
    actual = [{k: row[k] for k in ("id", "site", "point", "replacement")} for row in plan["mutants"]]
    require(actual == catalog(files), "plan omitted/changed a canonical mutant")


def plan_command(args):
    import pytest
    from pytest_leela.engine import Engine
    from pytest_leela.config import load_config
    files = targets(args); require(bool(files), "no full mutation targets")
    class Collected:
        def pytest_collection_finish(self, session): self.ids = [i.nodeid for i in session.items]
    collector = Collected(); selection_start = time.monotonic()
    code = pytest.main(["--collect-only", "-q", "-p", "no:leela", "-p", "no:leela-benchmark"], plugins=[collector])
    require(code == 0 and bool(getattr(collector, "ids", None)), "test collection failed/empty")
    ids = collector.ids; selection_seconds = time.monotonic() - selection_start
    engine = Engine(enabled_categories=load_config(Path.cwd()).operators)
    prepared = engine.prepare(files, test_node_ids=ids)
    rows = []
    for mutant, tests in zip(prepared.mutants, prepared.test_ids):
        require(bool(tests), "native test selection missing")
        point = dataclasses.asdict(mutant.point); point["file_path"] = site(mutant)["file"]
        rows.append({"id": mutant.mutant_id, "site": site(mutant), "point": point, "replacement": mutant.replacement_op, "tests": tests, "test_times": {t: (prepared.test_times or {}).get(t, 1.0) for t in tests}})
    seed = load(Path(args.seed).read_text()) if args.seed else {"weights": {}}
    prior = seed.get("weights", {}).get(context()["python"], {})
    # Stable site IDs use observed same-site timings; unseen sites fall back to
    # the prior median for that target file, then 1s only for a new target.
    by_file = {}
    for record in prior.values(): by_file.setdefault(record["file"], []).append(record["seconds"])
    file_priors = seed.get("file_weights", {}).get(context()["python"], {})
    weights = {digest(row["site"]): prior.get(digest(row["site"]), {}).get("seconds", statistics.median(by_file[row["site"]["file"]]) if row["site"]["file"] in by_file else file_priors.get(row["site"]["file"], 1.0)) for row in rows}
    shards, predicted = balanced(rows, weights)
    value = bind({"schema": PLAN, "context": context(), "runtime": runtime(), "targets": files, "shard_count": 2, "mutants": rows, "shards": shards, "predicted_seconds": predicted, "weight_matches": sum(digest(r["site"]) in prior for r in rows), "weight_provenance": seed.get("provenance"), "collected_tests": ids, "pruned": prepared.total_pruned, "selection_seconds": selection_seconds, "prepare_seconds": time.monotonic() - ENTRY_START})
    validate_plan(value, context()); inventory_check(value, files)
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    atomic(out / "plan.json", value)
    # Install this exact dependency set on every worker, including pip itself.
    pins = [f"{n}=={v}" for n, v in sorted(value["runtime"]["packages"].items()) if n != "pytest-leela"]
    (out / "requirements.txt").write_text("\n".join(pins) + "\n")
    print(json.dumps({"phase": "plan", "mutants": len(rows), "plan_id": value["plan_id"], "shards": list(map(len, shards)), "predicted_seconds": predicted}), flush=True)


def dependencies_command(args):
    plan = load(Path(args.plan).read_text()); validate_plan(plan, context())
    pins = []
    for name, version in sorted(plan["runtime"]["packages"].items()):
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is not None, "invalid dependency name")
        require(isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9.!+_-]+", version) is not None, "invalid dependency version")
        if name != "pytest-leela": pins.append(f"{name}=={version}")
    Path(args.output).write_text("\n".join(pins) + "\n")


def execute_command(args):
    from pytest_leela.models import MutationPoint, Mutant
    from pytest_leela.runner import run_baseline, run_tests_for_mutant, precompute_user_modules, ProjectModuleScope
    plan = load(Path(args.plan).read_text()); validate_plan(plan, context())
    require(runtime() == plan["runtime"], "worker dependency/runtime differs from plan")
    inventory_check(plan, targets(args))
    require(args.count in (1, 2) and args.index in range(args.count), "invalid worker count/index")
    assigned = list(range(len(plan["mutants"]))) if args.count == 1 else plan["shards"][args.index]
    # Work in canonical plan order; balance assignment, never reorder pytest.
    assigned = set(assigned); work = [r for r in plan["mutants"] if r["id"] in assigned]
    scope = ProjectModuleScope(); known = precompute_user_modules(scope)
    baseline_ids = sorted({t for r in plan["mutants"] for t in r["tests"]})
    base = run_baseline(baseline_ids, None, known, scope)
    require(base.error() is None and not base.failures(), "worker unmutated baseline failed")
    if args.wait_stdin:
        require(context()["fixture"], "interactive barrier forbidden for hosted/full CI")
        print(json.dumps({"phase": "ready"}), flush=True)
        require(sys.stdin.readline().strip() == "GO", "missing fixture start signal")
    rows = []
    for raw in work:
        point = dict(raw["point"]); path = Path(point["file_path"]).resolve()
        require(path.is_relative_to(Path.cwd().resolve()), "foreign source path")
        point["file_path"] = str(path); mutant = Mutant(MutationPoint(**point), raw["replacement"], raw["id"])
        start = time.monotonic(); utc = time.time_ns()
        result = run_tests_for_mutant(mutant, {mutant.point.module_name: path.read_text()}, {mutant.point.module_name: str(path)}, test_ids=raw["tests"], known_user_modules=known, scope=scope, test_times=raw["test_times"])
        rows.append({"id": raw["id"], "site": raw["site"], "selected_tests": raw["tests"], "actual_tests": result.test_ids_run, "tests_run": result.tests_run, "reported_inner_seconds": result.time_seconds, "control_confirmation_counts": None, "status": result.status, "timeout": "<timeout>" in result.killing_tests, "error": result.error, "killing_tests": result.killing_tests, "runner_seconds": time.monotonic() - start, "start_ns": utc, "end_ns": time.time_ns()})
        print(json.dumps({"phase": "mutant", **rows[-1]}), flush=True)
    require(source_digest() == plan["context"]["source_digest"], "source changed during execution")
    value = {"schema": RESULT, "complete": True, "context": context(), "plan_id": plan["plan_id"], "runtime": runtime(), "shard_index": args.index, "shard_count": args.count, "baseline_passed": True, "baseline_tests_run": base.collector.total, "baseline_seconds": base.elapsed, "start_ns": ENTRY_UTC, "end_ns": time.time_ns(), "wall_seconds": time.monotonic() - ENTRY_START, "results": rows}
    atomic(Path(args.output), value)
    return int(any(r["status"] != "killed" or r["timeout"] or r["error"] is not None for r in rows))


def aggregate_command(args):
    plan = load(Path(args.plan).read_text()); inventory_check(plan, targets(args))
    bundles = [load(p.read_text()) for p in Path(args.results).rglob("result.json")]
    serial = None
    if args.serial:
        refs = list(Path(args.serial).rglob("result.json"))
        require(len(refs) == 1, "missing/duplicate serial artifact")
        serial = load(refs[0].read_text())
    report = aggregate(plan, bundles, context(), serial, context()["serial_required"])
    atomic(Path(args.output), report); print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    for name in ["plan", "dependencies", "run", "aggregate"]:
        p = sub.add_parser(name); p.add_argument("--target", action="append"); p.add_argument("--output", required=True)
        if name == "plan": p.add_argument("--seed")
        else: p.add_argument("--plan", required=True)
        if name == "run": p.add_argument("--index", type=int, required=True); p.add_argument("--count", type=int, default=2); p.add_argument("--wait-stdin", action="store_true")
        if name == "aggregate": p.add_argument("--results", required=True); p.add_argument("--serial")
    args = parser.parse_args()
    if args.command == "plan": plan_command(args); return 0
    if args.command == "dependencies": dependencies_command(args); return 0
    if args.command == "run": return execute_command(args)
    aggregate_command(args); return 0


if __name__ == "__main__":
    raise SystemExit(main())
