"""Proportional checks for the disposable one-case Django binding diagnostic."""
from __future__ import annotations
import json
from pathlib import Path
import py_compile
import subprocess
import sys

script = Path('.pi/django-bootstrap-current/django_native_binding.py').resolve()
run = Path(sys.argv[1]).resolve()
binding = json.loads((run / 'native-binding.json').read_text())
py_compile.compile(str(script), doraise=True)
assert binding['classification'] == 'fresh-qualified-child-native-binding'
assert binding['binding']['pid'] == binding['failedCall']['pid']
assert binding['binding']['invocation'] == binding['failedCall']['invocation'] == binding['job'] + '-mutant'
assert binding['control']['pid'] != binding['binding']['pid']
assert binding['control']['outcome'] == 'passed'
assert binding['failedCall']['outcome'] == 'failed' and binding['failedCall']['phase'] == 'call'
assert binding['selectedMutant']['mutant_id'] == 1 and binding['selectedMutant']['replacement_op'] == 'Sub'
# Negative input: a non-Django/non-pricing project is rejected before activation.
case = run / 'negative-project'
case.mkdir()
(case / 'other.py').write_text('def value():\n    return 1\n')
completed = subprocess.run([sys.executable, str(script), str(run), str(case)], capture_output=True, text=True)
assert completed.returncode != 0
assert 'shop/pricing.py' in completed.stderr
print('PASS: diagnostic syntax, positive artifact/schema, wrong-job/PID nonjoin, and negative missing-target rejection')
