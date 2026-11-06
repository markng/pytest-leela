"""Disposable, single-investigation bounded generated-source observer."""
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

root = Path.cwd().resolve()
run, project = (Path(x).resolve() for x in sys.argv[1:3])
module, scope_name, ordinal = sys.argv[3], sys.argv[4], int(sys.argv[5])
assert module in ('shop.pricing', 'shop.admin') and scope_name in ('default', 'all')
target = project / (module.replace('.', '/') + '.py')
source = target.read_text()
sha = hashlib.sha256(source.encode()).hexdigest()
points, enrichment = enrich_mutation_points(source, find_mutation_points(source, str(target), module))
ops = DEFAULT_OPERATORS if scope_name == 'default' else ALL_OPERATORS
mutants = [Mutant(p, op, i) for i, (p, op) in enumerate((p, op) for p in points for op in mutations_for(p, True, build_allowed_keys(ops)))]
mutant = mutants[ordinal]
job = uuid.uuid4().hex
events = run / (job + '.jsonl')
observer = root / '.pi/recovered-helpers/passive_timeout_observer.py'
assert hashlib.sha256(observer.read_bytes()).hexdigest() == '9facc5f24b9d55c495e954fadac8c4427eb8acdf53d3b205a7580e36e15d8f54'
sys.path.insert(0, str(observer.parent)); sys.path.insert(0, str(project))
os.environ['PYTHONPATH'] = os.pathsep.join((str(observer.parent), str(project), str(root / 'src')))
os.environ['PYTEST_PLUGINS'] = 'passive_timeout_observer'
os.environ['LEELA_PROOF_EVENTS'] = str(events)
test_id = 'tests/test_shop.py::test_registry_and_identity_hold'
times = {test_id: 0.31}
scope = ProjectModuleScope(str(project))
os.chdir(project)
os.environ['LEELA_PROOF_INVOCATION'] = job + '-control'
control = run_control([test_id], None, frozenset(), scope, times)
assert control.error() is None and not control.failures(), (control.error(), control.failures())
os.environ['LEELA_PROOF_INVOCATION'] = job + '-mutant'
result = run_tests_for_mutant(mutant, {module: source}, {module: str(target)}, test_ids=[test_id], test_times=times, scope=scope)
records = [json.loads(line) for line in events.read_text().splitlines()]
clean = [e for e in records if e['invocation'] == job+'-control' and e['event']=='report' and e['phase']=='call' and e['outcome']=='passed' and e['nodeid']==test_id]
bind = [e for e in records if e['invocation']==job+'-mutant' and e['event']=='loader-end' and e['module']==module and e['filename']==str(target) and e['source_sha']==sha and e['mutant']==dataclasses.asdict(mutant) and e['completed'] and e['applied'] and e['ast_changed'] and len(e['executed_modules'])==1 and e['executed_modules'][0]['code_sha']==e['expected_module_sha'] and e['executed_modules'][0]['filename']==str(target)]
failed = [e for e in records if e['invocation']==job+'-mutant' and e['event']=='report' and e['phase']=='call' and e['outcome']=='failed' and e['nodeid'] in result.killing_tests]
genuine = bool(result.killed and result.error is None and clean and any(b['pid']==f['pid'] and b['invocation']==f['invocation'] for b in bind for f in failed) and '<timeout>' not in result.killing_tests)
output = {'job':job, 'module':module, 'scope':scope_name, 'target':str(target), 'source_sha':sha, 'denominator':len(mutants), 'mutants':[dataclasses.asdict(m) for m in mutants], 'enrichment':dataclasses.asdict(enrichment), 'selected':dataclasses.asdict(mutant), 'test_id':test_id, 'clean_calls':clean, 'bindings':bind, 'failed_calls':failed, 'result':dataclasses.asdict(result), 'status':'genuine-CALL' if genuine else 'unqualified-'+result.status, 'event_path':str(events), 'event_counts':{kind:sum(e['event']==kind for e in records if e['invocation']==job+'-mutant') for kind in ('configure','loader-start','loader-end','report')}, 'observer_sha':hashlib.sha256(observer.read_bytes()).hexdigest(), 'python':sys.executable}
(run/(scope_name+'-'+module.replace('.','_')+'-'+str(ordinal)+'-'+job+'.json')).write_text(json.dumps(output,indent=2)+'\n')
print(json.dumps({'job':job,'scope':scope_name,'module':module,'ordinal':ordinal,'denominator':len(mutants),'status':output['status'],'event_counts':output['event_counts'],'result':output['result']},sort_keys=True))
