"""Tests for pytest_leela.type_extractor — type annotation enrichment."""

import ast

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.models import MutationPoint
from pytest_leela.type_extractor import enrich_mutation_points


def describe_enrich_mutation_points():
    def it_infers_int_type_from_parameter_annotation():
        source = "def f(x: int, y: int) -> int:\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type == "int"

    def it_infers_str_type_from_parameter_annotation():
        source = "def f(a: str, b: str) -> str:\n    return a + b\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type == "str"

    def it_infers_return_type_for_return_nodes():
        source = "def f() -> bool:\n    return True\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched = enrich_mutation_points(source, points)
        returns = [p for p in enriched if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].inferred_type == "bool"

    def it_handles_optional_return_types():
        source = "from typing import Optional\ndef f(x: int) -> Optional[int]:\n    if x > 0:\n        return x\n    return None\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched = enrich_mutation_points(source, points)
        returns = [p for p in enriched if p.node_type == "Return"]
        for r in returns:
            assert r.inferred_type == "Optional[int]"

    def it_leaves_unannotated_as_none():
        source = "def f(x, y):\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type is None

    def it_infers_float_type_from_annotation():
        source = "def f(x: float, y: float) -> float:\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type == "float"

    def it_infers_bool_for_boolop():
        source = "def f(a: bool, b: bool) -> bool:\n    return a and b\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched = enrich_mutation_points(source, points)
        boolops = [p for p in enriched if p.node_type == "BoolOp"]
        assert len(boolops) >= 1
        assert boolops[0].inferred_type == "bool"

    def it_returns_empty_list_unchanged():
        enriched = enrich_mutation_points("def f(): pass\n", [])
        assert enriched == []

    def it_infers_type_from_constant_operand():
        source = "def f(x):\n    return x + 1\n"
        points = find_mutation_points(source, "test.py", "test")
        enriched = enrich_mutation_points(source, points)
        binops = [p for p in enriched if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].inferred_type == "int"

    # --- Group 1: _annotation_to_str branches (L15-42) ---

    def describe_annotation_to_str():
        def it_resolves_string_forward_ref_annotations():
            """L15: str constant annotation (forward reference like 'int')."""
            source = 'def f(x: "int") -> "bool":\n    return x > 0\n'
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
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
            enriched = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # x has annotation None -> _annotation_to_str returns str(None) = "None"
            # BinOp checks left (x -> "None") first
            assert binops[0].inferred_type == "None"

        def it_resolves_dotted_attribute_annotations():
            """L21-22: Attribute annotation like typing.Optional."""
            source = "import typing\ndef f() -> typing.Optional:\n    return None\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "typing.Optional"

        def it_returns_none_for_unresolvable_attribute_base():
            """L23: Attribute with base that _annotation_to_str can't resolve."""
            source = "def f(x: foo().bar, y: int) -> int:\n    return x + y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # x annotation unresolvable (foo().bar -> None), y is "int"
            # BinOp checks left (x not in param_types), then right (y -> "int")
            assert binops[0].inferred_type == "int"

        def it_resolves_optional_subscript_with_valid_inner():
            """L26-29: Optional[T] with resolvable inner type."""
            source = "from typing import Optional\ndef f(x: Optional[str]) -> Optional[int]:\n    return len(x)\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "Optional[int]"

        def it_returns_base_for_optional_with_unresolvable_inner():
            """L28 false path: Optional[<unresolvable>] returns just 'Optional'."""
            source = "from typing import Optional\ndef f() -> Optional[foo().bar]:\n    return None\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            # inner is unresolvable -> falls through to return base ("Optional")
            assert returns[0].inferred_type == "Optional"

        def it_resolves_list_subscript_annotation():
            """list[T] -> 'list'."""
            source = "def f(x: list[int]) -> list[int]:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "list"

        def it_resolves_dict_subscript_annotation():
            """dict[K, V] -> 'dict'."""
            source = "def f(x: dict[str, int]) -> dict[str, int]:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "dict"

        def it_resolves_set_subscript_annotation():
            """set[T] -> 'set'."""
            source = "def f(x: set[int]) -> set[int]:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "set"

        def it_resolves_tuple_subscript_annotation():
            """tuple[T, ...] -> 'tuple'."""
            source = "def f(x: tuple[int, str]) -> tuple[int, str]:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "tuple"

        def it_resolves_other_subscript_annotation():
            """L32: Subscript with non-container base returns base name."""
            source = "from typing import Sequence\ndef f() -> Sequence[int]:\n    return []\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "Sequence"

        def it_resolves_pipe_none_union_as_optional():
            """L33, L37-38: X | None -> Optional[X]."""
            source = "def f(x: int | None) -> int | None:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "Optional[int]"

        def it_resolves_none_pipe_type_as_optional():
            """L39-40: None | X -> Optional[X]."""
            source = "def f(x: None | str) -> None | str:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type == "Optional[str]"

        def it_returns_none_for_non_none_union():
            """L41-42: X | Y (neither None) -> None."""
            source = "def f(x: int | str) -> int | str:\n    return x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            returns = [p for p in enriched if p.node_type == "Return"]
            assert len(returns) >= 1
            assert returns[0].inferred_type is None

        def it_returns_none_for_union_with_unresolvable_sides():
            """L41: BitOr with left=None (unresolvable) -> None."""
            source = "def f(x: foo() | bar()) -> int:\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            # Filter to the Add BinOp on line 2 (not the BitOr in the annotation)
            add_binops = [p for p in enriched if p.node_type == "BinOp" and p.original_op == "Add"]
            assert len(add_binops) >= 1
            # x annotation unresolvable, but right operand 1 is int
            assert add_binops[0].inferred_type == "int"

        def it_returns_none_for_unsupported_annotation_node():
            """L42: final return None for unhandled AST node types (e.g. Set)."""
            source = "def f(x: {1, 2}, y: int) -> int:\n    return x + y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
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
            enriched = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # True is bool, not int — must return "bool" not "int"
            assert binops[0].inferred_type == "bool"

        def it_infers_float_from_literal():
            """L135-136: float constant in BinOp."""
            source = "def f(x):\n    return x + 1.5\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "float"

        def it_infers_str_from_literal():
            """L137-138: str constant in BinOp."""
            source = 'def f(x):\n    return x + "hello"\n'
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "str"

        def it_infers_bool_from_bool_literal():
            """L139-140: bool constant returns 'bool'."""
            source = "def f(x):\n    return x + False\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "bool"

        def it_infers_int_from_len_call():
            """L141-143: len() call returns 'int'."""
            source = "def f(x, y):\n    return len(x) + y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type == "int"

        def it_does_not_infer_type_for_non_len_calls():
            """L142: non-len Call returns None."""
            source = "def f(x, y):\n    return abs(x) + y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
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
            enriched = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type == "int"

        def it_infers_type_from_comparator():
            """L156-157: only comparator is typed (int literal)."""
            source = "def f(x):\n    return x > 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type == "int"

        def it_returns_none_when_neither_side_typed():
            """L158: neither left nor comparators have type info."""
            source = "def f(x, y):\n    return x > y\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type is None

        def it_infers_type_from_float_comparator():
            """L156-157: comparator is a float literal."""
            source = "def f(x):\n    return x > 1.5\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
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
            source = "def f(x: int) -> int:\n" + "    pass\n" * 99 + "    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")

            original_parse = ast.parse

            def nullify_end_lineno(src, *args, **kwargs):
                tree = original_parse(src, *args, **kwargs)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        node.end_lineno = 0
                return tree

            monkeypatch.setattr(ast, "parse", nullify_end_lineno)
            enriched = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # With + 100: end_line = 1 + 100 = 101, BinOp at line 101 is within [1, 101]
            # With * 100: end_line = 1 * 100 = 100, BinOp at line 101 is NOT within [1, 100]
            assert binops[0].inferred_type == "int"

        def it_leaves_module_level_binop_unenriched():
            """L112: _find_enclosing_func returns None for module-level code."""
            source = "x = 1 + 2\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            assert binops[0].inferred_type is None

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
            enriched = enrich_mutation_points(source, [bad_point])
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
            enriched = enrich_mutation_points(source, [bad_point])
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
            enriched = enrich_mutation_points(source, [bad_point])
            assert enriched[0].inferred_type is None

    # --- Enrichment dispatch (L195-214) ---

    def describe_enrichment_dispatch():
        def it_infers_type_for_unary_op():
            """L211-214: UnaryOp node type gets enriched from operand type."""
            source = "def f(x: int) -> int:\n    return -x\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            unaryops = [p for p in enriched if p.node_type == "UnaryOp"]
            assert len(unaryops) >= 1
            assert unaryops[0].inferred_type == "int"

        def it_enriches_correct_type_for_known_annotated_binop():
            """L198-201: BinOp enrichment produces correct type."""
            source = "def f(x: float) -> float:\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            binops = [p for p in enriched if p.node_type == "BinOp"]
            assert len(binops) >= 1
            # Left operand x has type "float", should take precedence over right (int)
            assert binops[0].inferred_type == "float"

        def it_enriches_compare_node_type():
            """L203-206: Compare enrichment through node lookup."""
            source = "def f(x: str) -> bool:\n    return x > 'a'\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
            compares = [p for p in enriched if p.node_type == "Compare"]
            assert len(compares) >= 1
            assert compares[0].inferred_type == "str"

        def it_handles_async_function_annotations():
            """Async functions are processed the same as sync functions."""
            source = "async def f(x: int) -> int:\n    return x + 1\n"
            points = find_mutation_points(source, "test.py", "test")
            enriched = enrich_mutation_points(source, points)
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
            enriched = enrich_mutation_points(source, [unknown_point])
            assert enriched[0].inferred_type is None
