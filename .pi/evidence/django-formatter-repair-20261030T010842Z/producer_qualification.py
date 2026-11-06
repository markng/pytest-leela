"""Disposable full-domain observer for the affected _django_project producer."""
import ast
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.models import Mutant
from pytest_leela.operators import DEFAULT_OPERATORS, ALL_OPERATORS, build_allowed_keys, mutations_for
from pytest_leela.runner import ProjectModuleScope, run_control, run_tests_for_mutant
from pytest_leela.type_extractor import enrich_mutation_points

root = Path.cwd().resolve(); run = Path(sys.argv[1]).resolve(); scope_name = sys.argv[2]
assert scope_name in ('default', 'all')
target = root / 'tests/leela/describe_end_to_end.py'; module = 'tests.leela.describe_end_to_end'
source = target.read_text(); source_sha = hashlib.sha256(source.encode()).hexdigest()
tree = ast.parse(source); producer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_django_project')
points, enrichment = enrich_mutation_points(source, find_mutation_points(source, str(target), module))
operators = DEFAULT_OPERATORS if scope_name == 'default' else ALL_OPERATORS
mutants = [Mutant(point, op, i) for i, (point, op) in enumerate((point, op) for point in points if producer.lineno <= point.lineno <= producer.end_lineno for op in mutations_for(point, True, build_allowed_keys(operators)))]
assert len(mutants) == 17, len(mutants)
observer = root / '.pi/recovered-helpers/passive_timeout_observer.py'
assert hashlib.sha256(observer.read_bytes()).hexdigest() == '9facc5f24b9d55c495e954fadac8c4427eb8acdf53d3b205a7580e36e15d8f54'
sys.path.insert(0, str(observer.parent)); os.environ['PYTHONPATH'] = os.pathsep.join((str(observer.parent), str(root/'src')))
os.environ['PYTEST_PLUGINS'] = 'passive_timeout_observer'; os.environ['LEELA_PROOF_TESTCODE']='1'; os.environ['PYTEST_ADDOPTS']='--assert=plain'
test_id='tests/leela/describe_end_to_end.py::describe_django_project::it_imports_models_admin_and_references_once_per_process'
times={test_id:1.12}; project_scope=ProjectModuleScope(str(root))
ledger=[]
for mutant in mutants:
    job=uuid.uuid4().hex; events=run/(job+'.jsonl'); os.environ['LEELA_PROOF_EVENTS']=str(events)
    os.environ['LEELA_PROOF_INVOCATION']=job+'-control'; control=run_control([test_id],None,frozenset(),project_scope,times)
    assert control.error() is None and not control.failures(),(mutant,control.error(),control.failures())
    os.environ['LEELA_PROOF_INVOCATION']=job+'-mutant'
    result=run_tests_for_mutant(mutant,{module:source},{module:str(target)},test_ids=[test_id],test_times=times,scope=project_scope)
    records=[json.loads(line) for line in events.read_text().splitlines()]
    clean=[e for e in records if e['invocation']==job+'-control' and e['event']=='report' and e['phase']=='call' and e['outcome']=='passed' and e['nodeid']==test_id]
    bind=[e for e in records if e['invocation']==job+'-mutant' and e['event']=='loader-end' and e['module']==module and e['filename']==str(target) and e['source_sha']==source_sha and e['mutant']==dataclasses.asdict(mutant) and e['completed'] and e['applied'] and e['ast_changed'] and len(e['executed_modules'])==1 and e['executed_modules'][0]['code_sha']==e['expected_module_sha'] and e['executed_modules'][0]['filename']==str(target)]
    failed=[e for e in records if e['invocation']==job+'-mutant' and e['event']=='report' and e['phase']=='call' and e['outcome']=='failed' and e['nodeid'] in result.killing_tests]
    genuine=bool(result.killed and result.error is None and '<timeout>' not in result.killing_tests and len(clean)==1 and any(b['pid']==f['pid'] and b['invocation']==f['invocation'] for b in bind for f in failed))
    ledger.append({'job':job,'mutant':dataclasses.asdict(mutant),'controlCalls':clean,'bindings':bind,'failedCalls':failed,'result':dataclasses.asdict(result),'status':'genuine-CALL' if genuine else 'unqualified-'+result.status,'eventCounts':{n:sum(x['event']==n for x in records if x['invocation']==job+'-mutant') for n in ('configure','loader-start','loader-end','report')},'events':str(events)})
out={'classification':'disposable full affected-producer diagnostic','scope':scope_name,'source':str(target),'sourceSha256':source_sha,'module':module,'producer':{'name':'_django_project','lines':[producer.lineno,producer.end_lineno]},'denominator':len(mutants),'enrichment':dataclasses.asdict(enrichment),'testId':test_id,'testTimes':times,'observerSha256':hashlib.sha256(observer.read_bytes()).hexdigest(),'python':sys.executable,'ledger':ledger,'genuineCallCount':sum(x['status']=='genuine-CALL' for x in ledger)}
(run/('producer-'+scope_name+'.json')).write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'scope':scope_name,'denominator':len(mutants),'genuineCallCount':out['genuineCallCount'],'statuses':[x['status'] for x in ledger]},sort_keys=True))
