"""Proportional controls for the disposable producer qualification artifacts."""
import json
from pathlib import Path
import py_compile
import subprocess
import sys

run = Path(sys.argv[1]).resolve()
probe = Path('.pi/django-formatter-repair/producer_qualification.py').resolve()
metrics = Path('.pi/django-formatter-repair/producer_metrics.py').resolve()
for path in (probe, metrics):
    py_compile.compile(str(path), doraise=True)
for scope in ('default', 'all'):
    report = json.loads((run / ('producer-' + scope + '.json')).read_text())
    assert report['denominator'] == 17 and report['genuineCallCount'] == 17
    assert len(report['ledger']) == 17
    for item in report['ledger']:
        assert item['status'] == 'genuine-CALL'
        assert len(item['controlCalls']) == len(item['bindings']) == len(item['failedCalls']) == 1
        clean, binding, failed = item['controlCalls'][0], item['bindings'][0], item['failedCalls'][0]
        assert clean['pid'] != binding['pid']
        assert binding['pid'] == failed['pid'] and binding['invocation'] == failed['invocation'] == item['job'] + '-mutant'
        assert clean['invocation'] == item['job'] + '-control'
        assert binding['mutant'] == item['mutant'] and binding['source_sha'] == report['sourceSha256']
        assert binding['applied'] and binding['ast_changed']
        assert binding['executed_modules'][0]['code_sha'] == binding['expected_module_sha']
        assert failed['phase'] == 'call' and failed['nodeid'] in item['result']['killing_tests']
        assert not [event for event in item['bindings'] if event['invocation'] == item['job'] + '-control']
# An unsupported scope is rejected before any test/subject execution.
negative = subprocess.run([sys.executable, str(probe), str(run), 'unsupported'], capture_output=True, text=True, timeout=5)
assert negative.returncode != 0 and 'AssertionError' in negative.stderr
metric = json.loads((run / 'producer-metrics.json').read_text())
assert metric['pytestExit'] == 0 and metric['branchAstNodes'] == 0
assert metric['statementNodesLineObserved'] == 15 and len(metric['unobservedStatementNodes']) == 2
print('PASS: syntax, 34 real positive joins, wrong-job/PID nonjoin, unsupported-scope negative, and metric artifact checks')
