"""Disposable source-bound AST/statement/branch inventory diagnostic."""
import ast, hashlib, json
from pathlib import Path
import sys
from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.type_extractor import enrich_mutation_points
from pytest_leela.operators import DEFAULT_OPERATORS, ALL_OPERATORS, build_allowed_keys, mutations_for
root=Path.cwd();out=Path(sys.argv[1]);paths=sorted(list((root/'src/pytest_leela').glob('*.py'))+list((root/'tests/leela').glob('*.py')))
records=[]; total=0
for p in paths:
 s=p.read_text(); tree=ast.parse(s);module=p.relative_to(root).with_suffix('').as_posix().replace('/','.')
 statements=[n for n in ast.walk(tree) if isinstance(n,ast.stmt)]
 branches=[n for n in ast.walk(tree) if isinstance(n,(ast.If,ast.For,ast.AsyncFor,ast.While,ast.Try,ast.Match,ast.IfExp,ast.comprehension))]
 points,stats=enrich_mutation_points(s,find_mutation_points(s,str(p),module))
 domains={}
 for name,ops in [('default',DEFAULT_OPERATORS),('all',ALL_OPERATORS)]:
  muts=[(pt,op) for pt in points for op in mutations_for(pt,True,build_allowed_keys(ops))]
  domains[name]={'denominator':len(muts),'mutants':[{'id':i,'line':pt.lineno,'column':pt.col_offset,'node':pt.node_type,'original':pt.original_op,'replacement':op,'inferredType':pt.inferred_type} for i,(pt,op) in enumerate(muts)]}
 rec={'path':str(p.relative_to(root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'statements':len(statements),'statementNodes':[{'type':type(n).__name__,'line':n.lineno,'end':n.end_lineno} for n in statements],'branchConstructs':len(branches),'branchNodes':[{'type':type(n).__name__,'line':getattr(n,'lineno',None)} for n in branches],'nativeEnrichment':{'annotations':stats.from_annotations,'assignmentDataflow':stats.from_assignment_dataflow},'DEFAULT':domains['default'],'ALL':domains['all']}
 records.append(rec);total+=len(statements)
(out/'source-domains.json').write_text(json.dumps({'sourceRoot':str(root),'files':records,'astStatementTotal':total,'branchConstructTotal':sum(x['branchConstructs'] for x in records),'nativeDefaultTotal':sum(x['DEFAULT']['denominator'] for x in records),'nativeAllTotal':sum(x['ALL']['denominator'] for x in records)},indent=2)+'\n')
print(json.dumps({'files':len(records),'astStatements':total,'branchConstructs':sum(x['branchConstructs'] for x in records),'nativeDEFAULT':sum(x['DEFAULT']['denominator'] for x in records),'nativeALL':sum(x['ALL']['denominator'] for x in records)}))
