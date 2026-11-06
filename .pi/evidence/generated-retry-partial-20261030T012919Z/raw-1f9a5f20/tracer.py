import dis, sys, json, hashlib, ast, importlib.util, pathlib
TARGET = pathlib.Path(sys.argv[1]).resolve()
OUT = pathlib.Path(sys.argv[2])
seen = set(); events=[]
target_name = str(TARGET)
def trace(frame, event, arg):
    if event == "line" and frame.f_code.co_filename == target_name:
        seen.add(frame.f_lineno)
        return trace
    if event == "call" and frame.f_code.co_filename == target_name:
        return trace
    return None
import pytest
sys.settrace(trace)
try:
    rc=pytest.main([str(TARGET), "tests/leela/describe_end_to_end.py::describe_django_project::it_imports_models_admin_and_references_once_per_process", "-q", "-p", "no:cacheprovider", "--basetemp="+str(OUT.parent/'pytester-run')])
finally:
    sys.settrace(None)
s=TARGET.read_text(); tree=ast.parse(s); target=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_django_project")
def stack(fn):
    code=compile(ast.Module(body=[fn],type_ignores=[]),str(TARGET),"exec"); nested=code.co_consts[0]
    return {"moduleCode": {"co_lines":list(code.co_lines()),"nestedCodeLine":nested.co_firstlineno},"functionCode":{"co_firstlineno":nested.co_firstlineno,"co_name":nested.co_name,"co_lines":list(nested.co_lines()),"instructions":[{"op":i.opname,"line_number":i.line_number,"positions_lineno":i.positions.lineno if i.positions else None} for i in dis.get_instructions(nested)]}}
result={"python":sys.version,"target":str(TARGET),"sha256":hashlib.sha256(s.encode()).hexdigest(),"pytestExit":int(rc),"traceLines":sorted(seen),"producerAstStatements":[{"type":type(n).__name__,"line":n.lineno} for n in ast.walk(target) if isinstance(n,ast.stmt)],"compiled":stack(target),"moduleCodeLines":list(compile(s,str(TARGET),"exec").co_lines())}
OUT.write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps({"exit":int(rc),"traceTargetLine313":313 in seen,"traceLines":sorted(seen),"docstringLine314":314 in seen}))
