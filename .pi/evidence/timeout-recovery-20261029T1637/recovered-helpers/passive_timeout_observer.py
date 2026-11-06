"""Disposable task observer: native loader code, no extra execution or verdict changes."""
import ast
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import sys
import types

from pytest_leela.import_hook import MutantApplier, MutatingLoader

original_exec = MutatingLoader.exec_module
plain_options = {}


def code_digest(code):
    def constant(value):
        if isinstance(value, types.CodeType):
            return key(value)
        if isinstance(value, frozenset):
            return ['frozenset', sorted((constant(v) for v in value), key=repr)]
        if isinstance(value, tuple):
            return ['tuple', [constant(v) for v in value]]
        return repr(value)
    def key(value):
        constants = [constant(c) for c in value.co_consts]
        return [value.co_code.hex(), constants, value.co_names, value.co_varnames,
                value.co_freevars, value.co_cellvars, value.co_flags,
                value.co_firstlineno, value.co_linetable.hex(), value.co_exceptiontable.hex()]
    return hashlib.sha256(json.dumps(key(code)).encode()).hexdigest()


def write(event):
    event.update(pid=os.getpid(), invocation=os.environ['LEELA_PROOF_INVOCATION'])
    with Path(os.environ['LEELA_PROOF_EVENTS']).open('a') as output:
        output.write(json.dumps(event) + '\n')


def observe(loader, module):
    original_tree = ast.parse(loader.source, filename=loader.filename)
    mutated_tree = ast.parse(loader.source, filename=loader.filename)
    applier = MutantApplier(loader.mutant)
    mutated_tree = ast.fix_missing_locations(applier.visit(mutated_tree))
    expected = compile(mutated_tree, loader.filename, 'exec', dont_inherit=True)
    module_frames = []
    previous = sys.getprofile()

    def capture(frame, event, arg):
        if event == 'call' and frame.f_code.co_name == '<module>':
            if frame.f_globals is module.__dict__ and frame.f_code.co_filename == loader.filename:
                module_frames.append({'code_sha': code_digest(frame.f_code),
                                      'filename': frame.f_code.co_filename})
        if previous is not None:
            previous(frame, event, arg)

    write({'event': 'loader-start', 'module': module.__name__, 'filename': loader.filename,
           'source_sha': hashlib.sha256(loader.source.encode()).hexdigest(),
           'mutant': dataclasses.asdict(loader.mutant)})
    completed = False
    try:
        sys.setprofile(capture)
        result = original_exec(loader, module)
        completed = True
        return result
    finally:
        sys.setprofile(previous)
        expected_functions = {}
        def walk(code):
            expected_functions[code.co_qualname] = code_digest(code)
            for value in code.co_consts:
                if isinstance(value, types.CodeType):
                    walk(value)
        walk(expected)
        live = []
        for value in vars(module).values():
            values = vars(value).values() if isinstance(value, type) and value.__module__ == module.__name__ else [value]
            for function in values:
                if isinstance(function, (staticmethod, classmethod)):
                    function = function.__func__
                if isinstance(function, types.FunctionType) and function.__module__ == module.__name__:
                    code = function.__code__
                    live.append({'qualname': code.co_qualname, 'filename': code.co_filename,
                                 'sha': code_digest(code), 'expected_sha': expected_functions.get(code.co_qualname)})
        write({'event': 'loader-end', 'module': module.__name__, 'filename': loader.filename,
               'source_sha': hashlib.sha256(loader.source.encode()).hexdigest(),
               'mutant': dataclasses.asdict(loader.mutant), 'completed': completed,
               'applied': applier.applied, 'expected_module_sha': code_digest(expected),
               'executed_modules': module_frames, 'live_functions': live,
               'ast_changed': ast.dump(original_tree) != ast.dump(mutated_tree)})


def pytest_configure(config):
    # Plain assertions apply only to this native test-code activation, not
    # to nested fixture projects that deliberately exercise rewrite caches.
    if os.environ.get('LEELA_PROOF_TESTCODE') == '1' and os.environ.get('PYTEST_ADDOPTS') == '--assert=plain':
        plain_options[id(config)] = os.environ.pop('PYTEST_ADDOPTS')
    MutatingLoader.exec_module = observe
    write({'event': 'configure', 'rootpath': str(config.rootpath),
           'observer_sha': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


def pytest_runtest_logreport(report):
    write({'event': 'report', 'nodeid': report.nodeid, 'phase': report.when,
           'outcome': report.outcome,
           'diagnostic': report.longreprtext if report.failed else None})


def pytest_unconfigure(config):
    MutatingLoader.exec_module = original_exec
    previous = plain_options.pop(id(config), None)
    if previous is not None:
        os.environ['PYTEST_ADDOPTS'] = previous
