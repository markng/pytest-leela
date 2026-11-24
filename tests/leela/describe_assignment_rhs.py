import ast
from inspect import get_annotations
from types import ModuleType

import pytest

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.import_hook import apply_mutation
from pytest_leela.models import Mutant, MutationPoint
from pytest_leela.operators import DEFAULT_OPERATORS, build_allowed_keys, mutations_for


def _point(lineno=1, col_offset=8, node_type="Assign"):
    return MutationPoint(
        file_path="subject.py", module_name="subject", lineno=lineno,
        col_offset=col_offset, node_type=node_type, original_op="assignment",
        inferred_type=None,
    )


def test_collects_the_start_of_each_eligible_rhs_and_still_visits_expressions():
    source = """\
shade = "blue"; size: str = "small"
amount = (
    2 + 3
)
label: str
empty = None
absent: str | None = None
count += 1
(a, b) = (1, 2)
a = b = 1
box.label: str = "red"
box.label = "red"
box[0] = "red"
if (part := 4):
    pass
"""
    points = find_mutation_points(source, "subject.py", "subject")
    assert [p for p in points if p.original_op == "assignment"] == [
        _point(), _point(1, 28, "AnnAssign"), _point(3, 4),
    ]
    assert [(p.lineno, p.col_offset, p.original_op) for p in points if p.node_type == "BinOp"] == [
        (3, 4, "Add"), (7, 8, "BitOr"),
    ]


@pytest.mark.parametrize("node_type", ["Assign", "AnnAssign"])
def test_enables_rhs_to_none_only_when_selected(node_type):
    point = _point(node_type=node_type)
    assert mutations_for(point, allowed_keys=build_allowed_keys(DEFAULT_OPERATORS)) == []
    assert mutations_for(point, allowed_keys=build_allowed_keys(("assignment",))) == ["rhs_to_none"]
    assert mutations_for(point, allowed_keys=build_allowed_keys(None)) == ["rhs_to_none"]


@pytest.mark.parametrize(("point", "expected"), [
    (_point(), 'shade = None; size: str = "small"'),
    (_point(1, 28, "AnnAssign"), 'shade = "blue"; size: str = None'),
])
def test_replaces_only_the_targeted_rhs_and_preserves_its_location(point, expected):
    source = 'shade = "blue"; size: str = "small"'
    mutant = Mutant(point=point, replacement_op="rhs_to_none", mutant_id=1)
    tree, applied = apply_mutation(source, mutant)
    assert applied
    assert ast.dump(tree) == ast.dump(ast.parse(expected))
    index = int(point.node_type == "AnnAssign")
    assert ast.dump(tree.body[index].value, include_attributes=True) == ast.dump(
        ast.copy_location(ast.Constant(value=None), ast.parse(source).body[index].value),
        include_attributes=True,
    )
    module = ModuleType("subject")
    exec(compile(tree, "subject.py", "exec"), vars(module))
    assert (module.shade, module.size) == (
        ("blue", None) if index else (None, "small")
    )
    assert get_annotations(module) == {"size": str}


@pytest.mark.parametrize(("source", "point", "replacement"), [
    ('shade = "blue"', _point(1, 0), "rhs_to_none"),
    ('shade = "blue"', _point(2, 8), "rhs_to_none"),
    ('shade = "blue"', _point(node_type="AnnAssign"), "rhs_to_none"),
    ('shade = "blue"', _point(), "remove_decorator"),
    ('shade = None', _point(), "rhs_to_none"),
    ('shade: str', _point(1, 0, "AnnAssign"), "rhs_to_none"),
    ('shade: str = None', _point(1, 13, "AnnAssign"), "rhs_to_none"),
    ('shade = other = 1', _point(1, 16), "rhs_to_none"),
    ('box.shade = 1', _point(1, 12), "rhs_to_none"),
])
def test_refuses_ineligible_assignments_and_nonmatching_mutants(source, point, replacement):
    mutant = Mutant(point=point, replacement_op=replacement, mutant_id=1)
    tree, applied = apply_mutation(source, mutant)
    assert not applied
    assert ast.dump(tree) == ast.dump(ast.parse(source))


def test_preserves_existing_mutations_nested_in_an_assignment():
    source = "count: int = 5 + 2"
    point, = [p for p in find_mutation_points(source, "subject.py", "subject") if p.node_type == "BinOp"]
    tree, applied = apply_mutation(source, Mutant(point=point, replacement_op="Sub", mutant_id=1))
    assert applied
    assert ast.dump(tree) == ast.dump(ast.parse("count: int = 5 - 2"))
