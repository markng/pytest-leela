"""Single-investigation pytest protocol trace for baseline isolation."""
from __future__ import annotations

import json
import os
from pathlib import Path

OUTPUT_DIR = Path(os.environ["BASELINE_TRACE_OUTPUT_DIR"])
EVENTS: dict[str, object] = {}
SERIAL = 0


def pytest_configure(config):
    EVENTS.clear()
    EVENTS["configure"] = {
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "rootpath": str(config.rootpath),
        "inipath": None if config.inipath is None else str(config.inipath),
        "invocationDir": str(config.invocation_params.dir),
        "args": list(config.invocation_params.args),
    }


def pytest_collection_finish(session):
    EVENTS["collection"] = [
        {
            "nodeid": item.nodeid,
            "path": str(item.path),
            "cwd": os.getcwd(),
            "rootpath": str(session.config.rootpath),
        }
        for item in session.items
    ]


def pytest_runtest_logreport(report):
    EVENTS.setdefault("reports", []).append(
        {"nodeid": report.nodeid, "phase": report.when, "outcome": report.outcome}
    )


def pytest_sessionfinish(session, exitstatus):
    global SERIAL
    EVENTS["finish"] = {
        "exitstatus": int(exitstatus),
        "cwd": os.getcwd(),
        "rootpath": str(session.config.rootpath),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"pytest-protocol-{os.getpid()}-{SERIAL}.json"
    SERIAL += 1
    output.write_text(json.dumps(EVENTS, indent=2, sort_keys=True) + "\n")
