"""AST analysis: find all mutable nodes in a source file."""

from __future__ import annotations

import ast
from pathlib import Path

from pytest_leela.models import MutationPoint


# Map AST operator classes to string names
_BINOP_NAMES: dict[type, str] = {
    ast.Add: "Add",
    ast.Sub: "Sub",
    ast.Mult: "Mult",
    ast.Div: "Div",
    ast.FloorDiv: "FloorDiv",
    ast.Mod: "Mod",
    ast.Pow: "Pow",
    ast.BitAnd: "BitAnd",
    ast.BitOr: "BitOr",
    ast.BitXor: "BitXor",
    ast.LShift: "LShift",
    ast.RShift: "RShift",
}

_CMPOP_NAMES: dict[type, str] = {
    ast.Eq: "Eq",
    ast.NotEq: "NotEq",
    ast.Lt: "Lt",
    ast.LtE: "LtE",
    ast.Gt: "Gt",
    ast.GtE: "GtE",
    ast.Is: "Is",
    ast.IsNot: "IsNot",
    ast.In: "In",
    ast.NotIn: "NotIn",
}

_BOOLOP_NAMES: dict[type, str] = {
    ast.And: "And",
    ast.Or: "Or",
}

_UNARYOP_NAMES: dict[type, str] = {
    ast.UAdd: "UAdd",
    ast.USub: "USub",
    ast.Not: "Not",
}


class _MutationPointCollector(ast.NodeVisitor):
    """Walk an AST and collect all mutable nodes."""

    def __init__(self, file_path: str, module_name: str) -> None:
        self.file_path = file_path
        self.module_name = module_name
        self.points: list[MutationPoint] = []

    def visit_BinOp(self, node: ast.BinOp) -> None:
        op_name = _BINOP_NAMES.get(type(node.op))
        if op_name is not None:
            self.points.append(
                MutationPoint(
                    file_path=self.file_path,
                    module_name=self.module_name,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    node_type="BinOp",
                    original_op=op_name,
                    inferred_type=None,  # Filled in by type_extractor
                )
            )
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        for op in node.ops:
            op_name = _CMPOP_NAMES.get(type(op))
            if op_name is not None:
                self.points.append(
                    MutationPoint(
                        file_path=self.file_path,
                        module_name=self.module_name,
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        node_type="Compare",
                        original_op=op_name,
                        inferred_type=None,
                    )
                )
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        op_name = _BOOLOP_NAMES.get(type(node.op))
        if op_name is not None:
            self.points.append(
                MutationPoint(
                    file_path=self.file_path,
                    module_name=self.module_name,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    node_type="BoolOp",
                    original_op=op_name,
                    inferred_type=None,
                )
            )
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        op_name = _UNARYOP_NAMES.get(type(node.op))
        if op_name is not None:
            self.points.append(
                MutationPoint(
                    file_path=self.file_path,
                    module_name=self.module_name,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    node_type="UnaryOp",
                    original_op=op_name,
                    inferred_type=None,
                )
            )
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        op_name = _BINOP_NAMES.get(type(node.op))
        if op_name is not None:
            self.points.append(
                MutationPoint(
                    file_path=self.file_path,
                    module_name=self.module_name,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    node_type="AugAssign",
                    original_op=op_name,
                    inferred_type=None,
                )
            )
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.points.append(
            MutationPoint(
                file_path=self.file_path,
                module_name=self.module_name,
                lineno=node.lineno,
                col_offset=node.col_offset,
                node_type="IfExp",
                original_op="ternary",
                inferred_type=None,
            )
        )
        self.generic_visit(node)

    def visit_Break(self, node: ast.Break) -> None:
        self.points.append(
            MutationPoint(
                file_path=self.file_path,
                module_name=self.module_name,
                lineno=node.lineno,
                col_offset=node.col_offset,
                node_type="Break",
                original_op="break",
                inferred_type=None,
            )
        )
        self.generic_visit(node)

    def visit_Continue(self, node: ast.Continue) -> None:
        self.points.append(
            MutationPoint(
                file_path=self.file_path,
                module_name=self.module_name,
                lineno=node.lineno,
                col_offset=node.col_offset,
                node_type="Continue",
                original_op="continue",
                inferred_type=None,
            )
        )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        is_already_exception = (
            isinstance(node.type, ast.Name) and node.type.id == "Exception"
        )
        body_is_bare_raise = (
            len(node.body) == 1
            and isinstance(node.body[0], ast.Raise)
            and node.body[0].exc is None
        )

        if node.type is not None:
            if is_already_exception and body_is_bare_raise:
                # No mutations possible — skip entirely
                pass
            elif is_already_exception:
                original_op = "typed_broadest"
                self._add_except_handler_point(node, original_op)
            elif body_is_bare_raise:
                original_op = "typed_raise_body"
                self._add_except_handler_point(node, original_op)
            else:
                self._add_except_handler_point(node, "typed")
        elif not body_is_bare_raise:
            self._add_except_handler_point(node, "bare")
        self.generic_visit(node)

    def _add_except_handler_point(self, node: ast.ExceptHandler, original_op: str) -> None:
        self.points.append(
            MutationPoint(
                file_path=self.file_path,
                module_name=self.module_name,
                lineno=node.lineno,
                col_offset=node.col_offset,
                node_type="ExceptHandler",
                original_op=original_op,
                inferred_type=None,
            )
        )

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None:
            # Determine the original "op" for return mutations
            original_op = _classify_return_value(node.value)
            self.points.append(
                MutationPoint(
                    file_path=self.file_path,
                    module_name=self.module_name,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    node_type="Return",
                    original_op=original_op,
                    inferred_type=None,
                )
            )
        self.generic_visit(node)


def _classify_return_value(node: ast.expr) -> str:
    """Classify what kind of value a return statement returns."""
    if isinstance(node, ast.Constant):
        if node.value is True:
            return "True"
        if node.value is False:
            return "False"
        if node.value is None:
            return "None"
        if isinstance(node.value, int):
            # -0 == 0 in Python (integer negative zero is identical to zero),
            # so negating 0 produces an equivalent mutant.  Skip it.
            if node.value == 0:
                return "zero_int_literal"
            return "int_literal"
        if isinstance(node.value, float):
            # -0.0 == 0.0 in Python, so negating 0.0 is equivalent.
            if node.value == 0.0:
                return "zero_float_literal"
            return "float_literal"
        if isinstance(node.value, str):
            if node.value == "":
                return "empty_str_literal"
            return "str_literal"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return "negation"
    return "expr"


def find_mutation_points(source: str, file_path: str, module_name: str) -> list[MutationPoint]:
    """Parse source code and find all mutable AST nodes."""
    tree = ast.parse(source, filename=file_path)
    collector = _MutationPointCollector(file_path, module_name)
    collector.visit(tree)
    return collector.points


def find_mutation_points_in_file(file_path: str) -> list[MutationPoint]:
    """Convenience: read a file and find mutation points."""
    path = Path(file_path)
    source = path.read_text()
    module_name = path.stem
    return find_mutation_points(source, file_path, module_name)
