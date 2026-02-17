"""Tests for pytest_leela.type_extractor — type annotation enrichment."""

from pytest_leela.ast_analysis import find_mutation_points
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
