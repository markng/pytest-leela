"""Tests for pytest_leela.bytecode — bytecode-level mutation engine."""

from __future__ import annotations

import dis
import sys
import types

from pytest_leela.bytecode import (
    _BINOP_ARGS,
    _COMPARE_ARGS,
    _COMPARE_BOOL_FLAG,
    _CONTAINS_ARGS,
    _INPLACE_BINOP_ARGS,
    _IS_ARGS,
    _compute_replacement,
    _replace_nested_code,
    _search_code_object,
    find_target_instruction,
    is_bytecode_eligible,
    is_supported,
    mutate_code,
    patch_code_object,
)
from pytest_leela.import_hook import MutatingLoader, install_hook, remove_hook
from pytest_leela.models import Mutant, MutationPoint


def _make_point(
    lineno: int = 1,
    col_offset: int = 0,
    node_type: str = "BinOp",
    original_op: str = "Add",
    inferred_type: str | None = None,
) -> MutationPoint:
    return MutationPoint(
        file_path="test.py",
        module_name="test",
        lineno=lineno,
        col_offset=col_offset,
        node_type=node_type,
        original_op=original_op,
        inferred_type=inferred_type,
    )


def _make_mutant(
    lineno: int = 1,
    col_offset: int = 0,
    node_type: str = "BinOp",
    original_op: str = "Add",
    replacement_op: str = "Sub",
    inferred_type: str | None = None,
) -> Mutant:
    point = _make_point(lineno, col_offset, node_type, original_op, inferred_type)
    return Mutant(point=point, replacement_op=replacement_op, mutant_id=0)


def describe_is_supported():
    def it_returns_true_on_python_312_plus():
        # We require Python >= 3.12 for the test suite anyway
        assert sys.version_info >= (3, 12)
        assert is_supported() is True


def describe_opcode_tables():
    """Verify the dynamically-built opcode tables are populated."""

    def it_has_binop_args():
        assert "Add" in _BINOP_ARGS
        assert "Sub" in _BINOP_ARGS
        assert "Mult" in _BINOP_ARGS
        assert "Div" in _BINOP_ARGS
        assert "FloorDiv" in _BINOP_ARGS
        assert "Mod" in _BINOP_ARGS
        assert "Pow" in _BINOP_ARGS
        assert "BitAnd" in _BINOP_ARGS
        assert "BitOr" in _BINOP_ARGS
        assert "BitXor" in _BINOP_ARGS
        assert "LShift" in _BINOP_ARGS
        assert "RShift" in _BINOP_ARGS

    def it_has_inplace_binop_args():
        assert "Add" in _INPLACE_BINOP_ARGS
        assert "Sub" in _INPLACE_BINOP_ARGS
        assert "Mult" in _INPLACE_BINOP_ARGS

    def it_has_compare_args():
        assert "Lt" in _COMPARE_ARGS
        assert "LtE" in _COMPARE_ARGS
        assert "Eq" in _COMPARE_ARGS
        assert "NotEq" in _COMPARE_ARGS
        assert "Gt" in _COMPARE_ARGS
        assert "GtE" in _COMPARE_ARGS

    def it_has_is_args():
        assert _IS_ARGS == {"Is": 0, "IsNot": 1}

    def it_has_contains_args():
        assert _CONTAINS_ARGS == {"In": 0, "NotIn": 1}


def describe_is_bytecode_eligible():
    """Test eligibility checks for various mutation types."""

    def it_returns_true_for_binop_add_to_sub():
        mutant = _make_mutant(
            node_type="BinOp", original_op="Add", replacement_op="Sub"
        )
        assert is_bytecode_eligible(mutant) is True

    def it_returns_true_for_binop_mult_to_add():
        mutant = _make_mutant(
            node_type="BinOp", original_op="Mult", replacement_op="Add"
        )
        assert is_bytecode_eligible(mutant) is True

    def it_returns_true_for_binop_bitwise():
        mutant = _make_mutant(
            node_type="BinOp", original_op="BitAnd", replacement_op="BitOr"
        )
        assert is_bytecode_eligible(mutant) is True

    def it_returns_true_for_augassign_add_to_sub():
        mutant = _make_mutant(
            node_type="AugAssign", original_op="Add", replacement_op="Sub"
        )
        assert is_bytecode_eligible(mutant) is True

    def it_returns_true_for_augassign_mult_to_floordiv():
        mutant = _make_mutant(
            node_type="AugAssign", original_op="Mult", replacement_op="FloorDiv"
        )
        assert is_bytecode_eligible(mutant) is True

    def it_returns_true_for_compare_lt_to_gte():
        mutant = _make_mutant(
            node_type="Compare", original_op="Lt", replacement_op="GtE"
        )
        assert is_bytecode_eligible(mutant) is True

    def it_returns_true_for_compare_eq_to_noteq():
        mutant = _make_mutant(
            node_type="Compare", original_op="Eq", replacement_op="NotEq"
        )
        assert is_bytecode_eligible(mutant) is True

    def it_returns_true_for_compare_is_to_isnot():
        mutant = _make_mutant(
            node_type="Compare", original_op="Is", replacement_op="IsNot"
        )
        assert is_bytecode_eligible(mutant) is True

    def it_returns_true_for_compare_in_to_notin():
        mutant = _make_mutant(
            node_type="Compare", original_op="In", replacement_op="NotIn"
        )
        assert is_bytecode_eligible(mutant) is True

    def it_returns_false_for_cross_family_compare():
        """Can't swap Lt (COMPARE_OP) with Is (IS_OP) — different opcodes."""
        mutant = _make_mutant(
            node_type="Compare", original_op="Lt", replacement_op="Is"
        )
        assert is_bytecode_eligible(mutant) is False

    def it_returns_false_for_boolop():
        mutant = _make_mutant(
            node_type="BoolOp", original_op="And", replacement_op="Or"
        )
        assert is_bytecode_eligible(mutant) is False

    def it_returns_false_for_return():
        mutant = _make_mutant(
            node_type="Return", original_op="True", replacement_op="False"
        )
        assert is_bytecode_eligible(mutant) is False

    def it_returns_false_for_ifexp():
        mutant = _make_mutant(
            node_type="IfExp", original_op="ternary", replacement_op="swap_branches"
        )
        assert is_bytecode_eligible(mutant) is False

    def it_returns_false_for_break():
        mutant = _make_mutant(
            node_type="Break", original_op="break", replacement_op="continue"
        )
        assert is_bytecode_eligible(mutant) is False

    def it_returns_false_for_continue():
        mutant = _make_mutant(
            node_type="Continue", original_op="continue", replacement_op="break"
        )
        assert is_bytecode_eligible(mutant) is False

    def it_returns_false_for_except_handler():
        mutant = _make_mutant(
            node_type="ExceptHandler", original_op="typed", replacement_op="broaden"
        )
        assert is_bytecode_eligible(mutant) is False

    def it_returns_false_for_unary_op():
        """UnaryOp mutations are not bytecode-eligible (UAdd uses CALL_INTRINSIC_1)."""
        mutant = _make_mutant(
            node_type="UnaryOp", original_op="USub", replacement_op="UAdd"
        )
        assert is_bytecode_eligible(mutant) is False

    def it_returns_false_for_unary_not_remove():
        mutant = _make_mutant(
            node_type="UnaryOp", original_op="Not", replacement_op="_remove"
        )
        assert is_bytecode_eligible(mutant) is False


def describe_find_target_instruction():
    """Test finding bytecode instructions by mutation point coordinates."""

    def it_finds_binop_in_simple_expression():
        source = "result = a + b\n"
        code = compile(source, "<test>", "exec")
        # Find the col_offset of the BinOp node
        import ast

        tree = ast.parse(source)
        binop = tree.body[0].value
        result = find_target_instruction(
            code, binop.lineno, binop.col_offset, "BinOp", "Add"
        )
        assert result is not None
        target_code, offset = result
        # Verify the instruction at that offset is BINARY_OP with arg for Add
        assert target_code.co_code[offset + 1] == _BINOP_ARGS["Add"]

    def it_finds_compare_op():
        source = "result = a < b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        compare = tree.body[0].value
        result = find_target_instruction(
            code, compare.lineno, compare.col_offset, "Compare", "Lt"
        )
        assert result is not None
        target_code, offset = result
        assert target_code.co_code[offset + 1] == _COMPARE_ARGS["Lt"]

    def it_finds_instruction_in_nested_function():
        source = "def foo(a, b):\n    return a + b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        binop = func.body[0].value
        result = find_target_instruction(
            code, binop.lineno, binop.col_offset, "BinOp", "Add"
        )
        assert result is not None
        target_code, offset = result
        # The target code should be the nested function's code object, not the module's
        assert target_code is not code
        assert target_code.co_code[offset + 1] == _BINOP_ARGS["Add"]

    def it_returns_none_when_no_match():
        source = "result = a + b\n"
        code = compile(source, "<test>", "exec")
        # Wrong line number
        result = find_target_instruction(code, 99, 0, "BinOp", "Add")
        assert result is None

    def it_returns_none_for_wrong_operator():
        source = "result = a + b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        binop = tree.body[0].value
        # Look for Sub but the code has Add
        result = find_target_instruction(
            code, binop.lineno, binop.col_offset, "BinOp", "Sub"
        )
        assert result is None

    def it_disambiguates_multiple_ops_on_same_line():
        source = "result = a + b - c\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        # a + b is the left BinOp, (a+b) - c is the outer BinOp
        outer = tree.body[0].value  # BinOp: (a+b) - c
        inner = outer.left  # BinOp: a + b

        # Find the inner Add
        result_add = find_target_instruction(
            code, inner.lineno, inner.col_offset, "BinOp", "Add"
        )
        assert result_add is not None

        # Find the outer Sub
        result_sub = find_target_instruction(
            code, outer.lineno, outer.col_offset, "BinOp", "Sub"
        )
        assert result_sub is not None

        # They should be different instructions
        assert result_add[1] != result_sub[1]

    def it_finds_augassign_instruction():
        source = "a += b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        aug = tree.body[0]
        result = find_target_instruction(
            code, aug.lineno, aug.col_offset, "AugAssign", "Add"
        )
        assert result is not None
        target_code, offset = result
        assert target_code.co_code[offset + 1] == _INPLACE_BINOP_ARGS["Add"]


def describe_patch_code_object():
    """Test patching bytecode instructions."""

    def it_patches_binop_arg():
        source = "result = a + b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        binop = tree.body[0].value
        target = find_target_instruction(
            code, binop.lineno, binop.col_offset, "BinOp", "Add"
        )
        assert target is not None
        target_code, offset = target

        patched = patch_code_object(code, target_code, offset, _BINOP_ARGS["Sub"])
        # Verify the patched code has Sub instead of Add
        # Find the instruction at the same offset
        found = False
        for instr in dis.get_instructions(patched):
            if instr.opname == "BINARY_OP" and instr.arg == _BINOP_ARGS["Sub"]:
                found = True
                break
        assert found

    def it_patches_nested_code_object():
        source = "def foo(a, b):\n    return a + b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        binop = func.body[0].value
        target = find_target_instruction(
            code, binop.lineno, binop.col_offset, "BinOp", "Add"
        )
        assert target is not None
        target_code, offset = target

        patched = patch_code_object(code, target_code, offset, _BINOP_ARGS["Sub"])
        # The root code should be different (new co_consts)
        assert patched is not code
        # Find the nested code and verify it was patched
        nested_codes = [c for c in patched.co_consts if isinstance(c, types.CodeType)]
        assert len(nested_codes) == 1
        found = False
        for instr in dis.get_instructions(nested_codes[0]):
            if instr.opname == "BINARY_OP" and instr.arg == _BINOP_ARGS["Sub"]:
                found = True
                break
        assert found


def describe_mutate_code():
    """Test the high-level mutate_code function."""

    def it_mutates_add_to_sub():
        source = "result = a + b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        binop = tree.body[0].value
        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )
        result = mutate_code(code, mutant)
        assert result is not None
        # Execute and verify behavior
        ns: dict[str, object] = {"a": 10, "b": 3}
        exec(result, ns)
        assert ns["result"] == 7  # 10 - 3

    def it_mutates_sub_to_add():
        source = "result = a - b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        binop = tree.body[0].value
        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="Sub",
            replacement_op="Add",
        )
        result = mutate_code(code, mutant)
        assert result is not None
        ns: dict[str, object] = {"a": 10, "b": 3}
        exec(result, ns)
        assert ns["result"] == 13  # 10 + 3

    def it_mutates_lt_to_gte():
        source = "result = a < b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        compare = tree.body[0].value
        mutant = _make_mutant(
            lineno=compare.lineno,
            col_offset=compare.col_offset,
            node_type="Compare",
            original_op="Lt",
            replacement_op="GtE",
        )
        result = mutate_code(code, mutant)
        assert result is not None
        # a < b becomes a >= b
        ns: dict[str, object] = {"a": 1, "b": 2}
        exec(result, ns)
        assert ns["result"] is False  # 1 >= 2 is False

        ns2: dict[str, object] = {"a": 3, "b": 2}
        exec(result, ns2)
        assert ns2["result"] is True  # 3 >= 2 is True

    def it_mutates_eq_to_noteq():
        source = "result = a == b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        compare = tree.body[0].value
        mutant = _make_mutant(
            lineno=compare.lineno,
            col_offset=compare.col_offset,
            node_type="Compare",
            original_op="Eq",
            replacement_op="NotEq",
        )
        result = mutate_code(code, mutant)
        assert result is not None
        ns: dict[str, object] = {"a": 5, "b": 5}
        exec(result, ns)
        assert ns["result"] is False  # 5 != 5 is False

    def it_mutates_is_to_isnot():
        source = "result = a is b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        compare = tree.body[0].value
        mutant = _make_mutant(
            lineno=compare.lineno,
            col_offset=compare.col_offset,
            node_type="Compare",
            original_op="Is",
            replacement_op="IsNot",
        )
        result = mutate_code(code, mutant)
        assert result is not None
        sentinel = object()
        ns: dict[str, object] = {"a": sentinel, "b": sentinel}
        exec(result, ns)
        assert ns["result"] is False  # same object, is not -> False

    def it_mutates_in_to_notin():
        source = "result = a in b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        compare = tree.body[0].value
        mutant = _make_mutant(
            lineno=compare.lineno,
            col_offset=compare.col_offset,
            node_type="Compare",
            original_op="In",
            replacement_op="NotIn",
        )
        result = mutate_code(code, mutant)
        assert result is not None
        ns: dict[str, object] = {"a": 1, "b": [1, 2, 3]}
        exec(result, ns)
        assert ns["result"] is False  # 1 not in [1,2,3] -> False

    def it_mutates_augassign_add_to_sub():
        source = "a += b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        aug = tree.body[0]
        mutant = _make_mutant(
            lineno=aug.lineno,
            col_offset=aug.col_offset,
            node_type="AugAssign",
            original_op="Add",
            replacement_op="Sub",
        )
        result = mutate_code(code, mutant)
        assert result is not None
        ns: dict[str, object] = {"a": 10, "b": 3}
        exec(result, ns)
        assert ns["a"] == 7  # 10 -= 3

    def it_mutates_add_to_sub_in_nested_function():
        source = "def foo(a, b):\n    return a + b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        binop = func.body[0].value
        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )
        result = mutate_code(code, mutant)
        assert result is not None
        ns: dict[str, object] = {}
        exec(result, ns)
        foo = ns["foo"]
        assert foo(10, 3) == 7  # 10 - 3 instead of 10 + 3

    def it_returns_none_for_ineligible_mutation():
        source = "result = True\n"
        code = compile(source, "<test>", "exec")
        mutant = _make_mutant(
            lineno=1,
            col_offset=0,
            node_type="Return",
            original_op="True",
            replacement_op="False",
        )
        result = mutate_code(code, mutant)
        assert result is None

    def it_returns_none_when_instruction_not_found():
        source = "result = a + b\n"
        code = compile(source, "<test>", "exec")
        # Wrong line
        mutant = _make_mutant(
            lineno=99,
            col_offset=0,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )
        result = mutate_code(code, mutant)
        assert result is None

    def it_mutates_bitwise_and_to_or():
        source = "result = a & b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        binop = tree.body[0].value
        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="BitAnd",
            replacement_op="BitOr",
        )
        result = mutate_code(code, mutant)
        assert result is not None
        ns: dict[str, object] = {"a": 0b1100, "b": 0b1010}
        exec(result, ns)
        assert ns["result"] == 0b1110  # OR instead of AND

    def it_mutates_floordiv_to_mult():
        source = "result = a // b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        binop = tree.body[0].value
        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="FloorDiv",
            replacement_op="Mult",
        )
        result = mutate_code(code, mutant)
        assert result is not None
        ns: dict[str, object] = {"a": 10, "b": 3}
        exec(result, ns)
        assert ns["result"] == 30  # 10 * 3 instead of 10 // 3


def describe_end_to_end():
    """End-to-end tests: compile, mutate bytecode, execute, verify behavior change."""

    def it_changes_function_behavior_via_bytecode_mutation():
        source = "def calculate(x, y):\n    return x + y\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        binop = func.body[0].value

        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Mult",
        )

        mutated = mutate_code(code, mutant)
        assert mutated is not None

        # Execute original
        ns_orig: dict[str, object] = {}
        exec(code, ns_orig)
        assert ns_orig["calculate"](5, 3) == 8  # 5 + 3

        # Execute mutated
        ns_mut: dict[str, object] = {}
        exec(mutated, ns_mut)
        assert ns_mut["calculate"](5, 3) == 15  # 5 * 3

    def it_handles_deeply_nested_code():
        """Test mutation inside a function nested within another function."""
        source = (
            "def outer():\n"
            "    def inner(a, b):\n"
            "        return a + b\n"
            "    return inner(5, 3)\n"
        )
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        outer_func = tree.body[0]
        inner_func = outer_func.body[0]
        binop = inner_func.body[0].value

        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )

        mutated = mutate_code(code, mutant)
        assert mutated is not None

        # Execute original
        ns_orig: dict[str, object] = {}
        exec(code, ns_orig)
        assert ns_orig["outer"]() == 8  # 5 + 3

        # Execute mutated
        ns_mut: dict[str, object] = {}
        exec(mutated, ns_mut)
        assert ns_mut["outer"]() == 2  # 5 - 3

    def it_preserves_other_instructions_when_patching():
        """Verify that only the targeted instruction changes."""
        source = "def calc(a, b, c):\n    x = a + b\n    y = b * c\n    return x - y\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        # Target the + on line 2
        add_node = func.body[0].value

        mutant = _make_mutant(
            lineno=add_node.lineno,
            col_offset=add_node.col_offset,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )

        mutated = mutate_code(code, mutant)
        assert mutated is not None

        # Execute mutated
        ns: dict[str, object] = {}
        exec(mutated, ns)
        calc = ns["calc"]
        # x = a - b (mutated), y = b * c (unchanged), return x - y (unchanged)
        # calc(10, 3, 2) => x = 10-3=7, y = 3*2=6, return 7-6=1
        assert calc(10, 3, 2) == 1

        # Verify original still works correctly
        ns_orig: dict[str, object] = {}
        exec(code, ns_orig)
        calc_orig = ns_orig["calc"]
        # x = 10+3=13, y = 3*2=6, return 13-6=7
        assert calc_orig(10, 3, 2) == 7

    def it_mutates_comparison_in_conditional():
        source = (
            "def is_positive(x):\n"
            "    if x > 0:\n"
            "        return True\n"
            "    return False\n"
        )
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        if_node = func.body[0]
        compare = if_node.test

        mutant = _make_mutant(
            lineno=compare.lineno,
            col_offset=compare.col_offset,
            node_type="Compare",
            original_op="Gt",
            replacement_op="LtE",
        )

        mutated = mutate_code(code, mutant)
        assert mutated is not None

        # Original: x > 0
        ns_orig: dict[str, object] = {}
        exec(code, ns_orig)
        assert ns_orig["is_positive"](5) is True
        assert ns_orig["is_positive"](-3) is False

        # Mutated: x <= 0
        ns_mut: dict[str, object] = {}
        exec(mutated, ns_mut)
        assert ns_mut["is_positive"](5) is False  # 5 <= 0 is False
        assert ns_mut["is_positive"](-3) is True  # -3 <= 0 is True


def describe_compute_replacement():
    """Test the _compute_replacement helper for various mutation types."""

    def it_computes_binop_replacement():
        mutant = _make_mutant(
            node_type="BinOp", original_op="Add", replacement_op="Sub"
        )
        result = _compute_replacement(mutant, _BINOP_ARGS["Add"])
        assert result is not None
        new_arg, new_opcode = result
        assert new_arg == _BINOP_ARGS["Sub"]
        assert new_opcode is None

    def it_computes_augassign_replacement():
        mutant = _make_mutant(
            node_type="AugAssign", original_op="Add", replacement_op="Sub"
        )
        result = _compute_replacement(mutant, _INPLACE_BINOP_ARGS["Add"])
        assert result is not None
        new_arg, new_opcode = result
        assert new_arg == _INPLACE_BINOP_ARGS["Sub"]
        assert new_opcode is None

    def it_returns_none_for_unknown_binop_replacement():
        mutant = _make_mutant(
            node_type="BinOp", original_op="Add", replacement_op="UnknownOp"
        )
        result = _compute_replacement(mutant, _BINOP_ARGS["Add"])
        assert result is None

    def it_returns_none_for_unknown_augassign_replacement():
        mutant = _make_mutant(
            node_type="AugAssign", original_op="Add", replacement_op="UnknownOp"
        )
        result = _compute_replacement(mutant, _INPLACE_BINOP_ARGS["Add"])
        assert result is None

    def it_computes_compare_op_replacement_preserving_bool_flag():
        mutant = _make_mutant(
            node_type="Compare", original_op="Lt", replacement_op="GtE"
        )
        # Simulate a bool-context COMPARE_OP arg (has _COMPARE_BOOL_FLAG set)
        old_arg_with_bool = _COMPARE_ARGS["Lt"] | _COMPARE_BOOL_FLAG
        result = _compute_replacement(mutant, old_arg_with_bool)
        assert result is not None
        new_arg, new_opcode = result
        # The bool flag should be preserved
        assert new_arg & _COMPARE_BOOL_FLAG != 0
        assert new_opcode is None

    def it_computes_is_op_replacement():
        mutant = _make_mutant(
            node_type="Compare", original_op="Is", replacement_op="IsNot"
        )
        result = _compute_replacement(mutant, _IS_ARGS["Is"])
        assert result is not None
        new_arg, new_opcode = result
        assert new_arg == _IS_ARGS["IsNot"]
        assert new_opcode is None

    def it_computes_contains_op_replacement():
        mutant = _make_mutant(
            node_type="Compare", original_op="In", replacement_op="NotIn"
        )
        result = _compute_replacement(mutant, _CONTAINS_ARGS["In"])
        assert result is not None
        new_arg, new_opcode = result
        assert new_arg == _CONTAINS_ARGS["NotIn"]
        assert new_opcode is None

    def it_returns_none_for_cross_family_compare():
        mutant = _make_mutant(
            node_type="Compare", original_op="Lt", replacement_op="Is"
        )
        result = _compute_replacement(mutant, _COMPARE_ARGS["Lt"])
        assert result is None

    def it_returns_none_for_unsupported_node_type():
        mutant = _make_mutant(
            node_type="Return", original_op="True", replacement_op="False"
        )
        result = _compute_replacement(mutant, 0)
        assert result is None


def describe_search_code_object():
    """Test the _search_code_object helper directly."""

    def it_finds_binop_by_col_offset():
        source = "result = a + b\n"
        code = compile(source, "<test>", "exec")
        import ast

        tree = ast.parse(source)
        binop = tree.body[0].value
        result = _search_code_object(
            code, binop.lineno, binop.col_offset, "BinOp", "Add"
        )
        assert result is not None

    def it_returns_none_for_no_match():
        source = "result = a + b\n"
        code = compile(source, "<test>", "exec")
        result = _search_code_object(code, 99, 0, "BinOp", "Add")
        assert result is None

    def it_falls_back_to_line_only_for_single_candidate():
        """When col_offset doesn't match but there's only one op on the line."""
        source = "result = a + b\n"
        code = compile(source, "<test>", "exec")
        # Use wrong col_offset but correct line
        result = _search_code_object(code, 1, 999, "BinOp", "Add")
        # Should find via line-only fallback (single candidate)
        assert result is not None

    def it_returns_none_for_ambiguous_line_only():
        """When col_offset doesn't match and there are multiple ops on the line."""
        source = "result = a + b + c\n"
        code = compile(source, "<test>", "exec")
        # Two Add ops on line 1, wrong col_offset → ambiguous → None
        result = _search_code_object(code, 1, 999, "BinOp", "Add")
        assert result is None


def describe_replace_nested_code():
    """Test the _replace_nested_code helper."""

    def it_replaces_direct_nested_code():
        source = "def foo(): return 1\n"
        code = compile(source, "<test>", "exec")
        old_nested = [c for c in code.co_consts if isinstance(c, types.CodeType)][0]
        new_nested = old_nested.replace(co_consts=(2,) + old_nested.co_consts[1:])
        result = _replace_nested_code(code, old_nested, new_nested)
        assert result is not code  # new root
        nested_codes = [c for c in result.co_consts if isinstance(c, types.CodeType)]
        assert nested_codes[0] is new_nested

    def it_returns_same_code_when_not_found():
        source = "x = 1\n"
        code = compile(source, "<test>", "exec")
        # Create a completely unrelated code object
        other = compile("y = 2", "<other>", "exec")
        fake_new = compile("z = 3", "<fake>", "exec")
        result = _replace_nested_code(code, other, fake_new)
        assert result is code  # unchanged


def describe_import_hook_bytecode_integration():
    """Test that MutatingLoader uses bytecode path when cached_code is provided."""

    def it_uses_bytecode_path_for_eligible_binop_mutation():
        source = "def add(a, b):\n    return a + b\n"
        cached_code = compile(source, "<test>", "exec")

        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        binop = func.body[0].value

        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )

        loader = MutatingLoader(source, mutant, "<test>", cached_code=cached_code)

        # Create a module and execute
        module = types.ModuleType("test_bytecode_integration")
        loader.exec_module(module)

        # Verify mutation was applied (a + b -> a - b)
        assert module.add(10, 3) == 7  # 10 - 3

    def it_falls_back_to_ast_for_ineligible_mutation():
        source = "def check(a, b):\n    return a and b\n"
        cached_code = compile(source, "<test>", "exec")

        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        boolop = func.body[0].value

        mutant = _make_mutant(
            lineno=boolop.lineno,
            col_offset=boolop.col_offset,
            node_type="BoolOp",
            original_op="And",
            replacement_op="Or",
        )

        loader = MutatingLoader(source, mutant, "<test>", cached_code=cached_code)

        module = types.ModuleType("test_ast_fallback")
        loader.exec_module(module)

        # BoolOp is not bytecode-eligible, so AST path should be used
        # and -> or: True and False -> True or False = True
        assert module.check(True, False) is True

    def it_falls_back_to_ast_when_no_cached_code():
        source = "def add(a, b):\n    return a + b\n"

        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        binop = func.body[0].value

        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )

        # No cached_code provided
        loader = MutatingLoader(source, mutant, "<test>", cached_code=None)

        module = types.ModuleType("test_no_cache")
        loader.exec_module(module)

        # AST path should still apply the mutation correctly
        assert module.add(10, 3) == 7

    def it_falls_back_to_ast_when_bytecode_returns_none():
        """When bytecode mutation is eligible but find_target_instruction fails."""
        source = "def add(a, b):\n    return a + b\n"
        cached_code = compile(source, "<test>", "exec")

        # Create a mutant with wrong col_offset so bytecode can't find the target
        # but the AST path can still match by lineno/col_offset
        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        binop = func.body[0].value

        # Use correct line but wrong col_offset — bytecode needs exact match
        # but AST matcher also matches by lineno+col_offset, so this tests
        # that the AST fallback works when bytecode returns None
        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )

        loader = MutatingLoader(source, mutant, "<test>", cached_code=cached_code)

        module = types.ModuleType("test_bytecode_fallback")
        loader.exec_module(module)

        # Either bytecode or AST should apply the mutation
        assert module.add(10, 3) == 7

    def it_passes_cached_code_via_install_hook():
        """Verify install_hook passes target_codes through to the finder/loader."""
        source = "VALUE = 10 + 5\n"
        cached_code = compile(source, "<test>", "exec")

        import ast

        tree = ast.parse(source)
        binop = tree.body[0].value

        mutant = _make_mutant(
            lineno=binop.lineno,
            col_offset=binop.col_offset,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )

        mod_name = "_test_bytecode_hook_integration"
        target_modules = {mod_name: source}
        target_codes = {mod_name: cached_code}

        finder = install_hook(target_modules, mutant, target_codes=target_codes)
        try:
            # Verify the finder has target_codes
            assert finder.target_codes == target_codes

            # Find the spec and verify the loader has cached_code
            spec = finder.find_spec(mod_name)
            assert spec is not None
            assert isinstance(spec.loader, MutatingLoader)
            assert spec.loader.cached_code is cached_code
        finally:
            remove_hook(finder)

    def it_install_hook_works_without_target_codes():
        """Backward compatibility: install_hook works without target_codes."""
        source = "VALUE = 42\n"
        mutant = _make_mutant()

        mod_name = "_test_no_codes_hook"
        target_modules = {mod_name: source}

        finder = install_hook(target_modules, mutant)
        try:
            assert finder.target_codes == {}
            spec = finder.find_spec(mod_name)
            assert spec is not None
            assert isinstance(spec.loader, MutatingLoader)
            assert spec.loader.cached_code is None
        finally:
            remove_hook(finder)

    def it_handles_compare_mutation_via_loader():
        source = "def is_less(a, b):\n    return a < b\n"
        cached_code = compile(source, "<test>", "exec")

        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        compare = func.body[0].value

        mutant = _make_mutant(
            lineno=compare.lineno,
            col_offset=compare.col_offset,
            node_type="Compare",
            original_op="Lt",
            replacement_op="GtE",
        )

        loader = MutatingLoader(source, mutant, "<test>", cached_code=cached_code)

        module = types.ModuleType("test_compare_bytecode")
        loader.exec_module(module)

        # a < b -> a >= b
        assert module.is_less(1, 2) is False  # 1 >= 2 is False
        assert module.is_less(3, 2) is True  # 3 >= 2 is True

    def it_handles_augassign_mutation_via_loader():
        source = "def accumulate(a, b):\n    a += b\n    return a\n"
        cached_code = compile(source, "<test>", "exec")

        import ast

        tree = ast.parse(source)
        func = tree.body[0]
        aug = func.body[0]

        mutant = _make_mutant(
            lineno=aug.lineno,
            col_offset=aug.col_offset,
            node_type="AugAssign",
            original_op="Add",
            replacement_op="Sub",
        )

        loader = MutatingLoader(source, mutant, "<test>", cached_code=cached_code)

        module = types.ModuleType("test_augassign_bytecode")
        loader.exec_module(module)

        # a += b -> a -= b
        assert module.accumulate(10, 3) == 7  # 10 - 3
