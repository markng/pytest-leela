"""Extract type annotations to enrich mutation points."""

from __future__ import annotations

import ast
from dataclasses import replace

from pytest_leela.models import MutationPoint


def _annotation_to_str(node: ast.expr) -> str | None:
    """Convert an annotation AST node to a string representation."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        return str(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        value = _annotation_to_str(node.value)
        if value is not None:
            return f"{value}.{node.attr}"
        return None
    if isinstance(node, ast.Subscript):
        base = _annotation_to_str(node.value)
        if base == "Optional":
            inner = _annotation_to_str(node.slice)
            if inner is not None:
                return f"Optional[{inner}]"
        if base in ("list", "dict", "set", "tuple"):
            return base
        return base
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # X | None style union
        left = _annotation_to_str(node.left)
        right = _annotation_to_str(node.right)
        if right == "None" and left is not None:
            return f"Optional[{left}]"
        if left == "None" and right is not None:
            return f"Optional[{right}]"
        return None
    return None


class _TypeCollector(ast.NodeVisitor):
    """Collect type annotations from function signatures."""

    def __init__(self) -> None:
        # Map (lineno range start, lineno range end) -> param types and return type
        self.functions: list[_FuncInfo] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._process_func(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._process_func(node)
        self.generic_visit(node)

    def _process_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        param_types: dict[str, str] = {}
        for arg in node.args.args:
            if arg.annotation is not None:
                type_str = _annotation_to_str(arg.annotation)
                if type_str is not None:
                    param_types[arg.arg] = type_str

        return_type: str | None = None
        if node.returns is not None:
            return_type = _annotation_to_str(node.returns)

        end_lineno = node.end_lineno if node.end_lineno else node.lineno + 100
        self.functions.append(
            _FuncInfo(
                name=node.name,
                start_line=node.lineno,
                end_line=end_lineno,
                param_types=param_types,
                return_type=return_type,
                body=node,
            )
        )


class _FuncInfo:
    """Info about a single function's type annotations."""

    __slots__ = ("name", "start_line", "end_line", "param_types", "return_type", "body")

    def __init__(
        self,
        name: str,
        start_line: int,
        end_line: int,
        param_types: dict[str, str],
        return_type: str | None,
        body: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self.name = name
        self.start_line = start_line
        self.end_line = end_line
        self.param_types = param_types
        self.return_type = return_type
        self.body = body


def _find_enclosing_func(functions: list[_FuncInfo], lineno: int) -> _FuncInfo | None:
    """Find the function that contains a given line."""
    for func in functions:
        if func.start_line <= lineno <= func.end_line:
            return func
    return None


def _infer_binop_type(
    node: ast.BinOp, func: _FuncInfo
) -> str | None:
    """Infer the type of a BinOp's operands from annotations."""
    # Check left operand
    left_type = _infer_expr_type(node.left, func)
    if left_type is not None:
        return left_type
    # Check right operand
    right_type = _infer_expr_type(node.right, func)
    return right_type


def _infer_expr_type(node: ast.expr, func: _FuncInfo) -> str | None:
    """Infer the type of an expression from function annotations."""
    if isinstance(node, ast.Name) and node.id in func.param_types:
        return func.param_types[node.id]
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            return "int"
        if isinstance(node.value, float):
            return "float"
        if isinstance(node.value, str):
            return "str"
        if isinstance(node.value, bool):
            return "bool"
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "len":
            return "int"
    return None


def _infer_compare_type(
    node: ast.Compare, func: _FuncInfo
) -> str | None:
    """Infer type context for a Compare node."""
    left_type = _infer_expr_type(node.left, func)
    if left_type is not None:
        return left_type
    for comp in node.comparators:
        comp_type = _infer_expr_type(comp, func)
        if comp_type is not None:
            return comp_type
    return None


def _find_node_at(tree: ast.AST, lineno: int, col_offset: int, node_type: str) -> ast.AST | None:
    """Find an AST node at a specific location."""
    for node in ast.walk(tree):
        if (
            hasattr(node, "lineno")
            and node.lineno == lineno
            and hasattr(node, "col_offset")
            and node.col_offset == col_offset
            and type(node).__name__ == node_type
        ):
            return node
    return None


def enrich_mutation_points(
    source: str, points: list[MutationPoint]
) -> list[MutationPoint]:
    """Add inferred type information to mutation points."""
    if not points:
        return points

    tree = ast.parse(source)
    collector = _TypeCollector()
    collector.visit(tree)

    enriched: list[MutationPoint] = []
    for point in points:
        func = _find_enclosing_func(collector.functions, point.lineno)
        if func is None:
            enriched.append(point)
            continue

        inferred_type: str | None = None

        if point.node_type == "Return":
            inferred_type = func.return_type

        elif point.node_type == "BinOp":
            node = _find_node_at(tree, point.lineno, point.col_offset, "BinOp")
            if isinstance(node, ast.BinOp):
                inferred_type = _infer_binop_type(node, func)

        elif point.node_type == "Compare":
            node = _find_node_at(tree, point.lineno, point.col_offset, "Compare")
            if isinstance(node, ast.Compare):
                inferred_type = _infer_compare_type(node, func)

        elif point.node_type == "BoolOp":
            inferred_type = "bool"

        elif point.node_type == "UnaryOp":
            node = _find_node_at(tree, point.lineno, point.col_offset, "UnaryOp")
            if isinstance(node, ast.UnaryOp):
                inferred_type = _infer_expr_type(node.operand, func)

        if inferred_type is not None:
            point = replace(point, inferred_type=inferred_type)

        enriched.append(point)

    return enriched
