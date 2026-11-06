"""Disposable source-bound statement/branch measurement for _django_project."""
import ast
import json
from pathlib import Path
import sys
import pytest
from pytest_leela.coverage_tracker import CoveragePlugin

run = Path(sys.argv[1]); target = Path('tests/leela/describe_end_to_end.py').resolve(); source=target.read_text(); tree=ast.parse(source)
producer=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_django_project')
statements=[n for n in ast.walk(producer) if isinstance(n,ast.stmt)]
branches=[n for n in ast.walk(producer) if isinstance(n,(ast.If,ast.For,ast.While,ast.Try,ast.Match,ast.IfExp))]
plugin=CoveragePlugin({str(target)})
node='tests/leela/describe_end_to_end.py::describe_django_project::it_imports_models_admin_and_references_once_per_process'
rc=pytest.main([node,'-q','-p','no:leela','-p','no:cacheprovider','--basetemp='+str((run/'metrics-basetemp').resolve())],plugins=[plugin])
hit=sorted(line for file,line in plugin.coverage_map.line_to_tests if Path(file).resolve()==target)
executed=[n for n in statements if n.lineno in hit]
unhit=[{'type':type(n).__name__,'line':n.lineno,'end':n.end_lineno} for n in statements if n.lineno not in hit]
out={'classification':'disposable affected-producer source metric','target':str(target),'producer':{'name':'_django_project','lines':[producer.lineno,producer.end_lineno]},'pytestExit':int(rc),'node':node,'statementAstNodes':len(statements),'statementNodesLineObserved':len(executed),'unobservedStatementNodes':unhit,'branchAstNodes':len(branches),'lineHits':hit,'coverageTestTimes':plugin.test_times,'coverageMap':{f'{file}:{line}':sorted(ids) for (file,line),ids in plugin.coverage_map.line_to_tests.items()}}
(run/'producer-metrics.json').write_text(json.dumps(out,indent=2)+'\n')
assert rc==0
print(json.dumps({'exit':int(rc),'statements':len(statements),'lineObserved':len(executed),'branches':len(branches),'unobserved':len(unhit)},sort_keys=True))
