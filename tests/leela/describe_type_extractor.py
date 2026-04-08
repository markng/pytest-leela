"""Tests for pytest_leela.type_extractor — type annotation enrichment."""

import ast
from unittest.mock import patch

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.models import MutationPoint
from pytest_leela.type_extractor import (
    _find_enclosing_func,
    _infer_assigned_value_type,
    _operand_from_env_not_param,
    _type_came_from_dataflow,
    _type_of_value_node,
    enrich_mutation_points,
)


def describe_enrich_mutation_points():
    def it_infers_int_type_from_parameter_annotation():
        source = "def f(x: int, y: int) -> int:\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type == "int"

    def it_infers_str_type_from_parameter_annotation():
        source = "def f(a: str, b: str) -> str:\n    return a + b\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type == "str"

    def it_infers_return_type_for_return_nodes():
        source = "def f() -> bool:\n    return True\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        returns = [p for p in enriched if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].inferred_type == "bool"

    def it_handles_optional_return_types():
        source = "from typing import Optional\ndef f(x: int) -> Optional[int]:\n    if x > 0:\n        return x\n    return None\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        returns = [p for p in enriched if p.node_type == "Return"]
        for r in returns:
            assert r.inferred_type == "Optional[int]"

    def it_leaves_unannotated_as_none():
        source = "def f(x, y):\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type is None

    def it_infers_float_type_from_annotation():
        source = "def f(x: float, y: float) -> float:\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type == "float"

    def it_infers_bool_for_boolop():
        source = "def f(a: bool, b: bool) -> bool:\n    return a and b\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        boolops = [p for p in enriched if p.node_type == "BoolOp"]
        assert len(boolops) >= 1
        assert boolops[0].inferred_type == "bool"

    def it_returns_empty_list_unchanged():
        enriched, _ = enrich_mutation_points("def f(): pass\n", [])
        assert enriched == []

    def it_infers_type_from_constant_operand():
        source = "def f(x):\n    return x + 1\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type == "int"

    # --- Group 1: _annotation_to_str branches (L15-42) ---

    def describe_annotation_to_str():
        def it_resolves_string_forward_ref_annotations():
            """L15: str constant annotation (forward reference like 'int')."""
            source = 'def f(x: "int") -> "bool":\n    return x > 0\n'
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type == "int"
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "bool"

        def it_handles_none_constant_annotation():
            """L16: non-str constant annotation (None -> 'None')."""
            source = "def f(x: None, y: int) -> int:\n    return x + y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # x has annotation None -> _annotation_to_str returns str(None) = "None"
            # BinOp checks left (x -> "None") first
            assert binops[0].inferred_type == "None"

        def it_resolves_dotted_attribute_annotations():
            """L21-22: Attribute annotation like typing.Optional."""
            source = "import typing\ndef f() -> typing.Optional:\n    return None\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "typing.Optional"

        def it_returns_none_for_unresolvable_attribute_base():
            """L23: Attribute with base that _annotation_to_str can't resolve."""
            source = "def f(x: foo().bar, y: int) -> int:\n    return x + y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # x annotation unresolvable (foo().bar -> None), y is "int"
            # BinOp checks left (x not in param_types), then right (y -> "int")
            assert binops[0].inferred_type == "int"

        def it_resolves_optional_subscript_with_valid_inner():
            """L26-29: Optional[T] with resolvable inner type."""
            source = "from typing import Optional\ndef f(x: Optional[str]) -> Optional[int]:\n    return len(x)\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "Optional[int]"

        def it_returns_base_for_optional_with_unresolvable_inner():
            """L28 false path: Optional[<unresolvable>] returns just 'Optional'."""
            source = "from typing import Optional\ndef f() -> Optional[foo().bar]:\n    return None\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            # inner is unresolvable -> falls through to return base ("Optional")
            assert returns[0].inferred_type == "Optional"

        def it_resolves_list_subscript_annotation():
            """list[T] -> 'list'."""
            source = "def f(x: list[int]) -> list[int]:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "list"

        def it_resolves_dict_subscript_annotation():
            """dict[K, V] -> 'dict'."""
            source = "def f(x: dict[str, int]) -> dict[str, int]:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "dict"

        def it_resolves_set_subscript_annotation():
            """set[T] -> 'set'."""
            source = "def f(x: set[int]) -> set[int]:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "set"

        def it_resolves_tuple_subscript_annotation():
            """tuple[T, ...] -> 'tuple'."""
            source = "def f(x: tuple[int, str]) -> tuple[int, str]:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "tuple"

        def it_resolves_other_subscript_annotation():
            """L32: Subscript with non-container base returns base name."""
            source = "from typing import Sequence\ndef f() -> Sequence[int]:\n    return []\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "Sequence"

        def it_resolves_pipe_none_union_as_optional():
            """L33, L37-38: X | None -> Optional[X]."""
            source = "def f(x: int | None) -> int | None:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "Optional[int]"

        def it_resolves_none_pipe_type_as_optional():
            """L39-40: None | X -> Optional[X]."""
            source = "def f(x: None | str) -> None | str:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "Optional[str]"

        def it_returns_none_for_non_none_union():
            """L41-42: X | Y (neither None) -> None."""
            source = "def f(x: int | str) -> int | str:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type is None

        def it_returns_none_for_union_with_unresolvable_sides():
            """L41: BitOr with left=None (unresolvable) -> None."""
            source = "def f(x: foo() | bar()) -> int:\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            # Filter to the Add BinOp on line 2 (not the BitOr in the annotation)
            add_binops = [
                p for p in enriched if p.node_type == "BinOp" and p.original_op == "Add"
            ]
            assert len(add_binops) == 1
            # x annotation unresolvable, but right operand 1 is int
            assert add_binops[0].inferred_type == "int"

        def it_returns_none_for_unsupported_annotation_node():
            """L42: final return None for unhandled AST node types (e.g. Set)."""
            source = "def f(x: {1, 2}, y: int) -> int:\n    return x + y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # x annotation is a Set literal — not handled -> None
            # BinOp checks left (x not in param_types), then right (y -> "int")
            assert binops[0].inferred_type == "int"

    # --- Group 2: _infer_expr_type branches (L133-143) ---

    def describe_infer_expr_type():
        def it_does_not_treat_bool_as_int():
            """L133: bool literal not classified as int (bool is subclass of int)."""
            source = "def f(x):\n    return x + True\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # True is bool, not int — must return "bool" not "int"
            assert binops[0].inferred_type == "bool"

        def it_infers_float_from_literal():
            """L135-136: float constant in BinOp."""
            source = "def f(x):\n    return x + 1.5\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "float"

        def it_infers_str_from_literal():
            """L137-138: str constant in BinOp."""
            source = 'def f(x):\n    return x + "hello"\n'
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "str"

        def it_infers_bool_from_bool_literal():
            """L139-140: bool constant returns 'bool'."""
            source = "def f(x):\n    return x + False\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "bool"

        def it_infers_int_from_len_call():
            """L141-143: len() call returns 'int'."""
            source = "def f(x, y):\n    return len(x) + y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "int"

        def it_does_not_infer_type_for_non_len_calls():
            """L142: non-len Call returns None."""
            source = "def f(x, y):\n    return abs(x) + y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # abs() is not len(), neither operand has type info
            assert binops[0].inferred_type is None

    # --- Group 3: _infer_compare_type (L152-158) ---

    def describe_infer_compare_type():
        def it_infers_type_from_left_operand():
            """L152-153: left operand has annotation."""
            source = "def f(x: int) -> bool:\n    return x > 0\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type == "int"

        def it_infers_type_from_comparator():
            """L156-157: only comparator is typed (int literal)."""
            source = "def f(x):\n    return x > 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type == "int"

        def it_returns_none_when_neither_side_typed():
            """L158: neither left nor comparators have type info."""
            source = "def f(x, y):\n    return x > y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type is None

        def it_infers_type_from_float_comparator():
            """L156-157: comparator is a float literal."""
            source = "def f(x):\n    return x > 1.5\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type == "float"

    # --- Group 4: Edge cases ---

    def describe_edge_cases():
        def it_handles_missing_end_lineno(monkeypatch):
            """L72: fallback ``end_lineno = lineno + 100`` when end_lineno is falsy.

            Kills the ``+ → *`` mutant: the function starts at line 1 and
            the BinOp is at line 101.  With ``+ 100`` the range is [1, 101]
            which includes line 101.  With ``* 100`` the range is [1, 100]
            which does NOT include line 101, so the BinOp would not be
            enriched and the assertion would fail.
            """
            # 101-line function: def header (line 1) + 99 pass lines + BinOp at line 101
            source = (
                "def f(x: int) -> int:\n" + "    pass\n" * 99 + "    return x + 1\n"
            )
            points = find_mutation_points(source, "test.py", "test")

            original_parse = ast.parse

            def nullify_end_lineno(src, *args, **kwargs):
                tree = original_parse(src, *args, **kwargs)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        node.end_lineno = 0
                return tree

            monkeypatch.setattr(ast, "parse", nullify_end_lineno)
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # With + 100: end_line = 1 + 100 = 101, BinOp at line 101 is within [1, 101]
            # With * 100: end_line = 1 * 100 = 100, BinOp at line 101 is NOT within [1, 100]
            assert binops[0].inferred_type == "int"

        def it_leaves_module_level_binop_unenriched():
            """L112: _find_enclosing_func returns None for module-level code."""
            source = "x = 1 + 2\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type is None

        def it_uses_innermost_function_for_nested_definitions():
            """_find_enclosing_func returns the innermost function for nested defs.

            The inner function's parameter annotation (x: int) must be used, not
            the outer function's (which has no parameters).  If the first-match
            heuristic were used, outer would be returned and x would be unannotated.
            """
            source = (
                "def outer():\n"
                "    def inner(x: int):\n"
                "        return x + 1\n"
                "    return 0\n"
            )
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            # BinOp x + 1 is in inner's body; must get type from inner's annotation
            inner_binops = [
                p for p in enriched if p.node_type == "BinOp" and p.lineno == 3
            ]
            assert len(inner_binops) == 1
            assert inner_binops[0].inferred_type == "int"

        def it_returns_none_from_find_node_at_when_no_match():
            """L170: _find_node_at returns None (not some other value) when no node matches."""
            from pytest_leela.type_extractor import _find_node_at

            tree = ast.parse("x = 1\n")
            result = _find_node_at(tree, 999, 0, "BinOp")
            assert result is None

        def it_leaves_type_none_when_binop_node_not_found():
            """L170: _find_node_at returns None for BinOp with wrong col_offset."""
            source = "def f(x: int) -> int:\n    return x + 1\n"
            bad_point = MutationPoint(
                file_path="test.py",
                module_name="test",
                lineno=2,
                col_offset=999,
                node_type="BinOp",
                original_op="Add",
                inferred_type=None,
            )
            enriched, _ = enrich_mutation_points(source, [bad_point])
            assert enriched[0].inferred_type is None

        def it_leaves_type_none_when_compare_node_not_found():
            """L170: _find_node_at returns None for Compare with wrong col_offset."""
            source = "def f(x: int) -> bool:\n    return x > 0\n"
            bad_point = MutationPoint(
                file_path="test.py",
                module_name="test",
                lineno=2,
                col_offset=999,
                node_type="Compare",
                original_op="Gt",
                inferred_type=None,
            )
            enriched, _ = enrich_mutation_points(source, [bad_point])
            assert enriched[0].inferred_type is None

        def it_leaves_type_none_when_unaryop_node_not_found():
            """L170: _find_node_at returns None for UnaryOp with wrong col_offset."""
            source = "def f(x: int) -> int:\n    return -x\n"
            bad_point = MutationPoint(
                file_path="test.py",
                module_name="test",
                lineno=2,
                col_offset=999,
                node_type="UnaryOp",
                original_op="USub",
                inferred_type=None,
            )
            enriched, _ = enrich_mutation_points(source, [bad_point])
            assert enriched[0].inferred_type is None

    # --- Enrichment dispatch (L195-214) ---

    def describe_enrichment_dispatch():
        def it_infers_type_for_unary_op():
            """L211-214: UnaryOp node type gets enriched from operand type."""
            source = "def f(x: int) -> int:\n    return -x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            unaryops = [p for p in enriched if p.node_type == "UnaryOp"]
            assert len(unaryops) >= 1
            assert unaryops[0].inferred_type == "int"

        def it_enriches_correct_type_for_known_annotated_binop():
            """L198-201: BinOp enrichment produces correct type."""
            source = "def f(x: float) -> float:\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # Left operand x has type "float", should take precedence over right (int)
            assert binops[0].inferred_type == "float"

        def it_enriches_compare_node_type():
            """L203-206: Compare enrichment through node lookup."""
            source = "def f(x: str) -> bool:\n    return x > 'a'\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type == "str"

        def it_handles_async_function_annotations():
            """Async functions are processed the same as sync functions."""
            source = "async def f(x: int) -> int:\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "int"

        def it_handles_unknown_node_type_gracefully():
            """Mutation point with unrecognized node_type is passed through."""
            source = "def f(x: int) -> int:\n    return x + 1\n"
            unknown_point = MutationPoint(
                file_path="test.py",
                module_name="test",
                lineno=2,
                col_offset=0,
                node_type="SomeUnknownType",
                original_op="Unknown",
                inferred_type=None,
            )
            enriched, _ = enrich_mutation_points(source, [unknown_point])
            assert enriched[0].inferred_type is None

    def describe_augmented_assignment():
        def it_infers_type_from_target_annotation():
            source = "def f(x: int) -> None:\n    x += 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            augassigns = [p for p in enriched if p.node_type == "AugAssign"]
            assert len(augassigns) == 1
            assert augassigns[0].inferred_type == "int"

        def it_infers_type_from_value_when_target_untyped():
            source = "def f(x) -> None:\n    x += 1.5\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            augassigns = [p for p in enriched if p.node_type == "AugAssign"]
            assert len(augassigns) == 1
            assert augassigns[0].inferred_type == "float"

        def it_leaves_type_none_when_both_untyped():
            source = "def f(x) -> None:\n    x += y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            augassigns = [p for p in enriched if p.node_type == "AugAssign"]
            assert len(augassigns) == 1
            assert augassigns[0].inferred_type is None

        def it_leaves_type_none_when_node_not_found():
            source = "def f(x: int) -> None:\n    x += 1\n"
            bad_point = MutationPoint(
                file_path="test.py",
                module_name="test",
                lineno=2,
                col_offset=999,
                node_type="AugAssign",
                original_op="Add",
                inferred_type=None,
            )
            enriched, _ = enrich_mutation_points(source, [bad_point])
            assert enriched[0].inferred_type is None

        def it_infers_str_type_from_target():
            source = "def f(s: str) -> None:\n    s += 'world'\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            augassigns = [p for p in enriched if p.node_type == "AugAssign"]
            assert len(augassigns) == 1
            assert augassigns[0].inferred_type == "str"

        def it_does_not_fall_through_to_value_when_target_is_conflicted():
            """x += 1 where x is conflicted (env[x]=None) should give None, not int.

            The target x has a known-but-unknown type in the env.  We must not
            fall through to infer from the value (1 → int), which would silently
            produce an incorrect type.
            """
            source = 'def f():\n    x = 1\n    x = "hello"\n    x += 1\n'
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            augassigns = [p for p in enriched if p.node_type == "AugAssign"]
            assert len(augassigns) == 1
            assert augassigns[0].inferred_type is None

    # --- Group 6: Assignment-based forward propagation ---

    def describe_assignment_dataflow():
        """Level-1 assignment-based dataflow: type inference from local assignments."""

        def it_infers_int_from_int_literal_assignment():
            """x = 5 before a BinOp using x should resolve x to int."""
            source = "def f():\n    x = 5\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "int"

        def it_infers_float_from_float_literal_assignment():
            """x = 1.5 should propagate float type."""
            source = "def f():\n    x = 1.5\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "float"

        def it_infers_str_from_str_literal_assignment():
            """x = 'hello' should propagate str type."""
            source = "def f():\n    x = \"hello\"\n    return x + 'world'\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "str"

        def it_infers_type_from_annotated_assignment():
            """x: int = ... should use the annotation, not the value."""
            source = "def f():\n    x: int = compute()\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "int"

        def it_propagates_type_from_param_through_variable():
            """y = x where x: int should propagate int to y."""
            source = "def f(x: int):\n    y = x\n    return y + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "int"

        def it_resolves_unknown_when_conflicting_assignments():
            """If x is assigned int then str, x has unknown type and x + 1 is also unknown.

            x is in the env with type None (conflicted).  When enriching the BinOp
            x + 1, the left operand (x) is a known variable whose type is None, so
            we do NOT fall through to infer from the right operand (1 → int).
            Propagating the literal's type would silently ignore the conflict.
            """
            source = 'def f():\n    x = 1\n    x = "hello"\n    return x + 1\n'
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # Conflicting assignments → x type is unknown (None); BinOp type is also None.
            assert binops[0].inferred_type is None

        def it_infers_from_right_when_left_is_genuinely_unresolvable():
            """When the left operand is not in env or params, fall through to right.

            x is not in env and not annotated (unannotated param), so x + 1 should
            resolve to int from the literal.  This contrasts with the conflicted-env
            case where we must NOT fall through.
            """
            source = "def f(x):\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # x has no annotation, not in env → left is unresolvable → fall through to 1 → int
            assert binops[0].inferred_type == "int"

        def it_resolves_compare_from_assigned_variable():
            """Assignment env should flow through Compare inference."""
            source = (
                "def f():\n"
                "    threshold = 10\n"
                "    value = 5\n"
                "    return value > threshold\n"
            )
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type == "int"

        def it_infers_int_from_len_call_assignment():
            """n = len(items) should be inferred as int."""
            source = "def f(items):\n    n = len(items)\n    return n + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "int"

        def it_does_not_enter_nested_blocks():
            """Assignments inside if/for/while blocks are not walked."""
            source = "def f():\n    if True:\n        x = 5\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # x is only assigned inside an if block — not in top-level body
            # so it should not appear in the env; type falls back to None for x,
            # but '1' is int so we still get int
            assert binops[0].inferred_type == "int"

        def it_handles_annotation_without_value():
            """x: int (no assignment) should still populate env."""
            source = "def f():\n    x: int\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "int"

        def it_env_assignment_overrides_param_annotation():
            """If a param x: float is shadowed by x = 5 (int), env wins."""
            source = "def f(x: float):\n    x = 5\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # env wins: x = 5 is int, overrides param annotation float
            assert binops[0].inferred_type == "int"

        def it_does_not_propagate_type_through_conflicted_env_variable():
            """z = x + 5 where x is conflicted (env[x]=None) should give z=None.

            If the left operand of the RHS BinOp is a variable with a known but
            conflicted (None) type in env, we must NOT fall through to infer from
            the right operand.  Doing so would silently assign an incorrect type
            to z.
            """
            source = 'def f():\n    x = 1\n    x = "hello"\n    z = x + 5\n    return z + 1\n'
            points = find_mutation_points(source, "test.py", "test")
            enriched, _ = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            # z = x + 5: x is conflicted → z is unknown → z + 1 BinOp has no type
            return_binop = [b for b in binops if b.lineno == 5]
            assert len(return_binop) == 1
            assert return_binop[0].inferred_type is None


def describe_enrichment_stats():
    """Verify EnrichmentStats tracking in enrich_mutation_points."""

    def it_returns_empty_stats_for_empty_points():
        _, stats = enrich_mutation_points("def f(): pass\n", [])
        assert stats.from_annotations == 0
        assert stats.from_assignment_dataflow == 0

    def it_counts_annotation_enrichment():
        """Parameter annotation enrichment goes to from_annotations."""
        source = "def f(x: int, y: int) -> int:\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_annotations > 0
        assert stats.from_assignment_dataflow == 0

    def it_counts_return_annotation_enrichment():
        """Return annotation enrichment goes to from_annotations."""
        source = "def f() -> bool:\n    return True\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_annotations >= 1
        assert stats.from_assignment_dataflow == 0

    def it_counts_dataflow_enrichment_from_literal_assignment():
        """Assignment from literal (x = 5) goes to from_assignment_dataflow."""
        source = "def f():\n    x = 5\n    return x + 1\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        # x is in env (not param_types), so the BinOp is credited to dataflow
        assert stats.from_assignment_dataflow >= 1

    def it_counts_dataflow_enrichment_from_compare():
        """Dataflow through Compare node is counted as assignment dataflow."""
        source = "def f():\n    threshold = 10\n    return threshold > 5\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_assignment_dataflow >= 1

    def it_buckets_are_mutually_exclusive():
        """Each enriched point is in exactly one bucket (total = annotations + dataflow)."""
        source = "def f(x: int) -> int:\n    y = 5\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, stats = enrich_mutation_points(source, points)
        enriched_count = sum(1 for p in enriched if p.inferred_type is not None)
        assert enriched_count == stats.from_annotations + stats.from_assignment_dataflow

    def it_returns_zero_stats_when_no_types_inferred():
        """Unannotated functions with no literal assignments produce zero stats."""
        source = "def f(x, y):\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        # x + y: neither x nor y has a type source
        # BUT: the right-hand literal path isn't here — x and y are both params
        assert stats.from_annotations == 0
        assert stats.from_assignment_dataflow == 0

    def it_counts_boolop_as_annotation():
        """BoolOp is hardcoded to 'bool' — not from dataflow."""
        source = "def f(a, b):\n    return a and b\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_annotations >= 1
        assert stats.from_assignment_dataflow == 0

    def it_unannotated_module_level_code_is_uncounted():
        """Module-level mutation points outside any function produce no stats."""
        source = "x = 1 + 2\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_annotations == 0
        assert stats.from_assignment_dataflow == 0


# ---------------------------------------------------------------------------
# Direct unit tests for internal helpers — kill surviving mutants
# ---------------------------------------------------------------------------


def describe_find_enclosing_func():
    """Unit tests for _find_enclosing_func that kill arithmetic/comparison mutants."""

    def _make_func_info(name, start, end):
        """Build a minimal _FuncInfo by parsing a real function and patching line ranges."""
        from pytest_leela.type_extractor import _TypeCollector

        # Parse a short stub; actual lines don't matter because we overwrite them.
        source = "def stub(x: int) -> int:\n    return x\n"
        tree = ast.parse(source)
        collector = _TypeCollector()
        collector.visit(tree)
        func = collector.functions[0]
        func.start_line = start
        func.end_line = end
        func.name = name
        return func

    def it_returns_none_when_no_functions():
        assert _find_enclosing_func([], 5) is None

    def it_returns_the_sole_matching_function():
        f = _make_func_info("f", 1, 10)
        assert _find_enclosing_func([f], 5) is f

    def it_returns_none_when_line_is_before_all_functions():
        f = _make_func_info("f", 1, 10)
        assert _find_enclosing_func([f], 0) is None

    def it_returns_none_when_line_is_after_all_functions():
        f = _make_func_info("f", 1, 10)
        assert _find_enclosing_func([f], 20) is None

    def it_includes_the_start_line():
        """start_line <= lineno boundary: kills - → + on 'func.start_line <= lineno'."""
        f = _make_func_info("f", 5, 15)
        assert _find_enclosing_func([f], 5) is f

    def it_includes_the_end_line():
        """lineno <= end_line boundary: kills - → + on 'lineno <= func.end_line'."""
        f = _make_func_info("f", 5, 15)
        assert _find_enclosing_func([f], 15) is f

    def it_picks_innermost_of_two_nested_functions():
        """Kills line 129 (-1→+1), line 132 (span=end-start→end+start), line 133 (<→>=).

        outer span=9, inner span=4. With correct code inner wins.
        With best_range=+1 init: outer (span=9) fails > 1 check so only inner is ever
        recorded. With span=end+start: outer span=11, inner span=10 — inner still wins
        for < but different spans expose the arithmetic error in edge cases.
        With >= instead of <: first function encountered would win (outer), not inner.
        """
        outer = _make_func_info("outer", 1, 10)  # span = 9
        inner = _make_func_info("inner", 3, 7)  # span = 4
        result = _find_enclosing_func([outer, inner], 5)
        assert result is inner

    def it_picks_innermost_when_outer_is_listed_second():
        """Order should not matter — innermost wins regardless of list position."""
        outer = _make_func_info("outer", 1, 10)
        inner = _make_func_info("inner", 3, 7)
        result = _find_enclosing_func([inner, outer], 5)
        assert result is inner

    def it_handles_equal_span_by_keeping_first_match():
        """Kills line 133: < → <= would replace first match with equal-span second.

        With < (correct): f2 span equals f1 span → NOT less than → f1 kept.
        With <= (mutant): f2 span equals f1 span → IS <= → f2 replaces f1.
        """
        f1 = _make_func_info("f1", 1, 5)  # span = 4
        f2 = _make_func_info("f2", 1, 5)  # span = 4
        result = _find_enclosing_func([f1, f2], 3)
        assert result is f1

    def it_verifies_arithmetic_with_large_span_difference():
        """Verify span = end - start (not end + start) matters for selection.

        outer: start=1 end=100 → span=99 (end-start) or 101 (end+start)
        inner: start=50 end=60 → span=10 (end-start) or 110 (end+start)

        With end-start: inner span (10) < outer span (99) → inner wins (correct).
        With end+start: inner span (110) > outer span (101) → outer wins (wrong).
        This directly kills the line 132 '- → +' mutant.
        """
        outer = _make_func_info("outer", 1, 100)
        inner = _make_func_info("inner", 50, 60)
        result = _find_enclosing_func([outer, inner], 55)
        assert result is inner


def describe_type_of_value_node():
    """Direct unit tests for _type_of_value_node."""

    def it_returns_bool_for_true():
        node = ast.parse("True", mode="eval").body
        assert _type_of_value_node(node) == "bool"

    def it_returns_bool_for_false():
        node = ast.parse("False", mode="eval").body
        assert _type_of_value_node(node) == "bool"

    def it_returns_int_for_integer():
        node = ast.parse("42", mode="eval").body
        assert _type_of_value_node(node) == "int"

    def it_returns_float_for_float():
        node = ast.parse("3.14", mode="eval").body
        assert _type_of_value_node(node) == "float"

    def it_returns_str_for_string():
        node = ast.parse('"hello"', mode="eval").body
        assert _type_of_value_node(node) == "str"

    def it_returns_int_for_len_call():
        node = ast.parse("len(x)", mode="eval").body
        assert _type_of_value_node(node) == "int"

    def it_returns_none_for_non_len_call():
        node = ast.parse("abs(x)", mode="eval").body
        assert _type_of_value_node(node) is None

    def it_returns_none_for_name_node():
        node = ast.parse("x", mode="eval").body
        assert _type_of_value_node(node) is None

    def it_returns_none_for_binop_node():
        node = ast.parse("x + 1", mode="eval").body
        assert _type_of_value_node(node) is None


def describe_infer_assigned_value_type():
    """Direct unit tests for _infer_assigned_value_type killing lines 204/207/219-227."""

    def it_returns_type_from_env_for_name_node():
        """Line 204: return env[name] — must return the type, not None."""
        env = {"x": "int"}
        node = ast.parse("x", mode="eval").body
        result = _infer_assigned_value_type(node, {}, env)
        assert result == "int"

    def it_returns_none_from_env_when_type_is_none():
        """Line 204: env[name] can be None (conflicted); returns None, not falls through."""
        env = {"x": None}
        node = ast.parse("x", mode="eval").body
        result = _infer_assigned_value_type(node, {}, env)
        assert result is None

    def it_returns_type_from_param_types_for_name_node():
        """Lines 205-206: return param_types[name].

        Kills line 207: None → expr would return param type unconditionally even
        when not in env and not in param_types (returning wrong value).
        We verify the correct branch: name in param_types → return its type.
        """
        param_types = {"x": "float"}
        node = ast.parse("x", mode="eval").body
        result = _infer_assigned_value_type(node, param_types, {})
        assert result == "float"

    def it_prefers_env_over_param_types_for_name_node():
        """When name is in both env and param_types, env takes priority (line 203-204)."""
        env = {"x": "int"}
        param_types = {"x": "float"}
        node = ast.parse("x", mode="eval").body
        result = _infer_assigned_value_type(node, param_types, env)
        assert result == "int"

    def it_returns_none_for_unknown_name():
        """Name not in env or param_types returns None (line 207)."""
        node = ast.parse("x", mode="eval").body
        result = _infer_assigned_value_type(node, {}, {})
        assert result is None

    def it_returns_constant_type_for_non_name():
        """Delegates to _type_of_value_node for non-Name nodes."""
        node = ast.parse("42", mode="eval").body
        result = _infer_assigned_value_type(node, {}, {})
        assert result == "int"

    def it_returns_none_for_unresolvable_expr():
        """Non-Name, non-Constant, non-Call, non-BinOp returns None."""
        node = ast.parse("x > 1", mode="eval").body  # Compare
        result = _infer_assigned_value_type(node, {}, {})
        assert result is None

    def it_handles_binop_with_known_left_name_in_env():
        """Line 219: left is Name in env → use env type.

        Kills line 219: in → not in (would skip the env-is-authoritative path).
        """
        env = {"x": "str"}
        node = ast.parse("x + 1", mode="eval").body
        result = _infer_assigned_value_type(node, {}, env)
        # x in env with type "str"; left authoritative → "str", NOT "int" from right
        assert result == "str"

    def it_handles_binop_with_known_left_name_in_param_types():
        """Line 219: left Name in param_types → also authoritative.

        Kills line 219: in → not in (would miss the param_types check for left).
        """
        param_types = {"x": "float"}
        node = ast.parse("x + 1", mode="eval").body
        result = _infer_assigned_value_type(node, param_types, {})
        assert result == "float"

    def it_handles_binop_with_conflicted_left_name_does_not_fall_through():
        """Line 221: conflicted left Name returns None, not right operand type.

        Kills line 221: return expr → return None (would return None always, masking
        the distinction between 'conflicted' and 'unresolvable').
        We confirm it's None (not int from right operand 5).
        """
        env = {"x": None}  # conflicted
        node = ast.parse("x + 5", mode="eval").body
        result = _infer_assigned_value_type(node, {}, env)
        assert result is None

    def it_handles_binop_with_unknown_left_falls_through_to_right():
        """Lines 222-226: left not in env/param → try left (None), then right.

        Kills line 223: return expr → return None (would discard resolved left).
        Kills line 226: return expr → return None (would discard resolved right).
        """
        node = ast.parse("x + 5", mode="eval").body
        result = _infer_assigned_value_type(node, {}, {})
        # x not in env or params → left unresolvable; right=5 → int
        assert result == "int"

    def it_handles_binop_with_resolvable_left_returns_left():
        """Line 223-224: when left resolves, return left not right.

        Kills line 223: return expr → return None (discards left result).
        """
        node = ast.parse("3.14 + 5", mode="eval").body
        result = _infer_assigned_value_type(node, {}, {})
        # left=3.14→float, right=5→int; left resolves → float
        assert result == "float"

    def it_handles_binop_with_right_name_in_env():
        """Lines 225-226: left unresolvable, right Name in env → env type.

        Kills line 227: None → expr (return would always be None).
        """
        env = {"y": "str"}
        node = ast.parse("unknown_func() + y", mode="eval").body
        result = _infer_assigned_value_type(node, {}, env)
        # left is a non-len Call → None; right y in env → "str"
        assert result == "str"


def describe_build_assignment_env_multi_target():
    """Tests for _build_assignment_env that kill the line 261 len(targets)==1 mutant."""

    def it_does_not_process_multi_target_assignment():
        """a = b = 5 has two targets — NOT added to env.

        Kills line 261: removing the len()==1 guard would add both targets.
        """
        source = "def f():\n    a = b = 5\n    return a + b\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        # a and b not in env; neither is an annotated param → type is None
        assert binops[0].inferred_type is None

    def it_processes_single_target_assignment_normally():
        """Single target x = 5 DOES populate env → type is int."""
        source = "def f():\n    x = 5\n    return x + 1\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type == "int"


def describe_infer_augassign_type_target_type_fallback():
    """Kill line 320: return target_type → return None in _infer_augassign_type."""

    def it_returns_float_target_type_from_param_via_non_env_fast_path():
        """When target Name is a param annotation, type comes from the env fast path.

        This exercises the _infer_expr_type(target) call at line 318 and the
        'if target_type is not None: return target_type' check at line 319-320.

        Kills line 320: return target_type → return None.
        """
        source = "def f(x: float) -> None:\n    x += 1\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        augassigns = [p for p in enriched if p.node_type == "AugAssign"]
        assert len(augassigns) == 1
        # x in param_types as "float" → target_type = "float" → must return "float"
        assert augassigns[0].inferred_type == "float"

    def it_returns_value_type_when_target_is_subscript():
        """Target is a Subscript (not Name), not in env/params → falls to value.

        Lines 321-323: value_type = _infer_expr_type(node.value); return value_type.
        This ensures the value-fallback path works when target is unresolvable.
        """
        source = "def f(data):\n    data[0] += 1.5\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched, _ = enrich_mutation_points(source, points)
        augassigns = [p for p in enriched if p.node_type == "AugAssign"]
        assert len(augassigns) == 1
        # data is unannotated; target is subscript (unresolvable); value 1.5 → float
        assert augassigns[0].inferred_type == "float"


def describe_type_came_from_dataflow():
    """Unit tests for _type_came_from_dataflow killing lines 389-419."""

    def _build_func_and_tree(source: str):
        """Parse source and return the first function's _FuncInfo and tree."""
        from pytest_leela.type_extractor import _TypeCollector

        tree = ast.parse(source)
        collector = _TypeCollector()
        collector.visit(tree)
        return collector.functions[0], tree

    def it_returns_false_for_return_node_type():
        """Return always comes from annotation, not dataflow (lines 377-379)."""
        source = "def f(x: int) -> int:\n    return x\n"
        func, tree = _build_func_and_tree(source)
        result = _type_came_from_dataflow("int", "Return", func, tree, 2, 0)
        assert result is False

    def it_returns_false_for_boolop_node_type():
        """BoolOp is hardcoded to bool, never from env (lines 381-383)."""
        source = "def f(a, b):\n    return a and b\n"
        func, tree = _build_func_and_tree(source)
        result = _type_came_from_dataflow("bool", "BoolOp", func, tree, 2, 11)
        assert result is False

    def it_returns_false_for_binop_when_no_node_found():
        """BinOp: _find_node_at returns None → False (line 392).

        Kills line 392: return False → return True.
        """
        source = "def f(x: int) -> int:\n    return x + 1\n"
        func, tree = _build_func_and_tree(source)
        result = _type_came_from_dataflow("int", "BinOp", func, tree, 2, 9999)
        assert result is False

    def it_returns_true_for_binop_when_left_operand_from_env():
        """BinOp line 388: left operand from env → True.

        Kills line 389: and → or, is → is not.
        Kills line 392: return False → return True.
        """
        source = "def f():\n    x = 5\n    return x + 1\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_assignment_dataflow >= 1
        assert stats.from_annotations == 0

    def it_returns_false_for_binop_when_left_operand_from_param_types():
        """BinOp: left operand from param_types (not env-only) → False.

        Kills line 392: return False → return True.
        """
        source = "def f(x: int) -> int:\n    return x + 1\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_annotations >= 1
        assert stats.from_assignment_dataflow == 0

    def it_returns_true_for_binop_when_right_operand_from_env_and_left_unresolvable():
        """BinOp lines 389-391: left unresolvable, right from env → True.

        Kills line 389: and → or (must require left is None AND right from env).

        We need left to be a Name not in env/params (so _infer_expr_type returns None),
        and right to be a Name in env (from assignment).
        """
        # unknown_param is unannotated (not in params, not in env).
        # n is assigned in env (len → int). Left: unknown_param, Right: n.
        source = "def f(unknown_param):\n    n = len(unknown_param)\n    return unknown_param + n\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        # unknown_param is in param_types with no annotation (so not in param_types dict)
        # wait — unannotated params are NOT in param_types.
        # left=unknown_param (not in env, not in param_types) → _infer_expr_type=None
        # right=n (in env as int) → _operand_from_env_not_param=True
        # So: False or (None is None and True) → True → dataflow
        assert stats.from_assignment_dataflow >= 1

    def it_returns_false_for_binop_when_left_is_literal_and_right_from_env():
        """BinOp line 389: kills and → or mutant.

        When left is a literal (not from env, but RESOLVABLE via _type_of_value_node),
        the second condition becomes: '_infer_expr_type(left) is None' = False.

        With and (correct): False and True → False → second clause is False → return False.
        With or (mutant): False or True → True → incorrectly returns True.

        This distinguishes and from or: when left resolves (not None) but is not from
        env, AND right is from env, the correct answer is False (left literal sources
        the type, not dataflow), but the mutant returns True.
        """
        # 5 + n: left=5 (literal, not from env), right=n (from env as int)
        # The BinOp type comes from left (5 → int via literal), NOT from n.
        # So this should be counted as from_annotations (literal is annotation-like).
        source = "def f():\n    n = 5\n    return 5 + n\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        # left=5 (Constant, not a Name) → _operand_from_env_not_param=False
        # _infer_expr_type(5) = "int" (not None)
        # second clause: False → whole or-expression = False
        # So: NOT from dataflow (literal drives the type)
        assert stats.from_assignment_dataflow == 0
        assert stats.from_annotations >= 1

    def it_returns_false_for_compare_when_no_node_found():
        """Compare: no node found → False (line 402).

        Kills line 402: return False → return True.
        """
        source = "def f(x: int) -> bool:\n    return x > 0\n"
        func, tree = _build_func_and_tree(source)
        result = _type_came_from_dataflow("int", "Compare", func, tree, 2, 9999)
        assert result is False

    def it_returns_true_for_compare_when_left_from_env():
        """Compare lines 397-398: left operand from env → True.

        Kills line 401: return True → return False.
        """
        source = "def f():\n    threshold = 10\n    return threshold > 5\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_assignment_dataflow >= 1
        assert stats.from_annotations == 0

    def it_returns_true_for_compare_when_comparator_from_env():
        """Compare lines 399-401: comparator from env → True.

        Kills line 401: return True → return False.
        """
        source = "def f():\n    limit = 100\n    return 50 < limit\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_assignment_dataflow >= 1

    def it_returns_false_for_compare_when_all_from_params():
        """Compare: all operands from param_types → False.

        Kills line 402: return False → return True.
        """
        source = "def f(x: int, y: int) -> bool:\n    return x > y\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_annotations >= 1
        assert stats.from_assignment_dataflow == 0

    def it_returns_false_for_augassign_when_no_node_found():
        """AugAssign: no node found → False (line 411).

        Kills line 404: == → != (wrong node_type check would misidentify).
        """
        source = "def f(x: int) -> None:\n    x += 1\n"
        func, tree = _build_func_and_tree(source)
        result = _type_came_from_dataflow("int", "AugAssign", func, tree, 2, 9999)
        assert result is False

    def it_returns_true_for_augassign_when_target_from_env():
        """AugAssign line 407: target from env → True.

        Kills line 404: == → != (wrong node_type).
        Kills line 407: return x → return -x (negates boolean).
        Kills line 407: or → and (requires both conditions simultaneously).
        """
        source = "def f():\n    x = 5\n    x += 1\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_assignment_dataflow >= 1

    def it_returns_false_for_augassign_when_target_from_param():
        """AugAssign: target is a param → False.

        Kills line 411: return False → return True.
        """
        source = "def f(x: int) -> None:\n    x += 1\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_annotations >= 1
        assert stats.from_assignment_dataflow == 0

    def it_returns_true_for_augassign_when_value_from_env_and_target_unresolvable():
        """AugAssign lines 408-410: target unresolvable, value from env → True.

        Kills line 408: and → or (both conditions required simultaneously).
        Kills line 408: is → is not (must require target type IS None).
        """
        # data[0] += n: target is subscript (unresolvable), value n is in env.
        source = "def f(data):\n    n = 5\n    data[0] += n\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_assignment_dataflow >= 1

    def it_returns_false_for_augassign_when_target_is_subscript_and_value_is_literal():
        """AugAssign line 408: kills and → or mutant.

        When target is a subscript (non-Name), _infer_expr_type(target) = None (True),
        but value is a literal (not from env), so _operand_from_env_not_param(value) = False.

        With and (correct): None is None and False → True and False → False → return False.
        With or (mutant): None is None or False → True or False → True → incorrectly True.
        """
        # data[0] += 1: target is subscript, value is literal int (not from env)
        source = "def f(data):\n    data[0] += 1\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        # Target: subscript (not Name) → not from env/params
        # Value: 1 (Constant, not a Name) → _operand_from_env_not_param=False
        # Correct: False or (True and False) = False → from_annotations
        # Mutant:  False or (True or False) = True → from_assignment_dataflow
        assert stats.from_assignment_dataflow == 0

    def it_returns_false_for_unaryop_when_no_node_found():
        """UnaryOp: no node found → False (line 417).

        Kills line 413: == → != (wrong node_type check).
        Kills line 417: return False → return True.
        """
        source = "def f(x: int) -> int:\n    return -x\n"
        func, tree = _build_func_and_tree(source)
        result = _type_came_from_dataflow("int", "UnaryOp", func, tree, 2, 9999)
        assert result is False

    def it_returns_true_for_unaryop_when_operand_from_env():
        """UnaryOp line 416: operand from env → True.

        Kills line 416: return x → return -x (negates the boolean result).
        Kills line 417: return False → return True (always True when node found).
        """
        source = "def f():\n    x = 5\n    return -x\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_assignment_dataflow >= 1

    def it_returns_false_for_unaryop_when_operand_from_param():
        """UnaryOp: operand is a param → False.

        Kills line 417: return False → return True.
        """
        source = "def f(x: int) -> int:\n    return -x\n"
        points = find_mutation_points(source, "test.py", "test")
        _, stats = enrich_mutation_points(source, points)
        assert stats.from_annotations >= 1
        assert stats.from_assignment_dataflow == 0

    def it_returns_false_for_unknown_node_type():
        """Unrecognized node_type falls through to line 419: return False.

        Kills line 419: return False → return True.
        """
        source = "def f(x: int) -> int:\n    return x + 1\n"
        func, tree = _build_func_and_tree(source)
        result = _type_came_from_dataflow(
            "int", "SomeUnknownNodeType", func, tree, 2, 0
        )
        assert result is False


def describe_operand_from_env_not_param():
    """Direct unit tests for _operand_from_env_not_param."""

    def it_returns_true_when_name_in_env_with_type_and_not_in_params():
        env = {"x": "int"}
        param_types: dict = {}
        node = ast.parse("x", mode="eval").body
        assert _operand_from_env_not_param(node, env, param_types) is True

    def it_returns_false_when_name_in_env_but_also_in_param_types():
        env = {"x": "int"}
        param_types = {"x": "int"}
        node = ast.parse("x", mode="eval").body
        assert _operand_from_env_not_param(node, env, param_types) is False

    def it_returns_false_when_name_in_env_but_type_is_none():
        env = {"x": None}
        param_types: dict = {}
        node = ast.parse("x", mode="eval").body
        assert _operand_from_env_not_param(node, env, param_types) is False

    def it_returns_false_when_name_not_in_env():
        env: dict = {}
        param_types: dict = {}
        node = ast.parse("x", mode="eval").body
        assert _operand_from_env_not_param(node, env, param_types) is False

    def it_returns_false_for_non_name_node():
        env = {"x": "int"}
        param_types: dict = {}
        node = ast.parse("42", mode="eval").body
        assert _operand_from_env_not_param(node, env, param_types) is False
