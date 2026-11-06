"""Disposable pytest-session trace: enabled before test module collection."""
import hashlib
import json
import os
from pathlib import Path
import sys

TARGET = str(Path(os.environ['TRACE_TARGET']).resolve())
OUTPUT = Path(os.environ['TRACE_OUTPUT'])
HITS: set[int] = set()

def tracer(frame, event, arg):
    if frame.f_code.co_filename == TARGET:
        if event == 'line':
            HITS.add(frame.f_lineno)
        return tracer
    return None

def pytest_configure(config):
    sys.settrace(tracer)

def pytest_unconfigure(config):
    sys.settrace(None)

def pytest_sessionfinish(session, exitstatus):
    source = Path(TARGET).read_text()
    OUTPUT.write_text(json.dumps({'target': TARGET, 'sha256': hashlib.sha256(source.encode()).hexdigest(), 'exitstatus': int(exitstatus), 'lineHits': sorted(HITS)}, indent=2) + '\n')
