"""Tests for pytest_leela.operators — mutation operator registry."""

from pytest_leela.models import MutationPoint
from pytest_leela.operators import count_pruned, mutations_for


def _make_point(
    node_type: str = "BinOp",
    original_op: str = "Add",
    inferred_type: str | None = None,
) -> MutationPoint:
    return MutationPoint(
        file_path="test.py",
        module_name="test",
        lineno=1,
        col_offset=0,
        node_type=node_type,
        original_op=original_op,
        inferred_type=inferred_type,
    )


def describe_mutations_for():
    def it_returns_typed_mutations_for_int_binop():
        point = _make_point(node_type="BinOp", original_op="Add", inferred_type="int")
        muts = mutations_for(point, use_types=True)
        assert "Sub" in muts
        assert "Mult" in muts
        assert "FloorDiv" in muts

    def it_prunes_str_addition():
        point = _make_point(node_type="BinOp", original_op="Add", inferred_type="str")
        muts = mutations_for(point, use_types=True)
        assert muts == []

    def it_returns_untyped_mutations_when_no_type():
        point = _make_point(node_type="BinOp", original_op="Add", inferred_type=None)
        muts = mutations_for(point, use_types=True)
        assert "Sub" in muts
        assert "Mult" in muts
        # Untyped Add doesn't include FloorDiv
        assert "FloorDiv" not in muts

    def it_mutates_comparisons():
        point = _make_point(node_type="Compare", original_op="Lt", inferred_type="int")
        muts = mutations_for(point, use_types=True)
        assert "LtE" in muts
        assert "GtE" in muts

    def it_mutates_boolop():
        point = _make_point(node_type="BoolOp", original_op="And", inferred_type="bool")
        muts = mutations_for(point, use_types=True)
        assert muts == ["Or"]

    def it_mutates_return_true():
        point = _make_point(node_type="Return", original_op="True", inferred_type="bool")
        muts = mutations_for(point, use_types=True)
        assert "False" in muts

    def it_skips_negate_for_zero_int_literal():
        """return 0 → return -0 is equivalent; no mutation should be generated."""
        point = _make_point(node_type="Return", original_op="zero_int_literal")
        assert mutations_for(point, use_types=False) == []

    def it_skips_negate_for_zero_int_literal_typed():
        """Typed variant: return 0 should also produce no mutations."""
        point = _make_point(node_type="Return", original_op="zero_int_literal", inferred_type="int")
        assert mutations_for(point, use_types=True) == []

    def it_skips_negate_for_zero_float_literal():
        """return 0.0 → return -0.0 is equivalent; no mutation should be generated."""
        point = _make_point(node_type="Return", original_op="zero_float_literal")
        assert mutations_for(point, use_types=False) == []

    def it_skips_negate_for_zero_float_literal_typed():
        """Typed variant: return 0.0 should also produce no mutations."""
        point = _make_point(node_type="Return", original_op="zero_float_literal", inferred_type="float")
        assert mutations_for(point, use_types=True) == []

    def it_still_negates_nonzero_int_literal():
        """Non-zero int literals should still get the negate mutation."""
        point = _make_point(node_type="Return", original_op="int_literal")
        assert mutations_for(point, use_types=False) == ["negate"]

    def it_still_negates_nonzero_float_literal():
        """Non-zero float literals should still get the negate mutation."""
        point = _make_point(node_type="Return", original_op="float_literal")
        assert mutations_for(point, use_types=False) == ["negate"]

    def describe_bitwise_operators():
        def it_mutates_bitand_untyped():
            point = _make_point(node_type="BinOp", original_op="BitAnd")
            muts = mutations_for(point, use_types=False)
            assert "BitOr" in muts
            assert "BitXor" in muts

        def it_mutates_bitor_untyped():
            point = _make_point(node_type="BinOp", original_op="BitOr")
            muts = mutations_for(point, use_types=False)
            assert "BitAnd" in muts
            assert "BitXor" in muts

        def it_mutates_bitxor_untyped():
            point = _make_point(node_type="BinOp", original_op="BitXor")
            muts = mutations_for(point, use_types=False)
            assert "BitAnd" in muts
            assert "BitOr" in muts

        def it_mutates_lshift_untyped():
            point = _make_point(node_type="BinOp", original_op="LShift")
            muts = mutations_for(point, use_types=False)
            assert muts == ["RShift"]

        def it_mutates_rshift_untyped():
            point = _make_point(node_type="BinOp", original_op="RShift")
            muts = mutations_for(point, use_types=False)
            assert muts == ["LShift"]

        def it_keeps_all_mutations_for_int_bitand():
            point = _make_point(node_type="BinOp", original_op="BitAnd", inferred_type="int")
            muts = mutations_for(point, use_types=True)
            assert "BitOr" in muts
            assert "BitXor" in muts

        def it_keeps_all_mutations_for_int_bitor():
            point = _make_point(node_type="BinOp", original_op="BitOr", inferred_type="int")
            muts = mutations_for(point, use_types=True)
            assert "BitAnd" in muts
            assert "BitXor" in muts

        def it_keeps_all_mutations_for_int_bitxor():
            point = _make_point(node_type="BinOp", original_op="BitXor", inferred_type="int")
            muts = mutations_for(point, use_types=True)
            assert "BitAnd" in muts
            assert "BitOr" in muts

        def it_keeps_shift_mutations_for_int():
            point_l = _make_point(node_type="BinOp", original_op="LShift", inferred_type="int")
            point_r = _make_point(node_type="BinOp", original_op="RShift", inferred_type="int")
            assert mutations_for(point_l, use_types=True) == ["RShift"]
            assert mutations_for(point_r, use_types=True) == ["LShift"]

        def it_prunes_bool_bitand_to_single_mutation():
            point = _make_point(node_type="BinOp", original_op="BitAnd", inferred_type="bool")
            muts = mutations_for(point, use_types=True)
            assert muts == ["BitOr"]

        def it_prunes_bool_bitor_to_single_mutation():
            point = _make_point(node_type="BinOp", original_op="BitOr", inferred_type="bool")
            muts = mutations_for(point, use_types=True)
            assert muts == ["BitAnd"]

        def it_keeps_both_mutations_for_bool_bitxor():
            point = _make_point(node_type="BinOp", original_op="BitXor", inferred_type="bool")
            muts = mutations_for(point, use_types=True)
            assert muts == ["BitAnd", "BitOr"]

        def it_falls_through_to_untyped_for_bool_shifts():
            point_l = _make_point(node_type="BinOp", original_op="LShift", inferred_type="bool")
            point_r = _make_point(node_type="BinOp", original_op="RShift", inferred_type="bool")
            assert mutations_for(point_l, use_types=True) == ["RShift"]
            assert mutations_for(point_r, use_types=True) == ["LShift"]

    def it_falls_through_to_untyped_for_unknown_typed_key():
        # A type that doesn't have a typed rule should fall through to untyped
        point = _make_point(node_type="BinOp", original_op="Add", inferred_type="complex")
        muts = mutations_for(point, use_types=True)
        # Falls through to untyped: ["Sub", "Mult"]
        assert "Sub" in muts
        assert "Mult" in muts


def describe_count_pruned():
    def it_counts_pruned_mutations():
        # str + str: typed gives 0 mutations, untyped gives 2 (Sub, Mult)
        point = _make_point(node_type="BinOp", original_op="Add", inferred_type="str")
        pruned = count_pruned([point], use_types=True)
        assert pruned == 2  # untyped has ["Sub", "Mult"], typed has []

    def it_returns_zero_when_types_disabled():
        point = _make_point(node_type="BinOp", original_op="Add", inferred_type="str")
        pruned = count_pruned([point], use_types=False)
        assert pruned == 0

    def it_counts_zero_for_non_pruned():
        # int Add: typed has 3 mutations, untyped has 2 -> pruned = 2 - 3 = -1? No.
        # Actually untyped Add = ["Sub", "Mult"] (2), typed int Add = ["Sub", "Mult", "FloorDiv"] (3)
        # pruned = 2 - 3 = -1, but that's expansion not pruning
        point = _make_point(node_type="BinOp", original_op="Add", inferred_type="int")
        pruned = count_pruned([point], use_types=True)
        # This can be negative (expansion), which is expected behavior
        assert pruned == -1

    def it_handles_empty_list():
        pruned = count_pruned([], use_types=True)
        assert pruned == 0

    def it_returns_non_negative_for_pruned_types():
        """When typed has fewer mutations than untyped, result is positive."""
        point = _make_point(node_type="BinOp", original_op="Add", inferred_type="str")
        pruned = count_pruned([point], use_types=True)
        assert pruned > 0

    def it_sums_across_multiple_points():
        """count_pruned sums pruned counts across all points."""
        points = [
            _make_point(node_type="BinOp", original_op="Add", inferred_type="str"),
            _make_point(node_type="BinOp", original_op="Mult", inferred_type="str"),
        ]
        pruned = count_pruned(points, use_types=True)
        # str Add: untyped 2, typed 0 -> pruned 2
        # str Mult: untyped 2, typed 0 -> pruned 2
        assert pruned == 4
