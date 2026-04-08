"""Extract type annotations to enrich mutation points."""

from __future__ import annotations

import ast
from dataclasses import replace

from pytest_leela.models import EnrichmentStats, MutationPoint


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

        return_type = None
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

    __slots__ = (
        "name",
        "start_line",
        "end_line",
        "param_types",
        "return_type",
        "body",
        "_assignment_env",
    )

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
        self._assignment_env: dict[str, str | None] | None = None

    @property
    def assignment_env(self) -> dict[str, str | None]:
        """Lazily build and cache the assignment type environment."""
        if self._assignment_env is None:
            self._assignment_env = _build_assignment_env(self.body, self.param_types)
        return self._assignment_env


def _find_enclosing_func(functions: list[_FuncInfo], lineno: int) -> _FuncInfo | None:
    """Find the function that contains a given line."""
    for func in functions:
        if func.start_line <= lineno <= func.end_line:
            return func
    return None


def _infer_expr_type(
    node: ast.expr,
    func: _FuncInfo,
    env: dict[str, str | None] | None = None,
) -> str | None:
    """Infer the type of an expression from function annotations and assignment env.

    Resolution order:
      1. Assignment environment (forward-propagated local variables)
      2. Parameter annotations
      3. Literal constants
      4. Known built-in calls (len)
    """
    if isinstance(node, ast.Name):
        name = node.id
        # Check assignment environment first
        if env is not None and name in env:
            return env[name]
        # Fall back to parameter annotations
        if name in func.param_types:
            return func.param_types[name]
        return None
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return "bool"
        if isinstance(node.value, int):
            return "int"
        if isinstance(node.value, float):
            return "float"
        if isinstance(node.value, str):
            return "str"
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "len":
            return "int"
    return None


def _infer_assigned_value_type(
    value: ast.expr,
    param_types: dict[str, str],
    env: dict[str, str | None],
) -> str | None:
    """Infer the type of a value expression during assignment environment building.

    This is a lightweight variant of _infer_expr_type used while building the
    assignment environment.  We can't call _infer_expr_type directly here because
    _FuncInfo is not yet fully constructed; instead we replicate the Name / Constant
    / Call logic against the in-progress env and param_types.
    """
    if isinstance(value, ast.Name):
        name = value.id
        if name in env:
            return env[name]
        if name in param_types:
            return param_types[name]
        return None
    if isinstance(value, ast.Constant):
        if isinstance(value.value, bool):
            return "bool"
        if isinstance(value.value, int):
            return "int"
        if isinstance(value.value, float):
            return "float"
        if isinstance(value.value, str):
            return "str"
    if isinstance(value, ast.Call):
        if isinstance(value.func, ast.Name) and value.func.id == "len":
            return "int"
    # BinOp: if both sides agree on a type, propagate it
    if isinstance(value, ast.BinOp):
        left = _infer_assigned_value_type(value.left, param_types, env)
        if left is not None:
            return left
        right = _infer_assigned_value_type(value.right, param_types, env)
        return right
    return None


def _build_assignment_env(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    param_types: dict[str, str],
) -> dict[str, str | None]:
    """Build a type environment from a single forward pass over the function body.

    Walks top-level statements in the function body in order.  For each
    assignment or annotated assignment encountered it attempts to infer the
    type of the assigned value.  If a variable is assigned multiple times
    with differing types, the entry is set to None (unknown).

    Rules:
    - ``x: int = ...``  — use the annotation (AnnAssign)
    - ``x = 5``         — infer from the literal / expression (Assign)
    - Conflicting re-assignments → None
    - Only the direct (top-level) body is walked; statements inside nested
      blocks (if, for, while, …) are deliberately skipped to avoid
      control-flow sensitivity.
    """
    env: dict[str, str | None] = {}

    for stmt in func_node.body:
        # Annotated assignment: x: int = ... or x: int (no value)
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            var_name = stmt.target.id
            ann_type = _annotation_to_str(stmt.annotation)
            _env_assign(env, var_name, ann_type)

        # Plain assignment: x = <expr>  (only single-target, simple Name target)
        elif isinstance(stmt, ast.Assign):
            # Only handle single-target assignments to a bare Name
            if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                var_name = stmt.targets[0].id
                inferred = _infer_assigned_value_type(stmt.value, param_types, env)
                _env_assign(env, var_name, inferred)

    return env


def _env_assign(env: dict[str, str | None], name: str, inferred: str | None) -> None:
    """Update the environment for a new assignment to *name*.

    If the variable already exists with a different (non-None) type, mark it
    as unknown (None) to avoid false type claims from conflicting assignments.
    """
    if name not in env:
        env[name] = inferred
    elif env[name] != inferred:
        # Conflicting types — fall back to unknown
        env[name] = None


def _infer_binop_type(node: ast.BinOp, func: _FuncInfo) -> str | None:
    """Infer the type of a BinOp's operands from annotations."""
    env = func.assignment_env
    # Check left operand
    left_type = _infer_expr_type(node.left, func, env)
    if left_type is not None:
        return left_type
    # Check right operand
    right_type = _infer_expr_type(node.right, func, env)
    return right_type


def _infer_augassign_type(node: ast.AugAssign, func: _FuncInfo) -> str | None:
    """Infer the type of an AugAssign from target or value annotations."""
    env = func.assignment_env
    # Check target (e.g., x += 1 — look up x's type)
    target_type = _infer_expr_type(node.target, func, env)
    if target_type is not None:
        return target_type
    # Check value expression
    value_type = _infer_expr_type(node.value, func, env)
    return value_type


def _infer_compare_type(node: ast.Compare, func: _FuncInfo) -> str | None:
    """Infer type context for a Compare node."""
    env = func.assignment_env
    left_type = _infer_expr_type(node.left, func, env)
    if left_type is not None:
        return left_type
    for comp in node.comparators:
        comp_type = _infer_expr_type(comp, func, env)
        if comp_type is not None:
            return comp_type
    return None


def _find_node_at(
    tree: ast.AST, lineno: int, col_offset: int, node_type: str
) -> ast.AST | None:
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


def _type_came_from_dataflow(
    inferred_type: str,
    point_node_type: str,
    func: _FuncInfo,
    tree: ast.AST,
    point_lineno: int,
    point_col_offset: int,
) -> bool:
    """Return True when *inferred_type* was sourced from the assignment environment.

    A type is considered to come from dataflow when the operand(s) that
    produced it are local variables whose types are present in the assignment
    environment but *not* in the parameter annotation map.

    The heuristic is conservative: if at least one operand's type is resolved
    via the assignment env and is absent from param_types, we credit dataflow.
    Annotation-sourced enrichment (param annotations, return type, literal
    constants, len()) is credited to annotations.
    """
    env = func.assignment_env
    param_types = func.param_types

    if point_node_type == "Return":
        # Return type always comes from the explicit return annotation.
        return False

    if point_node_type == "BoolOp":
        # BoolOp is hardcoded to "bool"; no env lookup involved.
        return False

    if point_node_type == "BinOp":
        node = _find_node_at(tree, point_lineno, point_col_offset, "BinOp")
        if isinstance(node, ast.BinOp):
            return _operand_from_env_not_param(node.left, env, param_types) or (
                _type_of_operand(node.left, func, env) is None
                and _operand_from_env_not_param(node.right, env, param_types)
            )
        return False

    if point_node_type == "Compare":
        node = _find_node_at(tree, point_lineno, point_col_offset, "Compare")
        if isinstance(node, ast.Compare):
            if _operand_from_env_not_param(node.left, env, param_types):
                return True
            for comp in node.comparators:
                if _operand_from_env_not_param(comp, env, param_types):
                    return True
        return False

    if point_node_type == "AugAssign":
        node = _find_node_at(tree, point_lineno, point_col_offset, "AugAssign")
        if isinstance(node, ast.AugAssign):
            return _operand_from_env_not_param(node.target, env, param_types) or (
                _type_of_operand(node.target, func, env) is None
                and _operand_from_env_not_param(node.value, env, param_types)
            )
        return False

    if point_node_type == "UnaryOp":
        node = _find_node_at(tree, point_lineno, point_col_offset, "UnaryOp")
        if isinstance(node, ast.UnaryOp):
            return _operand_from_env_not_param(node.operand, env, param_types)
        return False

    return False


def _operand_from_env_not_param(
    operand: ast.expr,
    env: dict[str, str | None],
    param_types: dict[str, str],
) -> bool:
    """Return True if *operand* is a Name whose type comes from env, not param_types."""
    if isinstance(operand, ast.Name):
        name = operand.id
        if name in env and env[name] is not None:
            return name not in param_types
    return False


def _type_of_operand(
    operand: ast.expr,
    func: _FuncInfo,
    env: dict[str, str | None],
) -> str | None:
    """Lightweight helper: return the type of a single operand or None."""
    return _infer_expr_type(operand, func, env)


def enrich_mutation_points(
    source: str, points: list[MutationPoint]
) -> tuple[list[MutationPoint], EnrichmentStats]:
    """Add inferred type information to mutation points.

    Returns a tuple of (enriched_points, stats) where *stats* breaks down how
    many points received their type from parameter/return annotations versus
    from local-variable assignment dataflow.
    """
    if not points:
        return points, EnrichmentStats()

    tree = ast.parse(source)
    collector = _TypeCollector()
    collector.visit(tree)

    enriched: list[MutationPoint] = []
    stats = EnrichmentStats()

    for point in points:
        func = _find_enclosing_func(collector.functions, point.lineno)
        if func is None:
            enriched.append(point)
            continue

        inferred_type = None

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

        elif point.node_type == "AugAssign":
            node = _find_node_at(tree, point.lineno, point.col_offset, "AugAssign")
            if isinstance(node, ast.AugAssign):
                inferred_type = _infer_augassign_type(node, func)

        elif point.node_type == "BoolOp":
            inferred_type = "bool"

        elif point.node_type == "UnaryOp":
            node = _find_node_at(tree, point.lineno, point.col_offset, "UnaryOp")
            if isinstance(node, ast.UnaryOp):
                inferred_type = _infer_expr_type(
                    node.operand, func, func.assignment_env
                )

        if inferred_type is not None:
            from_dataflow = _type_came_from_dataflow(
                inferred_type,
                point.node_type,
                func,
                tree,
                point.lineno,
                point.col_offset,
            )
            if from_dataflow:
                stats.from_assignment_dataflow += 1
            else:
                stats.from_annotations += 1
            point = replace(point, inferred_type=inferred_type)

        enriched.append(point)

    return enriched, stats
