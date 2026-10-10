"""Regression tests for annotation evaluation in mutation loading."""

import __future__
import ast
import sys
import types
import typing
from pathlib import Path

import pytest

from pytest_leela import coverage_tracker, import_hook
from pytest_leela.import_hook import MutatingLoader, apply_mutation
from pytest_leela.models import Mutant, MutationPoint


def _mutate_collect_coverage_test_dir_union() -> tuple[str, Mutant]:
    source_path = Path(coverage_tracker.__file__)
    source = source_path.read_text()
    tree = ast.parse(source, filename=str(source_path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "collect_coverage"
    )
    argument = next(arg for arg in function.args.args if arg.arg == "test_dir")
    assert isinstance(argument.annotation, ast.BinOp)
    assert isinstance(argument.annotation.op, ast.BitOr)
    mutant = Mutant(
        point=MutationPoint(
            file_path=str(source_path),
            module_name="annotation_policy_coverage_tracker",
            lineno=argument.annotation.lineno,
            col_offset=argument.annotation.col_offset,
            node_type="BinOp",
            original_op="BitOr",
            inferred_type=None,
        ),
        replacement_op="BitAnd",
        mutant_id=0,
    )
    return source, mutant


def test_compile_inherits_future_annotations_from_the_calling_code() -> None:
    target = "def target(value: int | None) -> None:\n    pass\n"
    namespace: dict[str, object] = {}
    exec(
        compile(
            "from __future__ import annotations\n"
            "compiled = compile(target, '<annotation-target>', 'exec')\n",
            "<annotation-caller>",
            "exec",
        ),
        {"target": target},
        namespace,
    )
    compiled = namespace["compiled"]
    assert isinstance(compiled, types.CodeType)
    assert compiled.co_flags & __future__.annotations.compiler_flag

    unflagged = compile(Path(import_hook.__file__).read_text(), import_hook.__file__, "exec")
    assert not unflagged.co_flags & __future__.annotations.compiler_flag


def test_invalid_union_annotation_mutation_is_forced_when_hints_are_read() -> None:
    source, mutant = _mutate_collect_coverage_test_dir_union()
    _, applied = apply_mutation(source, mutant)
    assert applied

    loader = MutatingLoader(source, mutant, coverage_tracker.__file__)
    module = types.ModuleType("annotation_policy_coverage_tracker")
    if sys.version_info < (3, 14):
        with pytest.raises(TypeError, match="unsupported operand type.*&"):
            loader.exec_module(module)
    else:
        loader.exec_module(module)
        with pytest.raises(TypeError, match="unsupported operand type.*&"):
            typing.get_type_hints(module.collect_coverage)
