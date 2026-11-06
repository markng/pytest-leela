"""Disposable task-only native partition; full denominator retained, passive module binding."""
import ast
import dataclasses
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.coverage_tracker import CoveragePlugin
from pytest_leela.engine import Engine
from pytest_leela.models import Mutant
from pytest_leela.operators import DEFAULT_OPERATORS, ALL_OPERATORS, build_allowed_keys, mutations_for
from pytest_leela.runner import ProjectModuleScope, run_control, run_tests_for_mutant
from pytest_leela.type_extractor import enrich_mutation_points

component, scope, partition = sys.argv[1:]
assert component in ('runner', '_timeout_process', '_timeout_guardian', '_timeout_worker',
                     'describe_runner', 'describe_timeout_process', 'describe_runner_timeout',
                     'describe_a_timeout_engine_contract')
assert scope in ('default', 'all')
root = Path.cwd()
base = Path(__file__).resolve().parents[1]
run = base / (component + '-' + scope + '-' + uuid.uuid4().hex)
run.mkdir()
test_component = component.startswith('describe_')
path = str(root / ('tests/leela' if test_component else 'src/pytest_leela') / (component + '.py'))
module = ('tests.leela.' if test_component else 'pytest_leela.') + component
source = Path(path).read_text()
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
points, stats = enrich_mutation_points(source, find_mutation_points(source, path, module))
changed = None
if component in ('runner', 'describe_runner'):
    def functions(tree, prefix=''):
        result = {}
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + node.name
                if not isinstance(node, ast.ClassDef):
                    result[qual] = node
                result.update(functions(node, qual + '.'))
        return result
    old = functions(ast.parse(subprocess.check_output(['git', 'show', 'HEAD:'+str(Path(path).relative_to(root))], text=True)))
    new = functions(ast.parse(source))
    changed = {name: (node.lineno, node.end_lineno) for name, node in new.items()
               if name not in old or ast.dump(old[name]) != ast.dump(node)}
    points = [p for p in points if any(a <= p.lineno <= b for a, b in changed.values())]
categories = DEFAULT_OPERATORS if scope == 'default' else ALL_OPERATORS
mutants = [Mutant(p, op, i) for i, (p, op) in enumerate(
    (p, op) for p in points for op in mutations_for(p, True, build_allowed_keys(categories)))]
selected_ordinals = set(range(len(mutants))) if partition == 'full' else {int(v) for v in partition.split(',')}
assert selected_ordinals and selected_ordinals <= set(range(len(mutants)))
tests = ['tests/leela/' + component + '.py'] if test_component else ['tests/leela/describe_timeout_process.py']
if component == 'runner':
    tests += ['tests/leela/describe_runner.py', 'tests/leela/describe_runner_timeout.py']
inputs = {str(p.relative_to(root)): sha(p) for directory in ('src/pytest_leela', 'tests')
          for p in sorted((root / directory).rglob('*.py')) if '__pycache__' not in p.parts}
inputs.update({p:sha(p) for p in ('pyproject.toml','uv.lock',str(Path(__file__).relative_to(root)),
                               str((Path(__file__).parent/'passive_timeout_observer.py').relative_to(root)))})
ledger = [{
    'id': f'{component}:{scope}:{m.mutant_id}:{m.point.lineno}:{m.point.col_offset}:{m.replacement_op}',
    'mutant': dataclasses.asdict(m), 'status': 'pending' if m.mutant_id in selected_ordinals else 'not-in-this-partition',
    'genuine_call': False,
} for m in mutants]
manifest = {'source_sha': sha(path), 'inputs':inputs, 'component':component, 'scope':scope,
            'categories':categories, 'changed_functions':changed, 'denominator':len(mutants),
            'partition':sorted(selected_ordinals), 'python':sys.version, 'executable':sys.executable,
            'pytest':pytest.__version__, 'leela':importlib.metadata.version('pytest-leela'),
            'private_tmp':os.environ['TMPDIR'], 'private_home':os.environ['HOME'],
            'assertion_mode':'plain (matched baseline/control/mutant)' if test_component else 'configured rewrite',
            'command':sys.argv, 'ledger':ledger}
def save():
    (run/'ledger.json').write_text(json.dumps(manifest,indent=2)+'\n')
save()
print('RUN', run, 'DENOMINATOR',len(mutants),'PARTITION',sorted(selected_ordinals),flush=True)
collector = CoveragePlugin({path})
# pytest's assertion-rewrite finder precedes native MutatingFinder for test
# modules. Its supported plain mode preserves ordinary assert semantics and
# lets the real native loader execute the actual test mutation. Both controls
# and mutant use the identical mode; no test/point/operator exclusion.
rc = pytest.main([*tests,'-q','-p','no:leela','-o','cache_dir='+str(run/'pytest-cache')],plugins=[collector])
manifest['baseline_exit'] = int(rc)
save()
assert rc == 0, 'ordinary coverage control failed'
coverage = collector.coverage_map
coverage.test_times = collector.test_times
assert coverage.test_times
manifest['baseline_test_times'] = coverage.test_times
manifest['coverage'] = [{'file':f,'line':line,'ids':sorted(ids)} for (f,line),ids in coverage.line_to_tests.items()]
sys.path.insert(0,str(Path(__file__).parent))
os.environ['PYTHONPATH']=os.pathsep.join([str(Path(__file__).parent),os.environ.get('PYTHONPATH','')])
for mutant in mutants:
    if mutant.mutant_id not in selected_ordinals:
        continue
    record = ledger[mutant.mutant_id]
    ids = Engine._tests_for(mutant,coverage,sorted(coverage.test_times))
    token = uuid.uuid4().hex
    events = run/(str(mutant.mutant_id)+'-'+token+'.jsonl')
    assert not events.exists()
    record.update(status='control-started',selected=ids,events=str(events),invocation=token)
    save()
    os.environ['PYTEST_PLUGINS']='passive_timeout_observer'
    if test_component:
        os.environ['LEELA_PROOF_TESTCODE']='1'
        os.environ['PYTEST_ADDOPTS']='--assert=plain'
    os.environ['LEELA_PROOF_EVENTS']=str(events)
    os.environ['LEELA_PROOF_INVOCATION']=token+'-control'
    control = run_control(ids,None,frozenset(),ProjectModuleScope(),coverage.test_times)
    record['control']={'error':control.error(),'failures':control.failures(),'total':control.collector.total,
                       'passed':control.collector.passed,'elapsed':control.elapsed}
    save()
    if control.error() is not None or control.failures():
        record.update(status='control-failed', error_classification='instrumented-clean-control-failure',
                      next_action='repair diagnostic environment; no mutant activation or kill credit')
        save()
        raise AssertionError(record['control'])
    os.environ['LEELA_PROOF_INVOCATION']=token+'-mutant'
    record.update(status='activation-started',phases=[])
    save()
    def phase(name, timeout, timed_out):
        record['phases'].append({'name':name,'timeout':timeout,'timed_out':timed_out})
        save()
    result = run_tests_for_mutant(mutant,{module:source},{module:path},test_ids=ids,
                                  test_times=coverage.test_times,on_phase=phase)
    del os.environ['PYTEST_PLUGINS']
    observed = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
    active = [e for e in observed if e['invocation']==token+'-mutant']
    clean = [e for e in observed if e['invocation']==token+'-control']
    bindings = [e for e in active if e['event']=='loader-end' and e['module']==module
                and e['filename']==path and e['source_sha']==sha(path)
                and e['mutant']==dataclasses.asdict(mutant) and e['completed'] and e['applied']
                and e['ast_changed'] and len(e['executed_modules'])==1
                and e['executed_modules'][0]['code_sha']==e['expected_module_sha']
                and e['executed_modules'][0]['filename']==path
                and e['live_functions']
                and all(f['sha']==f['expected_sha'] for f in e['live_functions'] if f['filename']==path)
                and any(f['filename']==path for f in e['live_functions'])]
    calls = [e for e in active if e['event']=='report' and e['phase']=='call'
             and e['outcome']=='failed' and e['nodeid'] in result.killing_tests]
    clean_pass = [e for e in clean if e['event']=='report' and e['phase']=='call' and e['outcome']=='passed'
                  and e['nodeid'] in ids]
    genuine = bool(result.killed and '<timeout>' not in result.killing_tests and bindings and calls
                   and any(b['pid']==c['pid'] and any(p['nodeid']==c['nodeid'] for p in clean_pass)
                           for b in bindings for c in calls))
    record.update(status=result.status,result=dataclasses.asdict(result),genuine_call=genuine,
                  module_bindings=bindings,call_records=calls,clean_call_records=clean_pass,
                  error_classification='test-detected-CALL' if genuine else 'unqualified-'+result.status,
                  next_action='none' if genuine else 'inspect causal evidence; no kill credit')
    save()
    print(record['id'],record['status'],'genuine',genuine,result.killing_tests,flush=True)
assert all(sha(p)==digest for p,digest in inputs.items()), 'relevant inputs changed during run'
manifest['partition_complete']=True
manifest['genuine_call_count']=sum(r['genuine_call'] for r in ledger)
save()
print('COMPLETE',len(selected_ordinals),'GENUINE',manifest['genuine_call_count'],'FULL_DENOMINATOR',len(mutants),flush=True)
