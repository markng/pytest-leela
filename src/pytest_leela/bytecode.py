"""Bytecode-level mutation for simple operator swaps.

Compiles source once and produces mutant code objects by patching a
single byte, skipping the full ``ast.parse -> visit -> compile`` cycle
for eligible mutations.

Falls back to ``None`` for complex mutations that require AST
rewriting (BoolOp, Return, IfExp, Break/Continue, ExceptHandler).
"""

from __future__ import annotations

import dis
import opcode
import sys
import types

from pytest_leela.models import Mutant

# Minimum Python version for bytecode mutation.
_MIN_VERSION = (3, 12)

# ---------------------------------------------------------------------------
# Opcode constants
# ---------------------------------------------------------------------------
_BINARY_OP = opcode.opmap["BINARY_OP"]
_COMPARE_OP = opcode.opmap["COMPARE_OP"]
_IS_OP = opcode.opmap.get("IS_OP")
_CONTAINS_OP = opcode.opmap.get("CONTAINS_OP")
_UNARY_NEGATIVE = opcode.opmap.get("UNARY_NEGATIVE")
_UNARY_NOT = opcode.opmap.get("UNARY_NOT")
_UNARY_INVERT = opcode.opmap.get("UNARY_INVERT")

# ---------------------------------------------------------------------------
# BINARY_OP arg mapping (AST operator name -> NB_* arg value)
# ---------------------------------------------------------------------------
# Built dynamically from opcode._nb_ops when available, with hardcoded
# fallback for the standard 3.12+ layout.

_BINOP_ARGS: dict[str, int] = {}
_INPLACE_BINOP_ARGS: dict[str, int] = {}

# Map NB_* names to AST operator names
_NB_TO_AST: dict[str, str] = {
    "NB_ADD": "Add",
    "NB_SUBTRACT": "Sub",
    "NB_MULTIPLY": "Mult",
    "NB_TRUE_DIVIDE": "Div",
    "NB_FLOOR_DIVIDE": "FloorDiv",
    "NB_REMAINDER": "Mod",
    "NB_POWER": "Pow",
    "NB_AND": "BitAnd",
    "NB_OR": "BitOr",
    "NB_XOR": "BitXor",
    "NB_LSHIFT": "LShift",
    "NB_RSHIFT": "RShift",
    "NB_MATRIX_MULTIPLY": "MatMult",
}

_NB_INPLACE_TO_AST: dict[str, str] = {
    "NB_INPLACE_ADD": "Add",
    "NB_INPLACE_SUBTRACT": "Sub",
    "NB_INPLACE_MULTIPLY": "Mult",
    "NB_INPLACE_TRUE_DIVIDE": "Div",
    "NB_INPLACE_FLOOR_DIVIDE": "FloorDiv",
    "NB_INPLACE_REMAINDER": "Mod",
    "NB_INPLACE_POWER": "Pow",
    "NB_INPLACE_AND": "BitAnd",
    "NB_INPLACE_OR": "BitOr",
    "NB_INPLACE_XOR": "BitXor",
    "NB_INPLACE_LSHIFT": "LShift",
    "NB_INPLACE_RSHIFT": "RShift",
    "NB_INPLACE_MATRIX_MULTIPLY": "MatMult",
}

if hasattr(opcode, "_nb_ops"):
    for idx, (nb_name, _symbol) in enumerate(opcode._nb_ops):
        if nb_name in _NB_TO_AST:
            _BINOP_ARGS[_NB_TO_AST[nb_name]] = idx
        if nb_name in _NB_INPLACE_TO_AST:
            _INPLACE_BINOP_ARGS[_NB_INPLACE_TO_AST[nb_name]] = idx

# Reverse lookup: arg value -> AST name
_BINOP_ARG_TO_NAME: dict[int, str] = {v: k for k, v in _BINOP_ARGS.items()}
_INPLACE_ARG_TO_NAME: dict[int, str] = {v: k for k, v in _INPLACE_BINOP_ARGS.items()}

# ---------------------------------------------------------------------------
# COMPARE_OP arg mapping (AST operator name -> oparg)
# ---------------------------------------------------------------------------
# Built dynamically by compiling tiny expressions — this is version-safe
# since CPython's COMPARE_OP encoding has changed across versions.

_COMPARE_ARGS: dict[str, int] = {}
_COMPARE_ARG_TO_NAME: dict[int, str] = {}
# Maps AST name -> cmp_index (the upper bits of COMPARE_OP arg, i.e. arg >> 5)
_COMPARE_INDEX: dict[str, int] = {}
_COMPARE_INDEX_TO_NAME: dict[int, str] = {}

_COMPARE_SYMBOLS: list[tuple[str, str]] = [
    ("Lt", "<"),
    ("LtE", "<="),
    ("Eq", "=="),
    ("NotEq", "!="),
    ("Gt", ">"),
    ("GtE", ">="),
]

# In Python 3.12+, COMPARE_OP arg = (cmp_index << 5) | predicate_mask.
# The predicate_mask encodes which comparison outcomes are truthy, and
# bit 4 (0x10) is an additional "bool" flag set when the comparison is
# used in a boolean context (e.g. ``if x > 0:``).
# We store the "standalone" arg (from ``a <op> b``) and derive the
# cmp_index from it.  When patching, we preserve the old predicate bits
# and swap only the comparison-specific bits.
_COMPARE_BOOL_FLAG = 0x10  # bit 4 — set in boolean context

for _ast_name, _symbol in _COMPARE_SYMBOLS:
    _code = compile(f"a {_symbol} b", "<leela-init>", "eval")
    for _instr in dis.get_instructions(_code):
        if _instr.opname == "COMPARE_OP":
            _COMPARE_ARGS[_ast_name] = _instr.arg
            _cmp_idx = _instr.arg >> 5
            _COMPARE_INDEX[_ast_name] = _cmp_idx
            _COMPARE_INDEX_TO_NAME[_cmp_idx] = _ast_name
            _COMPARE_ARG_TO_NAME[_instr.arg] = _ast_name
            break

# IS_OP: arg 0 = is, arg 1 = is not
# CONTAINS_OP: arg 0 = in, arg 1 = not in
_IS_ARGS: dict[str, int] = {"Is": 0, "IsNot": 1}
_IS_ARG_TO_NAME: dict[int, str] = {0: "Is", 1: "IsNot"}
_CONTAINS_ARGS: dict[str, int] = {"In": 0, "NotIn": 1}
_CONTAINS_ARG_TO_NAME: dict[int, str] = {0: "In", 1: "NotIn"}

# ---------------------------------------------------------------------------
# Unary opcode mapping
# ---------------------------------------------------------------------------
_UNARY_OPCODES: dict[str, int | None] = {
    "USub": _UNARY_NEGATIVE,
    "UAdd": None,  # UAdd uses CALL_INTRINSIC_1 on 3.12+, not patchable by opcode swap
    "Not": _UNARY_NOT,
}
_UNARY_OPCODE_TO_NAME: dict[int, str] = {}
if _UNARY_NEGATIVE is not None:
    _UNARY_OPCODE_TO_NAME[_UNARY_NEGATIVE] = "USub"
if _UNARY_NOT is not None:
    _UNARY_OPCODE_TO_NAME[_UNARY_NOT] = "Not"
if _UNARY_INVERT is not None:
    _UNARY_OPCODE_TO_NAME[_UNARY_INVERT] = "Invert"

# Node types eligible for bytecode mutation
_BYTECODE_ELIGIBLE_NODE_TYPES = frozenset({"BinOp", "AugAssign", "Compare", "UnaryOp"})

# Node types that are never eligible (require AST rewriting)
_AST_ONLY_NODE_TYPES = frozenset(
    {"BoolOp", "Return", "IfExp", "Break", "Continue", "ExceptHandler"}
)


def is_supported() -> bool:
    """Check if bytecode mutation is supported on this Python version."""
    return sys.version_info >= _MIN_VERSION


def is_bytecode_eligible(mutant: Mutant) -> bool:
    """Return True if the mutation can be done via bytecode patching.

    Eligible: BinOp, AugAssign, Compare (simple ops via COMPARE_OP, IS_OP,
    CONTAINS_OP), UnaryOp (USub only — UAdd uses CALL_INTRINSIC_1 and Not
    requires a TO_BOOL prefix that can't be simply swapped).

    NOT eligible: BoolOp, Return, IfExp, Break/Continue, ExceptHandler,
    UnaryOp with _remove replacement, UAdd.
    """
    if not is_supported():
        return False

    node_type = mutant.point.node_type
    if node_type not in _BYTECODE_ELIGIBLE_NODE_TYPES:
        return False

    # UnaryOp: only USub <-> UAdd is NOT bytecode-eligible because UAdd
    # uses CALL_INTRINSIC_1.  USub <-> UAdd would need opcode+arg rewrite.
    # _remove (removing `not`) also requires structural AST changes.
    if node_type == "UnaryOp":
        original = mutant.point.original_op
        replacement = mutant.replacement_op
        # Only USub -> UAdd via opcode swap is possible if both have dedicated opcodes
        # But UAdd uses CALL_INTRINSIC_1 on Python 3.12+, so it's not a simple swap
        # The only safe unary swap is between opcodes that exist as single instructions
        # Currently none of the UnaryOp mutations can be done purely by bytecode
        return False

    # BinOp/AugAssign: check that replacement has a known BINARY_OP arg
    if node_type in ("BinOp", "AugAssign"):
        if node_type == "BinOp":
            return mutant.replacement_op in _BINOP_ARGS
        else:
            return mutant.replacement_op in _INPLACE_BINOP_ARGS

    # Compare: check the specific sub-kind
    if node_type == "Compare":
        original = mutant.point.original_op
        replacement = mutant.replacement_op
        # COMPARE_OP family: Lt, LtE, Eq, NotEq, Gt, GtE
        if original in _COMPARE_ARGS and replacement in _COMPARE_ARGS:
            return True
        # IS_OP family: Is, IsNot
        if original in _IS_ARGS and replacement in _IS_ARGS:
            return True
        # CONTAINS_OP family: In, NotIn
        if original in _CONTAINS_ARGS and replacement in _CONTAINS_ARGS:
            return True
        # Cross-family (e.g., Lt -> Is) is not a simple arg swap
        return False

    return False


def _instruction_matches_binop(
    instr: dis.Instruction,
    lineno: int,
    col_offset: int,
    original_op: str,
    arg_map: dict[str, int],
) -> bool:
    """Check if an instruction matches a BinOp/AugAssign mutation point."""
    if instr.opname != "BINARY_OP":
        return False
    if instr.positions is None:
        return False
    if instr.positions.lineno != lineno:
        return False
    expected_arg = arg_map.get(original_op)
    if expected_arg is None:
        return False
    return instr.arg == expected_arg


def _instruction_matches_compare(
    instr: dis.Instruction,
    lineno: int,
    col_offset: int,
    original_op: str,
) -> bool:
    """Check if an instruction matches a Compare mutation point.

    For COMPARE_OP, matches by cmp_index (arg >> 5) rather than exact
    arg value, because the lower 5 bits encode a context-dependent
    predicate mask (standalone vs boolean context).
    """
    if original_op in _COMPARE_INDEX:
        if instr.opname != "COMPARE_OP":
            return False
        expected_index = _COMPARE_INDEX[original_op]
        if (instr.arg >> 5) != expected_index:
            return False
    elif original_op in _IS_ARGS:
        if instr.opname != "IS_OP":
            return False
        if instr.arg != _IS_ARGS[original_op]:
            return False
    elif original_op in _CONTAINS_ARGS:
        if instr.opname != "CONTAINS_OP":
            return False
        if instr.arg != _CONTAINS_ARGS[original_op]:
            return False
    else:
        return False

    if instr.positions is None:
        return False
    return instr.positions.lineno == lineno


def find_target_instruction(
    code: types.CodeType,
    lineno: int,
    col_offset: int,
    node_type: str,
    original_op: str,
) -> tuple[types.CodeType, int] | None:
    """Walk the code object to find the bytecode instruction matching a mutation point.

    Returns ``(containing_code_object, instruction_offset)`` or ``None``
    if no matching instruction is found.

    Recursively searches nested code objects in ``co_consts`` for
    mutations inside functions, methods, or class bodies.
    """
    result = _search_code_object(code, lineno, col_offset, node_type, original_op)
    if result is not None:
        return result

    # Search nested code objects (functions, classes, lambdas, comprehensions)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            result = find_target_instruction(
                const, lineno, col_offset, node_type, original_op
            )
            if result is not None:
                return result

    return None


def _search_code_object(
    code: types.CodeType,
    lineno: int,
    col_offset: int,
    node_type: str,
    original_op: str,
) -> tuple[types.CodeType, int] | None:
    """Search a single code object (non-recursive) for the target instruction."""
    for instr in dis.get_instructions(code):
        if instr.positions is None:
            continue

        if node_type in ("BinOp", "AugAssign"):
            arg_map = _BINOP_ARGS if node_type == "BinOp" else _INPLACE_BINOP_ARGS
            if _instruction_matches_binop(
                instr, lineno, col_offset, original_op, arg_map
            ):
                # Disambiguate by col_offset when multiple same-type ops on one line
                if instr.positions.col_offset == col_offset:
                    return (code, instr.offset)

        elif node_type == "Compare":
            if _instruction_matches_compare(instr, lineno, col_offset, original_op):
                if instr.positions.col_offset == col_offset:
                    return (code, instr.offset)

    # If col_offset matching found nothing, try line-only matching with
    # a single candidate (unambiguous case).
    candidates: list[tuple[types.CodeType, int]] = []
    for instr in dis.get_instructions(code):
        if instr.positions is None:
            continue

        if node_type in ("BinOp", "AugAssign"):
            arg_map = _BINOP_ARGS if node_type == "BinOp" else _INPLACE_BINOP_ARGS
            if _instruction_matches_binop(
                instr, lineno, col_offset, original_op, arg_map
            ):
                candidates.append((code, instr.offset))

        elif node_type == "Compare":
            if _instruction_matches_compare(instr, lineno, col_offset, original_op):
                candidates.append((code, instr.offset))

    if len(candidates) == 1:
        return candidates[0]

    return None


def patch_code_object(
    root_code: types.CodeType,
    target_code: types.CodeType,
    offset: int,
    new_arg: int,
    new_opcode: int | None = None,
) -> types.CodeType:
    """Create a mutated copy of a code object with one instruction patched.

    If *target_code* is the *root_code* itself, patches directly.
    Otherwise, replaces the nested code object in ``co_consts``
    recursively up to the root.
    """
    patched_target = _patch_single_code(target_code, offset, new_arg, new_opcode)

    if target_code is root_code:
        return patched_target

    return _replace_nested_code(root_code, target_code, patched_target)


def _patch_single_code(
    code: types.CodeType,
    offset: int,
    new_arg: int,
    new_opcode: int | None = None,
) -> types.CodeType:
    """Patch a single instruction in a code object's bytecode."""
    bytecode = bytearray(code.co_code)
    if new_opcode is not None:
        bytecode[offset] = new_opcode
    bytecode[offset + 1] = new_arg
    return code.replace(co_code=bytes(bytecode))


def _replace_nested_code(
    root: types.CodeType,
    old_nested: types.CodeType,
    new_nested: types.CodeType,
) -> types.CodeType:
    """Replace a nested code object inside root's co_consts (recursively)."""
    new_consts = []
    replaced = False
    for const in root.co_consts:
        if const is old_nested:
            new_consts.append(new_nested)
            replaced = True
        elif isinstance(const, types.CodeType):
            # Recurse into nested code objects
            candidate = _replace_nested_code(const, old_nested, new_nested)
            if candidate is not const:
                replaced = True
            new_consts.append(candidate)
        else:
            new_consts.append(const)

    if replaced:
        return root.replace(co_consts=tuple(new_consts))
    return root


def _compute_replacement(
    mutant: Mutant,
    old_arg: int,
) -> tuple[int, int | None] | None:
    """Compute (new_arg, new_opcode_or_None) for a bytecode mutation.

    *old_arg* is the current arg byte at the target instruction offset.
    For COMPARE_OP, the old arg's predicate bits (lower 5 bits) are
    preserved and combined with the new comparison's base arg.

    Returns ``None`` if the mutation cannot be expressed as a bytecode patch.
    """
    node_type = mutant.point.node_type
    replacement = mutant.replacement_op

    if node_type == "BinOp":
        new_arg = _BINOP_ARGS.get(replacement)
        if new_arg is None:
            return None
        return (new_arg, None)

    if node_type == "AugAssign":
        new_arg = _INPLACE_BINOP_ARGS.get(replacement)
        if new_arg is None:
            return None
        return (new_arg, None)

    if node_type == "Compare":
        original = mutant.point.original_op
        # COMPARE_OP family: preserve predicate bits from old arg
        if original in _COMPARE_INDEX and replacement in _COMPARE_INDEX:
            new_base = _COMPARE_ARGS[replacement]
            # Preserve the bool-context flag (bit 4) from the old instruction
            bool_flag = old_arg & _COMPARE_BOOL_FLAG
            new_arg = new_base | bool_flag
            return (new_arg, None)
        # IS_OP family
        if original in _IS_ARGS and replacement in _IS_ARGS:
            return (_IS_ARGS[replacement], None)
        # CONTAINS_OP family
        if original in _CONTAINS_ARGS and replacement in _CONTAINS_ARGS:
            return (_CONTAINS_ARGS[replacement], None)
        return None

    return None


def mutate_code(
    code: types.CodeType,
    mutant: Mutant,
) -> types.CodeType | None:
    """Find, compute, and patch one bytecode mutation.

    Returns ``None`` if bytecode mutation isn't possible for this
    mutant.
    """
    if not is_bytecode_eligible(mutant):
        return None

    target = find_target_instruction(
        code,
        mutant.point.lineno,
        mutant.point.col_offset,
        mutant.point.node_type,
        mutant.point.original_op,
    )
    if target is None:
        return None

    target_code, offset = target
    old_arg = target_code.co_code[offset + 1]

    replacement = _compute_replacement(mutant, old_arg)
    if replacement is None:
        return None

    new_arg, new_opcode = replacement
    return patch_code_object(code, target_code, offset, new_arg, new_opcode)
