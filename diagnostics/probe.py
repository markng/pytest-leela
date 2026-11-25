"""Non-scoring, bounded 776 diagnostic; never emits a native CI result bundle.

Historical actual_tests is a passed/failed/errors bag, NOT a phase trace.
The two historical bags are identical. These are same-host fresh-process
replays of that input, NOT replays of 776/388 preceding mutations.
"""
from __future__ import annotations
import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

SOURCE = "3ec05c436b3db0d129fc182c6f06fd82a07c8c69"
TREE = "ea5478cf5fdc815bb12be6679177d118d6ea1f9d"
PLAN_SHA = "8bff5efe68c82cc450de57ea42b63de58d4c2eaabe2897287cbd3edf4124e452"


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(value, f, indent=2, allow_nan=False); f.write("\n")
        f.flush(); os.fsync(f.fileno())


def load_plan(path):
    data = Path(path).read_bytes()
    assert hashlib.sha256(data).hexdigest() == PLAN_SHA, "origin plan byte drift"
    plan = json.loads(data)
    assert plan["context"]["sha"] == SOURCE and plan["context"]["tree"] == TREE
    assert plan["context"]["run_id"] == "45560790046"
    assert plan["context"]["python"] == "3.14" and len(plan["modules"]) == 16
    assert len(plan["mutants"]) == 909 and plan["mutants"][776]["id"] == 776
    return plan


def telemetry():
    result = {"process_cpu_seconds": time.process_time(), "affinity": sorted(os.sched_getaffinity(0))}
    for name in ["/proc/stat", "/sys/fs/cgroup/cpu.stat"]:
        try: result[name] = Path(name).read_text()
        except OSError as e: result[name] = {"unavailable": type(e).__name__}
    try:
        result["cpu_models"] = sorted(set(line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name")))
    except OSError as e: result["cpu_models"] = {"unavailable": type(e).__name__}
    result["rusage"] = list(resource.getrusage(resource.RUSAGE_SELF))
    return result


class Observation:
    """Pass-through public pytest hooks, separated by nested main invocation."""
    def __init__(self):
        self.events = []; self.timers = []; self.stack = []; self.serial = 0
        self.stage = "unset"; self.known_events = {}

    def emit(self, kind, **fields):
        self.events.append({"kind": kind, "stage": self.stage, "monotonic_ns": time.monotonic_ns(), "invocation": self.stack[-1] if self.stack else None, "depth": len(self.stack), **fields})

    def call(self, function):
        import pytest
        old_main = pytest.main; old_start = threading.Timer.start
        owner = self
        def start(timer, *args, **kwargs):
            event = getattr(getattr(timer, "function", None), "__self__", None)
            value = {"interval": timer.interval, "event_id": id(event), "before_start_ns": time.monotonic_ns(), "stage": owner.stage}
            result = old_start(timer, *args, **kwargs)
            value["after_start_ns"] = time.monotonic_ns()
            owner.timers.append(value)
            if isinstance(event, threading.Event): owner.known_events[id(event)] = event
            return result
        def main(args=None, plugins=None):
            invocation = owner.serial; owner.serial += 1
            timeout = [getattr(p, "event") for p in plugins or [] if type(p).__name__ == "_TimeoutPlugin"]
            class Trace:
                def pytest_collection_modifyitems(self, session, config, items):
                    owner.emit("collection", nodeids=[i.nodeid for i in items])
                def pytest_runtest_logstart(self, nodeid, location):
                    owner.emit("test_start", nodeid=nodeid)
                def pytest_runtest_logreport(self, report):
                    owner.emit("phase", nodeid=report.nodeid, when=report.when, outcome=report.outcome, duration=report.duration, native_timeout_seen=[e.is_set() for e in timeout])
                def pytest_runtest_logfinish(self, nodeid, location):
                    owner.emit("test_finish", nodeid=nodeid)
            owner.stack.append(invocation)
            owner.emit("main_begin", args=list(args or []), timeout_event_ids=[id(e) for e in timeout])
            try: return old_main(args, plugins=[*(plugins or []), Trace()])
            finally:
                owner.emit("main_end", native_timeout_seen=[e.is_set() for e in timeout]); owner.stack.pop()
        pytest.main = main; threading.Timer.start = start
        try: return function()
        finally: pytest.main = old_main; threading.Timer.start = old_start


def child(args):
    sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / "src")]
    from ci.mutations import runtime, source_digest
    from pytest_leela.engine import _module_name_from_path
    from pytest_leela.models import Mutant, MutationPoint
    from pytest_leela.runner import ProjectModuleScope, precompute_user_modules, run_baseline, run_tests_for_mutant
    plan = load_plan(args.plan); raw = plan["mutants"][776]
    retained_bytes = Path(__file__).with_name("retained776.json").read_bytes()
    assert hashlib.sha256(retained_bytes).hexdigest() == "8703460cb83c44435b86f6d5ccebe67ddfb93e5fcfc32a6753ad94e4513c9bcd"
    retained = json.loads(retained_bytes)
    assert retained["origin_plan_sha256"] == PLAN_SHA and retained["origin_plan_id"] == plan["plan_id"]
    historical = retained["rows"]["serial" if args.mode.startswith("serial") else "shard"]
    assert historical["selected_tests"] == raw["tests"]
    receipt = {"schema": "t44.776-diagnostic/v1", "diagnostic_only": True, "mode": args.mode, "prior_mutant_state_prefix_replayed": False, "origin_plan_id": plan["plan_id"], "origin_plan_sha256": PLAN_SHA, "current_hosted_run": os.environ.get("GITHUB_RUN_ID"), "source": SOURCE, "modules": plan["modules"], "selected_tests": raw["tests"], "producer_test_times": raw["test_times"], "measurements": {}, "complete": False}
    receipt["historical_reported_bag_NOT_ordered_phase_trace"] = historical
    out = Path(args.output)
    save(out / "receipt.json", receipt)
    observer = None
    try:
        assert subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == SOURCE
        assert source_digest() == plan["context"]["source_digest"]
        observed = runtime(); receipt["runtime_difference_fields"] = [k for k in set(observed) | set(plan["runtime"]) if observed.get(k) != plan["runtime"].get(k)]
        save(out / "runtime.json", {"expected": plan["runtime"], "observed": observed})
        assert not receipt["runtime_difference_fields"], "exact immutable runtime unavailable"
        module_files = {_module_name_from_path(str(Path(f).resolve())): str(Path(f).resolve()) for f in plan["targets"]}
        sources = {n: Path(f).read_text() for n, f in module_files.items()}
        assert sorted(sources) == plan["modules"]
        point = dict(raw["point"]); point["file_path"] = str(Path(point["file_path"]).resolve())
        mutant = Mutant(MutationPoint(**point), raw["replacement"], 776)
        scope = ProjectModuleScope(); known = precompute_user_modules(scope)
        all_ids = sorted({t for row in plan["mutants"] for t in row["tests"]})
        started = time.monotonic(); base = run_baseline(all_ids, None, known, scope)
        receipt["full_unmutated_baseline"] = {"elapsed": time.monotonic()-started, "calls": base.collector.total, "error": base.error(), "failures": base.failures()}
        assert base.error() is None and not base.failures(), "unmutated baseline failed; no mutant run"
        observer = Observation()
        def warm(label):
            observer.stage = label
            started = time.monotonic()
            b = observer.call(lambda: run_baseline(raw["tests"], None, known, scope))
            receipt["measurements"][label] = {"wall": time.monotonic()-started, "calls": b.collector.total, "error": b.error(), "failures": b.failures()}
            assert b.error() is None and not b.failures(), "local warm baseline failed"
        def execute(label, traced):
            observer.stage = label; before = telemetry(); start = time.monotonic_ns()
            call = lambda: run_tests_for_mutant(mutant, sources, module_files, test_ids=raw["tests"], known_user_modules=known, scope=scope, test_times=raw["test_times"])
            r = observer.call(call) if traced else call()
            receipt["measurements"][label] = {"start_ns": start, "end_ns": time.monotonic_ns(), "status": r.status, "error": r.error, "killing_tests": r.killing_tests, "tests_run": r.tests_run, "reported_inner_seconds": r.time_seconds, "actual_tests_reported_bag": r.test_ids_run, "before": before, "after": telemetry()}
            save(out / "receipt.json", receipt); save(out / "ordered-events.json", observer.events); save(out / "actual-timers.json", observer.timers)
        warm("local_warm_before_control")
        execute("776_uninstrumented_control", False)
        warm("local_warm_before_trace")
        execute("776_observed", True)
        receipt["budget_seconds_derived_frozen_native_formula"] = max(2*sum(raw["test_times"].get(t,1.0) for t in raw["tests"])+1.0,5.0)
        a = receipt["measurements"]["776_uninstrumented_control"]; b = receipt["measurements"]["776_observed"]
        receipt["observer_signature_equal"] = all(a[k] == b[k] for k in ["status", "error", "killing_tests"])
        receipt["local_warm_baseline_per_test"] = {}
        for event in observer.events:
            if event["kind"] == "phase" and event["depth"] == 1 and event["stage"].startswith("local_warm"):
                receipt["local_warm_baseline_per_test"].setdefault(event["stage"], {}).setdefault(event["nodeid"], {})[event["when"]] = {"duration": event["duration"], "outcome": event["outcome"]}
        root_events = [e for e in observer.events if e["kind"] == "main_begin" and e["depth"] == 1 and e["stage"] == "776_observed"]
        event_ids = {i for e in root_events for i in e["timeout_event_ids"]}
        receipt["actual_root_native_timer_records"] = [t for t in observer.timers if t["event_id"] in event_ids]
        assert receipt["actual_root_native_timer_records"], "native timer not observed; no budget proof"
        assert all(abs(t["interval"]-receipt["budget_seconds_derived_frozen_native_formula"]) < 1e-9 for t in receipt["actual_root_native_timer_records"]), "native budget changed"
        receipt["ordered_observed_root_phases"] = [e for e in observer.events if e["depth"] == 1 and e["stage"] == "776_observed" and e["kind"] in ["test_start", "phase", "test_finish"]]
        receipt["local_warm_timing_method"] = "Public setup/call/teardown report durations and logstart/logfinish intervals, no coverage tracer; NOT identical measurement scope to frozen producer CoveragePlugin. Diagnostics only, never substituted for frozen inputs."
        assert source_digest() == plan["context"]["source_digest"]
        receipt["complete"] = True
    except BaseException as e:
        receipt["failure"] = {"type": type(e).__name__, "message": str(e), "traceback": traceback.format_exc()}
        raise
    finally:
        save(out / "receipt.json", receipt)
        if observer is not None:
            save(out / "ordered-events.json", observer.events)
            save(out / "actual-timers.json", observer.timers)


def main():
    p = argparse.ArgumentParser(); p.add_argument("action", choices=["pins", "run", "child"]); p.add_argument("--plan", required=True); p.add_argument("--output", required=True); p.add_argument("--mode"); args=p.parse_args()
    plan = load_plan(args.plan)
    if args.action == "pins":
        pins=[]
        for n,v in sorted(plan["runtime"]["packages"].items()):
            assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*",n) and re.fullmatch(r"[A-Za-z0-9.!+_-]+",v)
            if n!="pytest-leela": pins.append(f"{n}=={v}")
        Path(args.output).write_text("\n".join(pins)+"\n"); return
    if args.action == "child": child(args); return
    output=Path(args.output).resolve(); output.mkdir(parents=True,exist_ok=True)
    source=Path.cwd().resolve(); results=[]
    for mode in ["serial-reported-prefix", "shard-reported-prefix"]:
        root=output/mode; root.mkdir(); checkout=root/"source"
        subprocess.run(["git","clone","--no-hardlinks",str(source),str(checkout)],check=True)
        subprocess.run(["git","-C",str(checkout),"checkout","--detach",SOURCE],check=True)
        env=dict(os.environ)
        for key,part in [("HOME","home"),("TMPDIR","tmp"),("XDG_CACHE_HOME","cache")]:
            d=root/part;d.mkdir();env[key]=str(d)
        env["PYTHONPATH"]=str(checkout/"src")+os.pathsep+str(checkout)
        proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),"child","--plan",str(Path(args.plan).resolve()),"--output",str(root),"--mode",mode],cwd=checkout,env=env,start_new_session=True)
        external_stop=False
        try: code=proc.wait(timeout=330)
        except subprocess.TimeoutExpired:
            external_stop=True; os.killpg(proc.pid,signal.SIGTERM)
            try: code=proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL); code=proc.wait(timeout=10)
        results.append({"mode":mode,"exit":code,"external_stop_NOT_native_timeout":external_stop})
        if code: break
        shutil.rmtree(checkout)  # Only this newly created diagnostic copy, after source recheck.
    save(output/"summary.json",{"diagnostic_only":True,"does_not_satisfy_ci":True,"origin_plan_sha256":PLAN_SHA,"modes":results,"full_historical_mutant_prefix_replayed":False})
    if any(x["exit"] for x in results): raise SystemExit(1)

if __name__ == "__main__": main()
