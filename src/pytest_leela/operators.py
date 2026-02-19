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
    ("BinOp", "BitAnd"): ["BitOr", "BitXor"],
    ("BinOp", "BitOr"): ["BitAnd", "BitXor"],
    ("BinOp", "BitXor"): ["BitAnd", "BitOr"],
    ("BinOp", "LShift"): ["RShift"],
    ("BinOp", "RShift"): ["LShift"],
    # AugAssign (+=, -=, *=, etc.)
    ("AugAssign", "Add"): ["Sub", "Mult"],
    ("AugAssign", "Sub"): ["Add", "Mult"],
    ("AugAssign", "Mult"): ["Add", "FloorDiv"],
    ("AugAssign", "Div"): ["Mult", "FloorDiv"],
    ("AugAssign", "FloorDiv"): ["Div", "Mult"],
    ("AugAssign", "Mod"): ["FloorDiv", "Mult"],
    ("AugAssign", "Pow"): ["Mult"],
    ("AugAssign", "BitAnd"): ["BitOr", "BitXor"],
    ("AugAssign", "BitOr"): ["BitAnd", "BitXor"],
    ("AugAssign", "BitXor"): ["BitAnd", "BitOr"],
    ("AugAssign", "LShift"): ["RShift"],
    ("AugAssign", "RShift"): ["LShift"],
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
    # IfExp (ternary)
    ("IfExp", "ternary"): ["swap_branches", "always_true", "always_false"],
    # Break/Continue
    ("Break", "break"): ["continue"],
    ("Continue", "continue"): ["break"],
    # ExceptHandler
    ("ExceptHandler", "typed"): ["broaden", "body_to_raise"],
    ("ExceptHandler", "typed_broadest"): ["body_to_raise"],
    ("ExceptHandler", "typed_raise_body"): ["broaden"],
    ("ExceptHandler", "bare"): ["body_to_raise"],
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
    # int bitwise: full set
    ("BinOp", "BitAnd", "int"): ["BitOr", "BitXor"],
    ("BinOp", "BitOr", "int"): ["BitAnd", "BitXor"],
    ("BinOp", "BitXor", "int"): ["BitAnd", "BitOr"],
    ("BinOp", "LShift", "int"): ["RShift"],
    ("BinOp", "RShift", "int"): ["LShift"],
    # int augmented assignment: full set
    ("AugAssign", "Add", "int"): ["Sub", "Mult", "FloorDiv"],
    ("AugAssign", "Sub", "int"): ["Add", "Mult"],
    ("AugAssign", "Mult", "int"): ["Add", "FloorDiv"],
    ("AugAssign", "Div", "int"): ["Mult", "FloorDiv"],
    ("AugAssign", "FloorDiv", "int"): ["Mult", "Add"],
    ("AugAssign", "Mod", "int"): ["FloorDiv"],
    ("AugAssign", "Pow", "int"): ["Mult"],
    ("AugAssign", "BitAnd", "int"): ["BitOr", "BitXor"],
    ("AugAssign", "BitOr", "int"): ["BitAnd", "BitXor"],
    ("AugAssign", "BitXor", "int"): ["BitAnd", "BitOr"],
    ("AugAssign", "LShift", "int"): ["RShift"],
    ("AugAssign", "RShift", "int"): ["LShift"],
    # float augmented assignment
    ("AugAssign", "Add", "float"): ["Sub", "Mult", "Div"],
    ("AugAssign", "Sub", "float"): ["Add", "Mult"],
    ("AugAssign", "Mult", "float"): ["Add", "Div"],
    ("AugAssign", "Div", "float"): ["Mult", "Sub"],
    ("AugAssign", "Pow", "float"): ["Mult"],
    # str augmented assignment: prune (str += is concat, not meaningful to mutate)
    ("AugAssign", "Add", "str"): [],
    # bool augmented bitwise: pruned
    ("AugAssign", "BitAnd", "bool"): ["BitOr"],
    ("AugAssign", "BitOr", "bool"): ["BitAnd"],
    ("AugAssign", "BitXor", "bool"): ["BitAnd", "BitOr"],
    # float arithmetic
    ("BinOp", "Add", "float"): ["Sub", "Mult", "Div"],
    ("BinOp", "Sub", "float"): ["Add", "Mult"],
    ("BinOp", "Mult", "float"): ["Add", "Div"],
    ("BinOp", "Div", "float"): ["Mult", "Sub"],
    ("BinOp", "Pow", "float"): ["Mult"],
    # bool bitwise: pruned (bitwise on bools is suspicious)
    ("BinOp", "BitAnd", "bool"): ["BitOr"],
    ("BinOp", "BitOr", "bool"): ["BitAnd"],
    ("BinOp", "BitXor", "bool"): ["BitAnd", "BitOr"],
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
