"""Mutation operator registry — typed and untyped variants."""

from __future__ import annotations

from pytest_leela.models import MutationPoint

# Untyped mutations: applied when no type info is available
UNTYPED_MUTATIONS: dict[tuple[str, str], list[str]] = {
    # BinOp
    ("BinOp", "Add"): ["Sub", "Mult"],
    ("BinOp", "Sub"): ["Add", "Mult"],
    ("BinOp", "Mult"): ["Add", "FloorDiv"],
    ("BinOp", "Div"): ["Mult", "FloorDiv"],
    ("BinOp", "FloorDiv"): ["Div", "Mult"],
    ("BinOp", "Mod"): ["FloorDiv", "Mult"],
    ("BinOp", "Pow"): ["Mult"],
    # Compare
    ("Compare", "Eq"): ["NotEq"],
    ("Compare", "NotEq"): ["Eq"],
    ("Compare", "Lt"): ["LtE", "GtE"],
    ("Compare", "LtE"): ["Lt", "Gt"],
    ("Compare", "Gt"): ["GtE", "LtE"],
    ("Compare", "GtE"): ["Gt", "Lt"],
    ("Compare", "Is"): ["IsNot"],
    ("Compare", "IsNot"): ["Is"],
    ("Compare", "In"): ["NotIn"],
    ("Compare", "NotIn"): ["In"],
    # BoolOp
    ("BoolOp", "And"): ["Or"],
    ("BoolOp", "Or"): ["And"],
    # UnaryOp
    ("UnaryOp", "USub"): ["UAdd"],
    ("UnaryOp", "UAdd"): ["USub"],
    ("UnaryOp", "Not"): ["_remove"],  # Remove the `not`
    # Return
    ("Return", "True"): ["False"],
    ("Return", "False"): ["True"],
    ("Return", "None"): ["expr"],  # Will be contextual
    ("Return", "expr"): ["None"],
    ("Return", "int_literal"): ["negate"],
    ("Return", "float_literal"): ["negate"],
    ("Return", "str_literal"): ["empty_str"],
    ("Return", "negation"): ["remove_negation"],
}

# Typed mutations: narrowed based on inferred type
TYPED_MUTATIONS: dict[tuple[str, str, str], list[str]] = {
    # int arithmetic: full set
    ("BinOp", "Add", "int"): ["Sub", "Mult", "FloorDiv"],
    ("BinOp", "Sub", "int"): ["Add", "Mult"],
    ("BinOp", "Mult", "int"): ["Add", "FloorDiv"],
    ("BinOp", "Div", "int"): ["Mult", "FloorDiv"],
    ("BinOp", "FloorDiv", "int"): ["Mult", "Add"],
    ("BinOp", "Mod", "int"): ["FloorDiv"],
    ("BinOp", "Pow", "int"): ["Mult"],
    # float arithmetic
    ("BinOp", "Add", "float"): ["Sub", "Mult", "Div"],
    ("BinOp", "Sub", "float"): ["Add", "Mult"],
    ("BinOp", "Mult", "float"): ["Add", "Div"],
    ("BinOp", "Div", "float"): ["Mult", "Sub"],
    ("BinOp", "Pow", "float"): ["Mult"],
    # str: only + is valid, and we prune it (can't meaningfully mutate str concat)
    ("BinOp", "Add", "str"): [],
    ("BinOp", "Mult", "str"): [],  # str * int is valid but mutation is meaningless
    # bool returns: just negate
    ("Return", "True", "bool"): ["False"],
    ("Return", "False", "bool"): ["True"],
    ("Return", "expr", "bool"): ["negate_expr"],
    # Optional returns: swap None/value
    ("Return", "None", "Optional[int]"): ["expr"],
    ("Return", "None", "Optional[str]"): ["expr"],
    ("Return", "expr", "Optional[int]"): ["None"],
    ("Return", "expr", "Optional[str]"): ["None"],
    ("Return", "None", "Optional[float]"): ["expr"],
    ("Return", "expr", "Optional[float]"): ["None"],
    # int comparisons: full boundary set
    ("Compare", "Lt", "int"): ["LtE", "GtE"],
    ("Compare", "LtE", "int"): ["Lt", "Gt"],
    ("Compare", "Gt", "int"): ["GtE", "LtE"],
    ("Compare", "GtE", "int"): ["Gt", "Lt"],
    ("Compare", "Eq", "int"): ["NotEq"],
    ("Compare", "NotEq", "int"): ["Eq"],
    # float comparisons
    ("Compare", "Lt", "float"): ["LtE", "GtE"],
    ("Compare", "LtE", "float"): ["Lt", "Gt"],
    ("Compare", "Gt", "float"): ["GtE", "LtE"],
    ("Compare", "GtE", "float"): ["Gt", "Lt"],
    ("Compare", "Eq", "float"): ["NotEq"],
    ("Compare", "NotEq", "float"): ["Eq"],
    # str containment
    ("Compare", "In", "str"): ["NotIn"],
    ("Compare", "NotIn", "str"): ["In"],
    # BoolOp always bool-typed
    ("BoolOp", "And", "bool"): ["Or"],
    ("BoolOp", "Or", "bool"): ["And"],
    # UnaryOp on int
    ("UnaryOp", "USub", "int"): ["UAdd"],
    ("UnaryOp", "UAdd", "int"): ["USub"],
    ("UnaryOp", "USub", "float"): ["UAdd"],
    ("UnaryOp", "UAdd", "float"): ["USub"],
    ("UnaryOp", "Not", "bool"): ["_remove"],
    # int return mutations
    ("Return", "int_literal", "int"): ["negate"],
    ("Return", "float_literal", "float"): ["negate"],
    ("Return", "negation", "int"): ["remove_negation"],
    ("Return", "negation", "float"): ["remove_negation"],
    ("Return", "expr", "int"): ["negate_expr"],
    ("Return", "expr", "float"): ["negate_expr"],
    ("Return", "str_literal", "str"): ["empty_str"],
}


def mutations_for(point: MutationPoint, use_types: bool = True) -> list[str]:
    """Get the list of mutations applicable to a mutation point."""
    if use_types and point.inferred_type:
        key = (point.node_type, point.original_op, point.inferred_type)
        if key in TYPED_MUTATIONS:
            return TYPED_MUTATIONS[key]
        # Fall through to untyped if no typed rule matches

    key_untyped = (point.node_type, point.original_op)
    return UNTYPED_MUTATIONS.get(key_untyped, [])


def count_pruned(points: list[MutationPoint], use_types: bool = True) -> int:
    """Count how many mutations are pruned by type awareness."""
    if not use_types:
        return 0

    pruned = 0
    for point in points:
        untyped = mutations_for(point, use_types=False)
        typed = mutations_for(point, use_types=True)
        pruned += len(untyped) - len(typed)
    return pruned
