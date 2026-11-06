"""Disposable single-task checks for generated qualification evidence."""
import json
from pathlib import Path
import py_compile
import subprocess
import sys

run = Path(sys.argv[1]).resolve()
probe = Path('.pi/generated-django-qualification/qualification_probe.py').resolve()
py_compile.compile(str(probe), doraise=True)
paths = list(run.glob('*shop_pricing*.json'))
assert len(paths) == 5
for path in paths:
    item = json.loads(path.read_text())
    assert item['status'] == 'genuine-CALL' and item['denominator'] == 3
    assert len(item['clean_calls']) == len(item['bindings']) == len(item['failed_calls']) == 1
    binding, failed, clean = item['bindings'][0], item['failed_calls'][0], item['clean_calls'][0]
    assert binding['pid'] == failed['pid'] and binding['invocation'] == failed['invocation'] == item['job'] + '-mutant'
    assert clean['pid'] != binding['pid'] and clean['invocation'] == item['job'] + '-control'
    assert not [x for x in item['bindings'] if x['invocation'] == item['job'] + '-control']
    assert binding['source_sha'] == item['source_sha'] and binding['mutant'] == item['selected']
    assert binding['applied'] and binding['ast_changed'] and binding['executed_modules'][0]['code_sha'] == binding['expected_module_sha']
    assert failed['nodeid'] in item['result']['killing_tests'] and failed['phase'] == 'call'
admin = json.loads(next(run.glob('default-shop_admin-0-*.json')).read_text())
assert admin['status'] == 'unqualified-error' and admin['result']['tests_run'] == 0
assert admin['clean_calls'][0]['outcome'] == 'passed' and not admin['bindings'] and not admin['failed_calls']
negative = run / 'negative-project'
negative.mkdir()
p = subprocess.run([sys.executable, str(probe), str(run), str(negative), 'shop.pricing', 'default', '0'], capture_output=True, text=True, timeout=5)
assert p.returncode != 0 and 'shop/pricing.py' in p.stderr
print('PASS: syntax, 5 positive native joins, wrong-job/PID rejection, admin error classification, missing-target rejection')
