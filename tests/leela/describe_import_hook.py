"""Tests for pytest_leela.import_hook — AST mutation application and hook lifecycle."""

import ast
import sys

from pytest_leela.import_hook import (
    MutantApplier,
    apply_mutation,
    install_hook,
    remove_hook,
)
from pytest_leela.models import Mutant, MutationPoint


def _make_point(
    lineno: int = 1,
    col_offset: int = 0,
    node_type: str = "BinOp",
    original_op: str = "Add",
) -> MutationPoint:
    return MutationPoint(
        file_path="test.py",
        module_name="test",
        lineno=lineno,
        col_offset=col_offset,
        node_type=node_type,
        original_op=original_op,
        inferred_type=None,
    )


def _make_mutant(
    lineno: int = 1,
    col_offset: int = 0,
    node_type: str = "BinOp",
    original_op: str = "Add",
    replacement_op: str = "Sub",
) -> Mutant:
    point = _make_point(lineno, col_offset, node_type, original_op)
    return Mutant(point=point, replacement_op=replacement_op, mutant_id=0)


def describe_MutantApplier():
    def it_applies_binop_mutation():
        source = "x + y"
        tree = ast.parse(source, mode="eval")
        # The BinOp is at line 1, col 0
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="BinOp", original_op="Add", replacement_op="Sub",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        # Verify the operator changed to Sub
        assert isinstance(new_tree.body.op, ast.Sub)

    def it_applies_compare_mutation():
        source = "x > y"
        tree = ast.parse(source, mode="eval")
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="Compare", original_op="Gt", replacement_op="LtE",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        assert isinstance(new_tree.body.ops[0], ast.LtE)

    def it_applies_boolop_mutation():
        source = "a and b"
        tree = ast.parse(source, mode="eval")
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="BoolOp", original_op="And", replacement_op="Or",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        assert isinstance(new_tree.body.op, ast.Or)

    def it_applies_return_mutation():
        source = "def f():\n    return True\n"
        tree = ast.parse(source)
        # Return is at line 2, col 4
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="True", replacement_op="False",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        ret_node = new_tree.body[0].body[0]  # FunctionDef -> body[0] = Return
        assert isinstance(ret_node.value, ast.Constant)
        assert ret_node.value.value is False

    def describe_bitwise_operators():
        def it_applies_bitand_to_bitor():
            source = "x & y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BinOp", original_op="BitAnd", replacement_op="BitOr",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body.op, ast.BitOr)

        def it_applies_bitand_to_bitxor():
            source = "x & y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BinOp", original_op="BitAnd", replacement_op="BitXor",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body.op, ast.BitXor)

        def it_applies_bitor_to_bitand():
            source = "x | y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BinOp", original_op="BitOr", replacement_op="BitAnd",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body.op, ast.BitAnd)

        def it_applies_bitor_to_bitxor():
            source = "x | y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BinOp", original_op="BitOr", replacement_op="BitXor",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body.op, ast.BitXor)

        def it_applies_bitxor_to_bitand():
            source = "x ^ y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BinOp", original_op="BitXor", replacement_op="BitAnd",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body.op, ast.BitAnd)

        def it_applies_bitxor_to_bitor():
            source = "x ^ y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BinOp", original_op="BitXor", replacement_op="BitOr",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body.op, ast.BitOr)

        def it_applies_lshift_to_rshift():
            source = "x << y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BinOp", original_op="LShift", replacement_op="RShift",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body.op, ast.RShift)

        def it_applies_rshift_to_lshift():
            source = "x >> y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BinOp", original_op="RShift", replacement_op="LShift",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body.op, ast.LShift)

    def describe_augmented_assignment():
        def it_applies_augassign_add_to_sub():
            source = "x += y\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="AugAssign", original_op="Add", replacement_op="Sub",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body[0].op, ast.Sub)

        def it_applies_augassign_sub_to_add():
            source = "x -= y\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="AugAssign", original_op="Sub", replacement_op="Add",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body[0].op, ast.Add)

        def it_applies_augassign_mult_to_floordiv():
            source = "x *= y\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="AugAssign", original_op="Mult", replacement_op="FloorDiv",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body[0].op, ast.FloorDiv)

        def it_applies_augassign_bitand_to_bitor():
            source = "x &= y\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="AugAssign", original_op="BitAnd", replacement_op="BitOr",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body[0].op, ast.BitOr)

        def it_applies_augassign_lshift_to_rshift():
            source = "x <<= y\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="AugAssign", original_op="LShift", replacement_op="RShift",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body[0].op, ast.RShift)

        def it_does_not_apply_augassign_mutant_to_binop():
            source = "x + y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="AugAssign", original_op="Add", replacement_op="Sub",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body.op, ast.Add)

        def it_does_not_apply_binop_mutant_to_augassign():
            source = "x += y\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BinOp", original_op="Add", replacement_op="Sub",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body[0].op, ast.Add)

    def describe_ifexp():
        def it_swaps_branches():
            source = "x if cond else y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="IfExp", original_op="ternary", replacement_op="swap_branches",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            # After swap: body should be y (was x), orelse should be x (was y)
            assert isinstance(new_tree.body, ast.IfExp)
            assert new_tree.body.body.id == "y"
            assert new_tree.body.orelse.id == "x"

        def it_replaces_with_always_true_branch():
            source = "x if cond else y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="IfExp", original_op="ternary", replacement_op="always_true",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            # Entire IfExp replaced with just the body (x)
            assert isinstance(new_tree.body, ast.Name)
            assert new_tree.body.id == "x"

        def it_replaces_with_always_false_branch():
            source = "x if cond else y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="IfExp", original_op="ternary", replacement_op="always_false",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            # Entire IfExp replaced with just the orelse (y)
            assert isinstance(new_tree.body, ast.Name)
            assert new_tree.body.id == "y"

        def it_does_not_apply_ifexp_mutant_to_binop():
            source = "x + y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="IfExp", original_op="ternary", replacement_op="swap_branches",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False

        def it_does_not_apply_binop_mutant_to_ifexp():
            source = "x if cond else y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BinOp", original_op="Add", replacement_op="Sub",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body, ast.IfExp)

        def it_does_not_apply_non_ifexp_mutant_to_ifexp_with_valid_replacement():
            """Kills and→or on visit_IfExp guard: _matches True but wrong node_type."""
            source = "x if cond else y"
            tree = ast.parse(source, mode="eval")
            # Position matches the IfExp, but node_type says BoolOp
            # replacement_op swap_branches would apply if guard fails
            mutant = _make_mutant(
                lineno=1, col_offset=0,
                node_type="BoolOp", original_op="And", replacement_op="swap_branches",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            # IfExp branches must NOT be swapped
            assert isinstance(new_tree.body, ast.IfExp)
            assert new_tree.body.body.id == "x"
            assert new_tree.body.orelse.id == "y"

    def describe_break_continue():
        def it_replaces_break_with_continue():
            source = "for i in range(10):\n    break\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=2, col_offset=4,
                node_type="Break", original_op="break", replacement_op="continue",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body[0].body[0], ast.Continue)

        def it_replaces_continue_with_break():
            source = "for i in range(10):\n    continue\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=2, col_offset=4,
                node_type="Continue", original_op="continue", replacement_op="break",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            assert isinstance(new_tree.body[0].body[0], ast.Break)

        def it_does_not_apply_break_mutant_to_continue():
            source = "for i in range(10):\n    continue\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=2, col_offset=4,
                node_type="Break", original_op="break", replacement_op="continue",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body[0].body[0], ast.Continue)

        def it_does_not_apply_continue_mutant_to_break():
            source = "for i in range(10):\n    break\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=2, col_offset=4,
                node_type="Continue", original_op="continue", replacement_op="break",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body[0].body[0], ast.Break)

        def it_does_not_apply_non_break_mutant_to_break_node():
            """Kills and→or on visit_Break guard: _matches True but wrong node_type."""
            source = "for i in range(10):\n    break\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=2, col_offset=4,
                node_type="BinOp", original_op="Add", replacement_op="continue",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body[0].body[0], ast.Break)

        def it_does_not_apply_non_continue_mutant_to_continue_node():
            """Kills and→or on visit_Continue guard: _matches True but wrong node_type."""
            source = "for i in range(10):\n    continue\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=2, col_offset=4,
                node_type="BinOp", original_op="Add", replacement_op="break",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body[0].body[0], ast.Continue)

    def it_does_not_apply_when_location_mismatches():
        source = "x + y"
        tree = ast.parse(source, mode="eval")
        mutant = _make_mutant(
            lineno=99, col_offset=0,
            node_type="BinOp", original_op="Add", replacement_op="Sub",
        )
        applier = MutantApplier(mutant)
        applier.visit(tree)
        assert applier.applied is False


def describe_apply_mutation():
    def it_returns_modified_tree_and_applied_flag():
        source = "x + y"
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="BinOp", original_op="Add", replacement_op="Sub",
        )
        tree, applied = apply_mutation(source, mutant)
        assert applied is True


def describe_install_and_remove_hook():
    def it_installs_and_removes_hook():
        source = "x = 1\n"
        point = _make_point(lineno=1, col_offset=0, node_type="BinOp", original_op="Add")
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)

        target_modules = {"__test_dummy_module__": source}
        finder = install_hook(target_modules, mutant)
        try:
            assert finder in sys.meta_path
        finally:
            remove_hook(finder)
        assert finder not in sys.meta_path

    def it_survives_double_removal():
        source = "x = 1\n"
        point = _make_point(lineno=1, col_offset=0, node_type="BinOp", original_op="Add")
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)

        target_modules = {"__test_dummy_module2__": source}
        finder = install_hook(target_modules, mutant)
        remove_hook(finder)
        # Should not raise
        remove_hook(finder)
        assert finder not in sys.meta_path


def describe_UnaryOp_mutation():
    def it_removes_unary_op_with_remove_replacement():
        """UnaryOp with _remove strips the operator, returning the operand."""
        source = "not x"
        tree = ast.parse(source, mode="eval")
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="UnaryOp", original_op="Not", replacement_op="_remove",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        # The UnaryOp should be gone; body should now be just the Name node
        assert isinstance(new_tree.body, ast.Name)
        assert new_tree.body.id == "x"


def describe_Return_mutations():
    def it_applies_negate_mutation_to_return():
        """Return negate wraps the return value in -()."""
        source = "def f():\n    return value\n"
        tree = ast.parse(source)
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="value", replacement_op="negate",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        ret_node = new_tree.body[0].body[0]  # FunctionDef -> Return
        # Return value should be UnaryOp(USub, original_value)
        assert isinstance(ret_node.value, ast.UnaryOp)
        assert isinstance(ret_node.value.op, ast.USub)
        assert isinstance(ret_node.value.operand, ast.Name)

    def it_applies_empty_str_mutation_to_return():
        """Return empty_str replaces the return value with ''."""
        source = "def f():\n    return 42\n"
        tree = ast.parse(source)
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="42", replacement_op="empty_str",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        ret_node = new_tree.body[0].body[0]  # FunctionDef -> Return
        assert isinstance(ret_node.value, ast.Constant)
        assert ret_node.value.value == ""

    def it_applies_negate_expr_mutation():
        """negate_expr wraps return value with `not`."""
        source = "def f():\n    return value\n"
        tree = ast.parse(source)
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="expr", replacement_op="negate_expr",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        ret_node = new_tree.body[0].body[0]
        assert isinstance(ret_node.value, ast.UnaryOp)
        assert isinstance(ret_node.value.op, ast.Not)

    def it_applies_remove_negation_mutation():
        """remove_negation strips UnaryOp from return value."""
        source = "def f():\n    return -x\n"
        tree = ast.parse(source)
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="negation", replacement_op="remove_negation",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        ret_node = new_tree.body[0].body[0]
        # Should be just the operand (Name 'x'), not UnaryOp
        assert isinstance(ret_node.value, ast.Name)

    def it_applies_expr_mutation_to_return_none():
        """return None -> return True when replacement is 'expr'.

        Kills: line 122 == → !=, is not → is, is → is not.
        """
        source = "def f():\n    return None\n"
        tree = ast.parse(source)
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="None", replacement_op="expr",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        ret_node = new_tree.body[0].body[0]
        assert isinstance(ret_node.value, ast.Constant)
        assert ret_node.value.value is True

    def it_does_not_apply_expr_mutation_to_non_none_return():
        """return 42 should NOT be changed by expr replacement.

        Kills: line 122 is → is not (checking node.value.value is None).
        """
        source = "def f():\n    return 42\n"
        tree = ast.parse(source)
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="int_literal", replacement_op="expr",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is False
        ret_node = new_tree.body[0].body[0]
        assert isinstance(ret_node.value, ast.Constant)
        assert ret_node.value.value == 42

    def it_does_not_apply_expr_mutation_to_bare_return():
        """Bare return (no value) should NOT be changed by expr replacement.

        Kills: line 122 is not → is (checking node.value is not None).
        """
        source = "def f():\n    return\n"
        tree = ast.parse(source)
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="None", replacement_op="expr",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is False
        ret_node = new_tree.body[0].body[0]
        assert ret_node.value is None

    def it_does_not_apply_expr_to_non_expr_replacement():
        """The 'False' replacement must NOT trigger the expr handler.

        Kills: line 122 == → != (checking replacement == 'expr').
        """
        source = "def f():\n    return None\n"
        tree = ast.parse(source)
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="None", replacement_op="False",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        assert applier.applied is True
        ret_node = new_tree.body[0].body[0]
        # Should be False (from the "False" handler), not True (from "expr" handler)
        assert ret_node.value.value is False

    def it_does_not_apply_negate_expr_when_value_is_none():
        """negate_expr requires node.value is not None."""
        source = "def f():\n    return None\n"
        tree = ast.parse(source)
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="None", replacement_op="negate_expr",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        # negate_expr has guard `and node.value is not None` — None constant IS
        # not Python None, it's ast.Constant(value=None), so the guard allows it.
        # But for a bare `return` with no value, it would not apply.
        # Here we have `return None` which HAS a value (ast.Constant(None)).
        # So applied should be True since the node.value is not None (it's an AST node).
        assert applier.applied is True

    def it_does_not_apply_remove_negation_to_non_unary():
        """remove_negation requires isinstance(node.value, ast.UnaryOp)."""
        source = "def f():\n    return x\n"
        tree = ast.parse(source)
        mutant = _make_mutant(
            lineno=2, col_offset=4,
            node_type="Return", original_op="expr", replacement_op="remove_negation",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        ast.fix_missing_locations(new_tree)
        # remove_negation requires UnaryOp — Name is not UnaryOp
        assert applier.applied is False


def describe_node_type_discrimination():
    """Tests that ensure each visit method only mutates matching node types.

    These kill the `and → or` mutants on compound conditions like
    `self._matches(node) and self.mutant.point.node_type == "Compare"`.
    """

    def it_does_not_apply_compare_mutant_to_binop():
        """A Compare mutant at same location must not affect a BinOp."""
        source = "x + y\n"
        tree = ast.parse(source, mode="eval")
        # Mutant says "Compare" but node is BinOp
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="Compare", original_op="Gt", replacement_op="LtE",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        # BinOp should be untouched
        assert applier.applied is False
        assert isinstance(new_tree.body.op, ast.Add)

    def it_does_not_apply_boolop_mutant_to_compare():
        """A BoolOp mutant at same location must not affect a Compare."""
        source = "x > y\n"
        tree = ast.parse(source, mode="eval")
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="BoolOp", original_op="And", replacement_op="Or",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        assert applier.applied is False
        assert isinstance(new_tree.body.ops[0], ast.Gt)

    def it_does_not_apply_unaryop_mutant_to_boolop():
        """A UnaryOp mutant at same location must not affect a BoolOp."""
        source = "a and b\n"
        tree = ast.parse(source, mode="eval")
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="UnaryOp", original_op="Not", replacement_op="_remove",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        assert applier.applied is False
        assert isinstance(new_tree.body.op, ast.And)

    def it_does_not_apply_non_boolop_mutant_to_boolop_with_valid_replacement():
        """A non-BoolOp mutant at matching position must not mutate a BoolOp.

        This kills the `and → or` mutant on line 75 of import_hook.py:
            if self._matches(node) and self.mutant.point.node_type == "BoolOp"
        With `or`, the condition would fire when _matches is True regardless
        of node_type. Using a valid replacement_op (one that exists in
        _OP_CLASSES) ensures the mutation would actually be applied if the
        guard fails.
        """
        source = "a and b\n"
        tree = ast.parse(source, mode="eval")
        # Position matches the BoolOp node, but node_type says "BinOp"
        # replacement_op "Or" IS in _OP_CLASSES — so if the guard fails,
        # the mutation would be applied
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="BinOp", original_op="And", replacement_op="Or",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        assert applier.applied is False
        # The BoolOp operator must remain And (not changed to Or)
        assert isinstance(new_tree.body.op, ast.And)

    def it_does_not_apply_return_mutant_to_binop():
        """A Return mutant at same location must not affect a BinOp in a return."""
        source = "def f():\n    return x + y\n"
        tree = ast.parse(source)
        # Target the BinOp inside the return (line 2, col 11)
        binop_node = tree.body[0].body[0].value  # The x + y BinOp
        mutant = _make_mutant(
            lineno=2, col_offset=binop_node.col_offset,
            node_type="Return", original_op="expr", replacement_op="None",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        # The return at col 4 is a Return, not at the BinOp's col_offset
        # The BinOp should be untouched
        assert isinstance(new_tree.body[0].body[0].value, ast.BinOp)

    def it_does_not_apply_binop_mutant_to_unaryop():
        """A BinOp mutant at same location must not affect a UnaryOp."""
        source = "-x\n"
        tree = ast.parse(source, mode="eval")
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="BinOp", original_op="Add", replacement_op="Sub",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        assert applier.applied is False
        assert isinstance(new_tree.body.op, ast.USub)

    def it_does_not_apply_compare_mutation_to_other_compare_at_wrong_location():
        """Compare mutant at line 1 must not affect Compare at line 2."""
        source = "x > 1\ny < 2\n"
        tree = ast.parse(source, mode="exec")
        # Mutant targets line 1
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="Compare", original_op="Gt", replacement_op="LtE",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        assert applier.applied is True
        # Line 1 compare should be mutated
        assert isinstance(new_tree.body[0].value.ops[0], ast.LtE)
        # Line 2 compare should be untouched
        assert isinstance(new_tree.body[1].value.ops[0], ast.Lt)

    def it_does_not_mutate_unaryop_with_unknown_replacement():
        """UnaryOp with an unrecognized replacement_op should not apply."""
        source = "-x\n"
        tree = ast.parse(source, mode="eval")
        mutant = _make_mutant(
            lineno=1, col_offset=0,
            node_type="UnaryOp", original_op="USub", replacement_op="TotallyFake",
        )
        applier = MutantApplier(mutant)
        new_tree = applier.visit(tree)
        assert applier.applied is False


def describe_MutatingFinder():
    def it_returns_none_for_non_target_module():
        from pytest_leela.import_hook import MutatingFinder

        mutant = _make_mutant()
        finder = MutatingFinder({"target_mod": "x = 1\n"}, mutant)
        result = finder.find_spec("some_other_module")
        assert result is None

    def it_returns_spec_for_target_module():
        from pytest_leela.import_hook import MutatingFinder

        mutant = _make_mutant()
        finder = MutatingFinder({"target_mod": "x = 1\n"}, mutant)
        result = finder.find_spec("target_mod")
        assert result is not None
        assert result.name == "target_mod"

    def it_uses_file_path_from_set_file_paths():
        from pytest_leela.import_hook import MutatingFinder

        mutant = _make_mutant()
        finder = MutatingFinder({"mymod": "x = 1\n"}, mutant)
        finder.set_file_paths({"mymod": "/real/path/mymod.py"})
        spec = finder.find_spec("mymod")
        assert spec is not None
        assert spec.origin == "/real/path/mymod.py"


def describe_MutatingLoader():
    def it_create_module_returns_none():
        """create_module must return None for default module creation."""
        import importlib.machinery

        from pytest_leela.import_hook import MutatingLoader

        mutant = _make_mutant()
        loader = MutatingLoader("x = 1\n", mutant, "test.py")
        spec = importlib.machinery.ModuleSpec("test", loader)
        result = loader.create_module(spec)
        assert result is None


def describe_clear_target_modules():
    def it_clears_module_and_submodules():
        from pytest_leela.import_hook import clear_target_modules
        import types

        # Set up fake modules
        fake = types.ModuleType("fake_target")
        fake_sub = types.ModuleType("fake_target.sub")
        sys.modules["fake_target"] = fake
        sys.modules["fake_target.sub"] = fake_sub

        try:
            clear_target_modules(["fake_target"])
            assert "fake_target" not in sys.modules
            assert "fake_target.sub" not in sys.modules
        finally:
            sys.modules.pop("fake_target", None)
            sys.modules.pop("fake_target.sub", None)
