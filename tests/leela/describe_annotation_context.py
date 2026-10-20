"""Lexical annotation contexts in the native mutation candidate pipeline."""

import ast
import textwrap
import typing

import pytest

from pytest_leela.ast_analysis import _MutationPointCollector, find_mutation_points
from pytest_leela.operators import mutations_for
from pytest_leela.type_extractor import enrich_mutation_points


def candidates(source):
    source = textwrap.dedent(source)
    points = find_mutation_points(source, "context.py", "context")
    points, _ = enrich_mutation_points(source, points)
    return [
        (p.lineno, p.col_offset, p.node_type, p.original_op, replacement)
        for p in points
        for replacement in mutations_for(p)
    ]


def test_function_visitor_accepts_sync_and_async_nodes():
    """The visitor's resolvable union is part of its introspection contract."""
    hints = typing.get_type_hints(_MutationPointCollector.visit_FunctionDef)
    assert hints == {
        "node": ast.FunctionDef | ast.AsyncFunctionDef,
        "return": type(None),
    }
    assert all(
        issubclass(node_type, hints["node"])
        for node_type in (ast.FunctionDef, ast.AsyncFunctionDef)
    )


@pytest.mark.parametrize("future", ["", "from __future__ import annotations\n"])
@pytest.mark.parametrize("function", ["def", "async def"])
def test_local_and_self_annotations_have_no_candidates(future, function):
    source = future + textwrap.dedent(f"""
        {function} f(self):
            local: tuple[int | str, -float, (int if flag else str)]
            self.value: list[int | str] = 6 | 3
            local: (left < right) and not flag = 6 & 3
    """)
    found = candidates(source)
    offset = bool(future)
    assert found == [
        (4 + offset, 34, "BinOp", "BitOr", "BitAnd"),
        (4 + offset, 34, "BinOp", "BitOr", "BitXor"),
        (5 + offset, 41, "BinOp", "BitAnd", "BitOr"),
        (5 + offset, 41, "BinOp", "BitAnd", "BitXor"),
    ]


@pytest.mark.parametrize("future", ["", "from __future__ import annotations\n"])
@pytest.mark.parametrize("function", ["def", "async def"])
def test_executable_targets_and_initializers_are_retained(future, function):
    source = future + textwrap.dedent(f"""
        {function} f(self, items):
            items[1 + 2]: int | str = 6 ^ 3
            (self if 3 > 2 else items).field: int | str
            plain: int | str
            runtime = int | str
            bits = (6 & 3) | (8 >> 1)
    """)
    lines = textwrap.dedent(source).splitlines()
    points = find_mutation_points(source, "context.py", "context")
    identities = [(lines[p.lineno - 1].strip(), p.node_type, p.original_op)
                  for p in points]
    assert identities == [
        ("items[1 + 2]: int | str = 6 ^ 3", "BinOp", "Add"),
        ("items[1 + 2]: int | str = 6 ^ 3", "BinOp", "BitXor"),
        ("(self if 3 > 2 else items).field: int | str", "IfExp", "ternary"),
        ("(self if 3 > 2 else items).field: int | str", "Compare", "Gt"),
        ("runtime = int | str", "BinOp", "BitOr"),
        ("bits = (6 & 3) | (8 >> 1)", "BinOp", "BitOr"),
        ("bits = (6 & 3) | (8 >> 1)", "BinOp", "BitAnd"),
        ("bits = (6 & 3) | (8 >> 1)", "BinOp", "RShift"),
    ]
    assert candidates(source)


@pytest.mark.parametrize("future", ["", "from __future__ import annotations\n"])
@pytest.mark.parametrize("function", ["def", "async def"])
def test_nearest_lexical_scope_and_restoration_preserve_annotations(future, function):
    source = future + textwrap.dedent(f"""
        module_before: int | str
        class Outer:
            class_before: int | str
            {function} method(self, arg: int | str) -> int | str:
                local_before: int | str
                class Inner:
                    class_inside_function: int | str
                    {function} nested(self, arg: int | str) -> int | str:
                        local_nested: int | str
                    class_after_nested: int | str
                local_after_class: int | str
                {function} inner(arg: int | str) -> int | str:
                    local_inner: int | str
                local_after_function: int | str
            class_after_method: int | str
        module_after: int | str
    """)
    lines = source.splitlines()
    found = candidates(source)
    assert len(found) == 24
    assert all(node == "BinOp" and op == "BitOr" for _, _, node, op, _ in found)
    assert {lines[line - 1].strip() for line, *_ in found} == {
        "module_before: int | str", "class_before: int | str",
        f"{function} method(self, arg: int | str) -> int | str:",
        "class_inside_function: int | str",
        f"{function} nested(self, arg: int | str) -> int | str:",
        "class_after_nested: int | str",
        f"{function} inner(arg: int | str) -> int | str:",
        "class_after_method: int | str", "module_after: int | str",
    }


@pytest.mark.parametrize("function", ["def", "async def"])
@pytest.mark.parametrize("future", ["", "from __future__ import annotations\n"])
def test_python_does_not_evaluate_local_annotations(function, future):
    source = future + textwrap.dedent(f"""
        {function} f(items):
            local: missing_name | int
            items[1 + 1]: also_missing | str = 6 | 3
    """)
    namespace = {}
    exec(compile(source, "context.py", "exec"), namespace)
    items = [0, 0, 0]
    result = namespace["f"](items)
    if function == "async def":
        import asyncio
        asyncio.run(result)
    assert items == [0, 0, 7]
    points = find_mutation_points(source, "context.py", "context")
    assert [(p.node_type, p.original_op) for p in points] == [
        ("BinOp", "Add"), ("BinOp", "BitOr")
    ]
