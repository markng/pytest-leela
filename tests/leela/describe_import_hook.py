"""Tests for pytest_leela.import_hook — AST mutation application and hook lifecycle."""

import ast
import importlib.machinery
import importlib.util
import sys
import types
from dataclasses import replace

import pytest

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
    @pytest.mark.parametrize("predicate", ["x", "obj.ready", "keep(x, y)", "y"])
    def it_negates_exactly_one_comprehension_filter_across_generators(predicate):
        source = "[(x, y) for x in xs if x if obj.ready for y in ys if keep(x, y) if y]"
        anchor = f"if {predicate}"
        mutant = _make_mutant(
            col_offset=source.index(anchor) + 3, node_type="ComprehensionFilter",
            original_op="condition", replacement_op="negate_condition",
        )
        tree, applied = apply_mutation(source, mutant)
        assert applied
        assert ast.dump(tree) == ast.dump(ast.parse(source.replace(anchor, f"if not {predicate}", 1)))

    def it_preserves_generator_laziness_short_circuiting_and_filter_evaluation():
        source = "result = (y for x in xs if x for y in children(x) if check(y) if allow(y))"
        tree, applied = apply_mutation(source, _make_mutant(
            col_offset=source.index("check"), node_type="ComprehensionFilter",
            original_op="condition", replacement_op="negate_condition",
        ))
        assert applied
        events = []

        def children(x):
            events.append(("children", x))
            return [1, 2]

        def check(y):
            events.append(("check", y))
            return [y] if y == 1 else []

        def allow(y):
            events.append(("allow", y))
            return True

        namespace = dict(xs=[0, 1], children=children, check=check, allow=allow)
        exec(compile(tree, "test.py", "exec"), namespace)
        assert events == []
        assert list(namespace["result"]) == [2]
        assert events == [("children", 1), ("check", 1), ("check", 2), ("allow", 2)]

    @pytest.mark.parametrize("predicate,mismatch", [
        ("x", {"col_offset": 0}), ("x", {"lineno": 2}),
        ("x", {"replacement_op": "unknown"}), ("x", {"node_type": "While"}),
        ("not x", {}), ("x and ready", {}), ("items[0]", {}),
    ])
    def it_keeps_ineligible_or_nonmatching_comprehension_filters(predicate, mismatch):
        source = f"[x for x in xs if {predicate}]"
        fields = dict(col_offset=source.index("if ") + 3, node_type="ComprehensionFilter",
                      original_op="condition", replacement_op="negate_condition")
        fields.update(mismatch)
        tree, applied = apply_mutation(source, _make_mutant(**fields))
        assert not applied
        assert ast.dump(tree) == ast.dump(ast.parse(source))

    def it_still_applies_compound_filter_child_mutations():
        source = "[x for x in xs if check(x == 1) if x]"
        tree, applied = apply_mutation(source, _make_mutant(
            col_offset=source.index("x =="), node_type="Compare",
            original_op="Eq", replacement_op="NotEq",
        ))
        assert applied
        assert ast.dump(tree) == ast.dump(ast.parse(source.replace("==", "!=")))

    @pytest.mark.parametrize("definition", ["def f():", "async def f():", "class f:"])
    @pytest.mark.parametrize("index", [0, 1, 2])
    def it_removes_only_the_decorator_at_the_selected_coordinates(definition, index):
        decorators = ["@wrap", "@factory()", "@wrap"]
        tail = [definition, "    pass", "", "@wrap", "def other(): pass"]
        source = "\n".join(decorators + tail)
        mutant = _make_mutant(
            lineno=index + 1, col_offset=1, node_type="Decorator",
            original_op="decorator", replacement_op="remove_decorator",
        )
        tree, applied = apply_mutation(source, mutant)
        expected = "\n".join(decorators[:index] + decorators[index + 1:] + tail)
        assert applied
        assert ast.dump(tree) == ast.dump(ast.parse(expected))

    @pytest.mark.parametrize("mismatch", [
        {"lineno": 2}, {"col_offset": 0}, {"node_type": "Call"},
        {"replacement_op": "unknown"},
    ])
    def it_leaves_decorators_unchanged_for_a_nonmatching_mutant(mismatch):
        source = "@wrap\ndef f(): pass\n"
        fields = dict(
            col_offset=1, node_type="Decorator", original_op="decorator",
            replacement_op="remove_decorator",
        )
        fields.update(mismatch)
        tree, applied = apply_mutation(source, _make_mutant(**fields))
        assert not applied
        assert ast.dump(tree) == ast.dump(ast.parse(source))

    @pytest.mark.parametrize("result", [[], [1]])
    def it_negates_one_if_predicate_and_evaluates_it_once(result):
        source = (
            "if check():\n"
            "    if flag: events.append('first')\n"
            "else:\n"
            "    events.append('second')\n"
        )
        mutant = _make_mutant(
            node_type="If", original_op="condition", replacement_op="negate_condition"
        )
        tree, applied = apply_mutation(source, mutant)
        assert applied
        predicate = tree.body[0].test
        assert (predicate.lineno, predicate.col_offset) == (1, 3)
        calls = []
        events = []

        def check():
            calls.append("checked")
            return result

        exec(compile(tree, "test.py", "exec"), dict(check=check, flag=True, events=events))
        assert calls == ["checked"]
        assert events == (["second"] if result else ["first"])

    @pytest.mark.parametrize("mismatch", [
        {"lineno": 2}, {"col_offset": 2}, {"node_type": "Call"}, {"replacement_op": "unknown"},
    ])
    def it_leaves_if_unchanged_for_a_nonmatching_mutant(mismatch):
        source = "if check(): pass\n"
        fields = dict(node_type="If", original_op="condition", replacement_op="negate_condition")
        fields.update(mismatch)
        tree, applied = apply_mutation(source, _make_mutant(**fields))
        assert not applied
        assert ast.dump(tree) == ast.dump(ast.parse(source))

    @pytest.mark.parametrize("result", [[], [1]])
    def it_negates_only_the_selected_assert_test_and_keeps_the_lazy_message(result):
        source = "assert flag\nassert check(), explain()\nassert flag and other\n"
        mutant = _make_mutant(
            lineno=2, node_type="Assert", original_op="condition",
            replacement_op="negate_condition",
        )
        tree, applied = apply_mutation(source, mutant)
        assert applied
        expected = ast.parse(source.replace("assert check()", "assert not check()"))
        assert ast.dump(tree) == ast.dump(expected)
        original = ast.parse(source).body[1].test
        predicate = tree.body[1].test
        assert ast.get_source_segment(source, predicate) == ast.get_source_segment(
            source, original
        ) == "check()"

        calls = []

        def check():
            calls.append("predicate")
            return result

        def explain():
            calls.append("message")
            return "validation failed"

        code = compile(tree, "test.py", "exec")
        namespace = dict(flag=True, other=True, check=check, explain=explain)
        if result:
            with pytest.raises(AssertionError, match="^validation failed$"):
                exec(code, namespace)
            assert calls == ["predicate", "message"]
        else:
            exec(code, namespace)
            assert calls == ["predicate"]

    @pytest.mark.parametrize(("predicate", "mismatch"), [
        ("flag", {"lineno": 2}), ("flag", {"col_offset": 7}),
        ("flag", {"node_type": "If"}), ("flag", {"replacement_op": "unknown"}),
        ("flag and other", {}), ("not flag", {}), ("flag == other", {}),
        ("True", {}), ("obj[0]", {}),
    ])
    def it_leaves_assert_unchanged_for_ineligible_or_nonmatching_mutants(predicate, mismatch):
        source = f"assert {predicate}, message()\n"
        fields = dict(node_type="Assert", original_op="condition", replacement_op="negate_condition")
        fields.update(mismatch)
        tree, applied = apply_mutation(source, _make_mutant(**fields))
        assert not applied
        assert ast.dump(tree) == ast.dump(ast.parse(source))

    def it_still_applies_existing_mutations_inside_the_assert_message():
        source = "assert flag, a + b\n"
        tree, applied = apply_mutation(source, _make_mutant(col_offset=13))
        assert applied
        assert ast.dump(tree) == ast.dump(ast.parse("assert flag, a - b\n"))

    def it_negates_one_while_test_once_per_check_without_rewriting_the_loop():
        source = (
            "while flag: break\n"
            "while check():\n"
            "    events.append('iteration')\n"
            "else:\n"
            "    events.append('done')\n"
        )
        mutant = _make_mutant(
            lineno=2, node_type="While", original_op="condition",
            replacement_op="negate_condition",
        )
        tree, applied = apply_mutation(source, mutant)
        assert applied
        expected = ast.parse(source.replace("while check()", "while not check()"))
        assert ast.dump(tree) == ast.dump(expected)
        assert ast.get_source_segment(source, tree.body[1].test) == "check()"
        values = iter(([], [], [1]))
        events = []

        def check():
            events.append("check")
            return next(values)

        exec(compile(tree, "test.py", "exec"), dict(flag=True, check=check, events=events))
        assert events == ["check", "iteration", "check", "iteration", "check", "done"]

    @pytest.mark.parametrize(("predicate", "mismatch"), [
        ("flag", {"lineno": 2}), ("flag", {"col_offset": 6}),
        ("flag", {"node_type": "Assert"}), ("flag", {"replacement_op": "unknown"}),
        ("flag and other", {}), ("not flag", {}), ("flag == other", {}),
        ("True", {}), ("obj[0]", {}),
    ])
    def it_leaves_while_unchanged_for_ineligible_or_nonmatching_mutants(predicate, mismatch):
        source = f"while {predicate}: break\n"
        fields = dict(node_type="While", original_op="condition", replacement_op="negate_condition")
        fields.update(mismatch)
        tree, applied = apply_mutation(source, _make_mutant(**fields))
        assert not applied
        assert ast.dump(tree) == ast.dump(ast.parse(source))

    def it_still_applies_existing_mutations_inside_a_while_test():
        source = "while check(a == b): break\n"
        mutant = _make_mutant(
            col_offset=12, node_type="Compare", original_op="Eq", replacement_op="NotEq",
        )
        tree, applied = apply_mutation(source, mutant)
        assert applied
        assert ast.dump(tree) == ast.dump(ast.parse("while check(a != b): break\n"))

    def it_applies_binop_mutation():
        source = "x + y"
        tree = ast.parse(source, mode="eval")
        # The BinOp is at line 1, col 0
        mutant = _make_mutant(
            lineno=1,
            col_offset=0,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
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
            lineno=1,
            col_offset=0,
            node_type="Compare",
            original_op="Gt",
            replacement_op="LtE",
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
            lineno=1,
            col_offset=0,
            node_type="BoolOp",
            original_op="And",
            replacement_op="Or",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="True",
            replacement_op="False",
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
                lineno=1,
                col_offset=0,
                node_type="BinOp",
                original_op="BitAnd",
                replacement_op="BitOr",
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
                lineno=1,
                col_offset=0,
                node_type="BinOp",
                original_op="BitAnd",
                replacement_op="BitXor",
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
                lineno=1,
                col_offset=0,
                node_type="BinOp",
                original_op="BitOr",
                replacement_op="BitAnd",
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
                lineno=1,
                col_offset=0,
                node_type="BinOp",
                original_op="BitOr",
                replacement_op="BitXor",
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
                lineno=1,
                col_offset=0,
                node_type="BinOp",
                original_op="BitXor",
                replacement_op="BitAnd",
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
                lineno=1,
                col_offset=0,
                node_type="BinOp",
                original_op="BitXor",
                replacement_op="BitOr",
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
                lineno=1,
                col_offset=0,
                node_type="BinOp",
                original_op="LShift",
                replacement_op="RShift",
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
                lineno=1,
                col_offset=0,
                node_type="BinOp",
                original_op="RShift",
                replacement_op="LShift",
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
                lineno=1,
                col_offset=0,
                node_type="AugAssign",
                original_op="Add",
                replacement_op="Sub",
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
                lineno=1,
                col_offset=0,
                node_type="AugAssign",
                original_op="Sub",
                replacement_op="Add",
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
                lineno=1,
                col_offset=0,
                node_type="AugAssign",
                original_op="Mult",
                replacement_op="FloorDiv",
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
                lineno=1,
                col_offset=0,
                node_type="AugAssign",
                original_op="BitAnd",
                replacement_op="BitOr",
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
                lineno=1,
                col_offset=0,
                node_type="AugAssign",
                original_op="LShift",
                replacement_op="RShift",
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
                lineno=1,
                col_offset=0,
                node_type="AugAssign",
                original_op="Add",
                replacement_op="Sub",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body.op, ast.Add)

        def it_does_not_apply_binop_mutant_to_augassign():
            source = "x += y\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=1,
                col_offset=0,
                node_type="BinOp",
                original_op="Add",
                replacement_op="Sub",
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
                lineno=1,
                col_offset=0,
                node_type="IfExp",
                original_op="ternary",
                replacement_op="swap_branches",
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
                lineno=1,
                col_offset=0,
                node_type="IfExp",
                original_op="ternary",
                replacement_op="always_true",
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
                lineno=1,
                col_offset=0,
                node_type="IfExp",
                original_op="ternary",
                replacement_op="always_false",
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
                lineno=1,
                col_offset=0,
                node_type="IfExp",
                original_op="ternary",
                replacement_op="swap_branches",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False

        def it_does_not_apply_binop_mutant_to_ifexp():
            source = "x if cond else y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1,
                col_offset=0,
                node_type="BinOp",
                original_op="Add",
                replacement_op="Sub",
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
                lineno=1,
                col_offset=0,
                node_type="BoolOp",
                original_op="And",
                replacement_op="swap_branches",
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
                lineno=2,
                col_offset=4,
                node_type="Break",
                original_op="break",
                replacement_op="continue",
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
                lineno=2,
                col_offset=4,
                node_type="Continue",
                original_op="continue",
                replacement_op="break",
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
                lineno=2,
                col_offset=4,
                node_type="Break",
                original_op="break",
                replacement_op="continue",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body[0].body[0], ast.Continue)

        def it_does_not_apply_continue_mutant_to_break():
            source = "for i in range(10):\n    break\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=2,
                col_offset=4,
                node_type="Continue",
                original_op="continue",
                replacement_op="break",
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
                lineno=2,
                col_offset=4,
                node_type="BinOp",
                original_op="Add",
                replacement_op="continue",
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
                lineno=2,
                col_offset=4,
                node_type="BinOp",
                original_op="Add",
                replacement_op="break",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body[0].body[0], ast.Continue)

    def describe_except_handler():
        def it_broadens_typed_except_to_exception():
            source = "try:\n    pass\nexcept ValueError:\n    pass\n"
            tree = ast.parse(source)
            # ExceptHandler is at line 3, col 0
            mutant = _make_mutant(
                lineno=3,
                col_offset=0,
                node_type="ExceptHandler",
                original_op="typed",
                replacement_op="broaden",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            handler = new_tree.body[0].handlers[0]
            assert isinstance(handler.type, ast.Name)
            assert handler.type.id == "Exception"

        def it_replaces_body_with_raise():
            source = "try:\n    pass\nexcept ValueError:\n    print('error')\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=3,
                col_offset=0,
                node_type="ExceptHandler",
                original_op="typed",
                replacement_op="body_to_raise",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            handler = new_tree.body[0].handlers[0]
            assert len(handler.body) == 1
            assert isinstance(handler.body[0], ast.Raise)

        def it_replaces_bare_except_body_with_raise():
            source = "try:\n    pass\nexcept:\n    print('error')\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=3,
                col_offset=0,
                node_type="ExceptHandler",
                original_op="bare",
                replacement_op="body_to_raise",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            handler = new_tree.body[0].handlers[0]
            assert len(handler.body) == 1
            assert isinstance(handler.body[0], ast.Raise)

        def it_does_not_broaden_bare_except():
            """Bare except has no type to broaden — broaden should not apply."""
            source = "try:\n    pass\nexcept:\n    pass\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=3,
                col_offset=0,
                node_type="ExceptHandler",
                original_op="bare",
                replacement_op="broaden",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False

        def it_does_not_apply_except_mutant_to_other_node():
            """ExceptHandler mutant should not affect a BinOp."""
            source = "x + y"
            tree = ast.parse(source, mode="eval")
            mutant = _make_mutant(
                lineno=1,
                col_offset=0,
                node_type="ExceptHandler",
                original_op="typed",
                replacement_op="broaden",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            assert isinstance(new_tree.body.op, ast.Add)

        def it_does_not_apply_non_except_mutant_to_except_handler():
            """Kills and→or on visit_ExceptHandler guard: _matches True but wrong node_type."""
            source = "try:\n    pass\nexcept ValueError:\n    pass\n"
            tree = ast.parse(source)
            # Position matches the ExceptHandler, but node_type says BinOp
            mutant = _make_mutant(
                lineno=3,
                col_offset=0,
                node_type="BinOp",
                original_op="Add",
                replacement_op="broaden",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            assert applier.applied is False
            handler = new_tree.body[0].handlers[0]
            # Handler type must remain ValueError, not broadened
            assert isinstance(handler.type, ast.Name)
            assert handler.type.id == "ValueError"

        def it_preserves_exception_name_binding():
            """Broadening should preserve 'as e' binding."""
            source = "try:\n    pass\nexcept ValueError as e:\n    print(e)\n"
            tree = ast.parse(source)
            mutant = _make_mutant(
                lineno=3,
                col_offset=0,
                node_type="ExceptHandler",
                original_op="typed",
                replacement_op="broaden",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            handler = new_tree.body[0].handlers[0]
            assert handler.name == "e"
            assert isinstance(handler.type, ast.Name)
            assert handler.type.id == "Exception"

        def it_copies_location_from_original_type_not_handler():
            """Broaden should copy location from the original exception type node."""
            source = "try:\n    pass\nexcept ValueError:\n    pass\n"
            tree = ast.parse(source)
            handler = tree.body[0].handlers[0]
            original_type_col = handler.type.col_offset
            mutant = _make_mutant(
                lineno=3,
                col_offset=0,
                node_type="ExceptHandler",
                original_op="typed",
                replacement_op="broaden",
            )
            applier = MutantApplier(mutant)
            new_tree = applier.visit(tree)
            ast.fix_missing_locations(new_tree)
            assert applier.applied is True
            new_handler = new_tree.body[0].handlers[0]
            # Exception name should have col_offset of original ValueError, not handler col 0
            assert new_handler.type.col_offset == original_type_col

    def it_does_not_apply_when_location_mismatches():
        source = "x + y"
        tree = ast.parse(source, mode="eval")
        mutant = _make_mutant(
            lineno=99,
            col_offset=0,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )
        applier = MutantApplier(mutant)
        applier.visit(tree)
        assert applier.applied is False


def describe_apply_mutation():
    def it_returns_modified_tree_and_applied_flag():
        source = "x + y"
        mutant = _make_mutant(
            lineno=1,
            col_offset=0,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
        )
        tree, applied = apply_mutation(source, mutant)
        assert applied is True


def describe_install_and_remove_hook():
    def it_installs_and_removes_hook():
        source = "x = 1\n"
        point = _make_point(
            lineno=1, col_offset=0, node_type="BinOp", original_op="Add"
        )
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
        point = _make_point(
            lineno=1, col_offset=0, node_type="BinOp", original_op="Add"
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)

        target_modules = {"__test_dummy_module2__": source}
        finder = install_hook(target_modules, mutant)
        remove_hook(finder)
        # Should not raise
        remove_hook(finder)
        assert finder not in sys.meta_path

    def it_propagates_non_valueerror_from_remove():
        """Non-ValueError exceptions in remove_hook must propagate.

        Kills broaden mutation on except ValueError: pass (line 265).
        """
        import pytest

        source = "x = 1\n"
        point = _make_point(
            lineno=1, col_offset=0, node_type="BinOp", original_op="Add"
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)

        target_modules = {"__test_dummy_module3__": source}
        finder = install_hook(target_modules, mutant)

        class BadRemoveList(list):
            def remove(self, item):
                raise RuntimeError("unexpected error in meta_path")

        original_meta_path = sys.meta_path
        sys.meta_path = BadRemoveList(original_meta_path)
        try:
            with pytest.raises(RuntimeError, match="unexpected error"):
                remove_hook(finder)
        finally:
            sys.meta_path = original_meta_path
            # Clean up the finder from the original list
            try:
                sys.meta_path.remove(finder)
            except ValueError:
                pass


def describe_UnaryOp_mutation():
    def it_removes_unary_op_with_remove_replacement():
        """UnaryOp with _remove strips the operator, returning the operand."""
        source = "not x"
        tree = ast.parse(source, mode="eval")
        mutant = _make_mutant(
            lineno=1,
            col_offset=0,
            node_type="UnaryOp",
            original_op="Not",
            replacement_op="_remove",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="value",
            replacement_op="negate",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="42",
            replacement_op="empty_str",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="expr",
            replacement_op="negate_expr",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="negation",
            replacement_op="remove_negation",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="None",
            replacement_op="expr",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="int_literal",
            replacement_op="expr",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="None",
            replacement_op="expr",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="None",
            replacement_op="False",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="None",
            replacement_op="negate_expr",
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
            lineno=2,
            col_offset=4,
            node_type="Return",
            original_op="expr",
            replacement_op="remove_negation",
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
            lineno=1,
            col_offset=0,
            node_type="Compare",
            original_op="Gt",
            replacement_op="LtE",
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
            lineno=1,
            col_offset=0,
            node_type="BoolOp",
            original_op="And",
            replacement_op="Or",
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
            lineno=1,
            col_offset=0,
            node_type="UnaryOp",
            original_op="Not",
            replacement_op="_remove",
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
            lineno=1,
            col_offset=0,
            node_type="BinOp",
            original_op="And",
            replacement_op="Or",
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
            lineno=2,
            col_offset=binop_node.col_offset,
            node_type="Return",
            original_op="expr",
            replacement_op="None",
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
            lineno=1,
            col_offset=0,
            node_type="BinOp",
            original_op="Add",
            replacement_op="Sub",
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
            lineno=1,
            col_offset=0,
            node_type="Compare",
            original_op="Gt",
            replacement_op="LtE",
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
            lineno=1,
            col_offset=0,
            node_type="UnaryOp",
            original_op="USub",
            replacement_op="TotallyFake",
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

    def it_mutates_only_the_mutants_own_module_at_shared_coordinates():
        point = MutationPoint(
            file_path="leela_same_coord_a.py",
            module_name="leela_same_coord_a",
            lineno=1,
            col_offset=8,
            node_type="BinOp",
            original_op="Add",
            inferred_type=None,
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
        finder = install_hook(
            {
                "leela_same_coord_a": "VALUE = 2 + 3\n",
                "leela_same_coord_b": "VALUE = 20 + 30\n",
            },
            mutant,
        )
        try:
            mutated = importlib.import_module("leela_same_coord_a")
            other = importlib.import_module("leela_same_coord_b")
        finally:
            remove_hook(finder)
            sys.modules.pop("leela_same_coord_a", None)
            sys.modules.pop("leela_same_coord_b", None)
        assert mutated.VALUE == -1
        assert other.VALUE == 50
        assert other.__file__ == "<mutated:leela_same_coord_b>"
        assert finder.import_errors == []

    @pytest.mark.parametrize(
        "same_source, expected", [(False, 50), (True, -10)], ids=["shadowed", "alias"]
    )
    def it_checks_file_identity_when_module_names_match(tmp_path, same_source, expected):
        from pytest_leela.import_hook import MutatingFinder

        loaded_path = tmp_path / "active" / "test.py"
        loaded_path.parent.mkdir()
        loaded_path.write_text("VALUE = 20 + 30\n")
        point_path = tmp_path / "test.py"
        if same_source:
            # Exercise realpath without requiring permission to create symlinks.
            point_path = loaded_path.parent / ".." / "active" / "test.py"
        else:
            point_path.write_text("VALUE = 2 + 3\n")
        point = replace(_make_point(col_offset=8), file_path=str(point_path))
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
        finder = MutatingFinder({point.module_name: loaded_path.read_text()}, mutant)
        finder.set_file_paths({point.module_name: str(loaded_path)})

        spec = finder.find_spec(point.module_name)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.VALUE == expected
        assert module.__file__ == str(loaded_path)

    def it_does_not_apply_a_mutant_to_a_different_name_for_the_same_file(tmp_path):
        from pytest_leela.import_hook import MutatingFinder

        source = "VALUE = 20 + 30\n"
        source_path = tmp_path / "active" / "subject.py"
        source_path.parent.mkdir()
        source_path.write_text(source)
        alias_path = source_path.parent / ".." / "active" / "subject.py"
        assert alias_path != source_path
        assert alias_path.resolve() == source_path.resolve()

        owner, other = "leela_toy.subject", "leela_alias.subject"
        point = replace(
            _make_point(col_offset=8), file_path=str(source_path), module_name=owner
        )
        mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
        finder = MutatingFinder({owner: source, other: source}, mutant)
        finder.set_file_paths({owner: str(source_path), other: str(alias_path)})

        def load(name):
            spec = finder.find_spec(name)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        assert load(owner).VALUE == -10
        assert load(other).VALUE == 50


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

    def describe_module_attribute_population():
        """exec_module must populate __file__, __loader__, __spec__ before exec.

        Regression for pith-task 0b048dd4: without these attrs, mutated
        imports of any module that references __file__ at module scope raise
        NameError during exec, and the mutant is mis-reported as SURVIVED.
        """

        def _exec_into_fresh_module(
            source: str, mutant: Mutant, filename: str, name: str
        ) -> types.ModuleType:
            from pytest_leela.import_hook import MutatingLoader

            loader = MutatingLoader(source, mutant, filename)
            spec = importlib.machinery.ModuleSpec(name, loader, origin=filename)
            module = types.ModuleType(name)
            module.__spec__ = spec
            loader.exec_module(module)
            return module

        def it_sets_module_file_to_loader_filename():
            point = _make_point(lineno=1, col_offset=0, node_type="BinOp")
            mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
            module = _exec_into_fresh_module(
                "x = 1\n", mutant, "/abs/path/foo.py", "leela_test_attr_file"
            )
            assert module.__file__ == "/abs/path/foo.py"

        def it_sets_module_loader_to_self():
            from pytest_leela.import_hook import MutatingLoader

            point = _make_point(lineno=1, col_offset=0, node_type="BinOp")
            mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
            loader = MutatingLoader("x = 1\n", mutant, "/abs/path/bar.py")
            spec = importlib.machinery.ModuleSpec(
                "leela_test_attr_loader", loader, origin="/abs/path/bar.py"
            )
            module = types.ModuleType("leela_test_attr_loader")
            module.__spec__ = spec
            loader.exec_module(module)
            assert module.__loader__ is loader

        def it_creates_spec_when_module_has_none():
            """If a caller invokes exec_module without setting __spec__,
            we populate it for parity with SourceFileLoader."""
            from pytest_leela.import_hook import MutatingLoader

            point = _make_point(lineno=1, col_offset=0, node_type="BinOp")
            mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
            loader = MutatingLoader("x = 1\n", mutant, "/abs/path/baz.py")
            module = types.ModuleType("leela_test_attr_spec")
            assert getattr(module, "__spec__", None) is None
            loader.exec_module(module)
            assert module.__spec__ is not None
            assert module.__spec__.origin == "/abs/path/baz.py"
            assert module.__spec__.loader is loader

        def it_preserves_existing_spec_when_already_populated():
            """Don't overwrite a __spec__ already attached by the import system."""
            from pytest_leela.import_hook import MutatingLoader

            point = _make_point(lineno=1, col_offset=0, node_type="BinOp")
            mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
            loader = MutatingLoader("x = 1\n", mutant, "/abs/path/qux.py")
            existing_spec = importlib.machinery.ModuleSpec(
                "leela_test_attr_keep_spec", loader, origin="/abs/path/qux.py"
            )
            module = types.ModuleType("leela_test_attr_keep_spec")
            module.__spec__ = existing_spec
            loader.exec_module(module)
            assert module.__spec__ is existing_spec

        def it_allows_module_scope_dunder_file_reference():
            """Direct regression: a module that touches __file__ during
            top-level exec must import successfully under MutatingLoader.

            Pre-fix this raised NameError: name '__file__' is not defined."""
            source = (
                "from pathlib import Path\n"
                "BASE = Path(__file__).resolve().parent\n"
                "FILE_NAME = Path(__file__).name\n"
            )
            point = _make_point(lineno=2, col_offset=7, node_type="BinOp")
            mutant = Mutant(point=point, replacement_op="Sub", mutant_id=0)
            module = _exec_into_fresh_module(
                source,
                mutant,
                "/abs/path/uses_file.py",
                "leela_test_uses_file",
            )
            from pathlib import Path as _Path

            assert module.BASE == _Path("/abs/path/uses_file.py").resolve().parent
            assert module.FILE_NAME == "uses_file.py"


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


def describe_import_error_recording():
    """MutatingLoader reports exceptions raised by the mutated source."""

    def _exec(source, import_errors=None):
        from pytest_leela.import_hook import MutatingLoader

        loader = MutatingLoader(source, _make_mutant(), "rec.py", import_errors)
        module = types.ModuleType("rec_mod")
        loader.exec_module(module)
        return loader

    def it_records_and_reraises_an_exception_from_the_module_body():
        sink: list[str] = []
        try:
            _exec("raise ValueError('boom')\n", sink)
        except ValueError:
            pass
        else:
            raise AssertionError("exec_module swallowed the exception")
        assert sink == ["rec_mod: ValueError: boom"]

    def it_records_nothing_for_a_module_that_imports_cleanly():
        sink: list[str] = []
        _exec("x = 1\n", sink)
        assert sink == []

    def it_owns_a_fresh_list_when_none_is_given():
        loader = _exec("x = 1\n")
        assert loader.import_errors == []

    def it_shares_the_finders_list_with_every_loader_it_creates():
        from pytest_leela.import_hook import MutatingFinder

        finder = MutatingFinder({"rec_target": "x = 1\n"}, _make_mutant())
        spec = finder.find_spec("rec_target")
        assert spec.loader.import_errors is finder.import_errors
