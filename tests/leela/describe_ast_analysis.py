"""Tests for pytest_leela.ast_analysis — mutation point discovery."""

from pytest_leela.ast_analysis import find_mutation_points


def describe_find_mutation_points():
    def it_finds_binop_nodes():
        source = "def f(x: int, y: int) -> int:\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        binops = [p for p in points if p.node_type == "BinOp"]
        assert len(binops) >= 1
        assert binops[0].original_op == "Add"

    def it_finds_compare_nodes():
        source = "def f(x: int) -> bool:\n    return x > 0\n"
        points = find_mutation_points(source, "test.py", "test")
        compares = [p for p in points if p.node_type == "Compare"]
        assert len(compares) >= 1
        assert compares[0].original_op == "Gt"

    def it_finds_boolop_nodes():
        source = "def f(a: bool, b: bool) -> bool:\n    return a and b\n"
        points = find_mutation_points(source, "test.py", "test")
        boolops = [p for p in points if p.node_type == "BoolOp"]
        assert len(boolops) >= 1
        assert boolops[0].original_op == "And"

    def it_finds_unaryop_nodes():
        source = "def f(x: int) -> int:\n    return -x\n"
        points = find_mutation_points(source, "test.py", "test")
        unaryops = [p for p in points if p.node_type == "UnaryOp"]
        assert len(unaryops) >= 1
        assert unaryops[0].original_op == "USub"

    def it_finds_return_nodes():
        source = "def f() -> bool:\n    return True\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) >= 1
        assert returns[0].original_op == "True"

    def it_records_correct_line_numbers():
        source = (
            "def f(x: int) -> int:\n"  # line 1
            "    y = x + 1\n"  # line 2
            "    return y\n"  # line 3
        )
        points = find_mutation_points(source, "test.py", "test")
        binops = [p for p in points if p.node_type == "BinOp"]
        assert len(binops) == 1
        assert binops[0].lineno == 2

    def it_records_file_path_and_module_name():
        source = "def f(x: int) -> int:\n    return x + 1\n"
        points = find_mutation_points(source, "src/foo.py", "foo")
        assert all(p.file_path == "src/foo.py" for p in points)
        assert all(p.module_name == "foo" for p in points)

    def it_leaves_inferred_type_as_none():
        source = "def f(x: int, y: int) -> int:\n    return x + y\n"
        points = find_mutation_points(source, "test.py", "test")
        assert all(p.inferred_type is None for p in points)

    def it_classifies_return_int_literal():
        source = "def f() -> int:\n    return 42\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].original_op == "int_literal"

    def it_classifies_return_false():
        source = "def f() -> bool:\n    return False\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].original_op == "False"

    def it_classifies_return_none():
        source = "def f():\n    return None\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].original_op == "None"

    def it_classifies_return_expression():
        source = "def f(x):\n    return x\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].original_op == "expr"

    def it_finds_multiple_comparisons_in_chain():
        source = "def f(x: int) -> bool:\n    return 0 < x < 10\n"
        points = find_mutation_points(source, "test.py", "test")
        compares = [p for p in points if p.node_type == "Compare"]
        # Chained comparison has 2 ops: Lt, Lt
        assert len(compares) >= 2

    def it_skips_bare_return():
        source = "def f():\n    return\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 0

    def it_classifies_return_float_literal():
        source = "def f() -> float:\n    return 3.14\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].original_op == "float_literal"

    def it_classifies_return_str_literal():
        source = "def f() -> str:\n    return 'hello'\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].original_op == "str_literal"

    def it_classifies_return_negation():
        source = "def f(x):\n    return -x\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].original_op == "negation"

    def it_classifies_return_zero_int_literal():
        source = "def f() -> int:\n    return 0\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].original_op == "zero_int_literal"

    def it_classifies_return_zero_float_literal():
        source = "def f() -> float:\n    return 0.0\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].original_op == "zero_float_literal"

    def it_classifies_return_negation_not_uadd():
        """UnaryOp with UAdd (e.g. return +x) is NOT 'negation'."""
        source = "def f(x):\n    return +x\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        # +x is UnaryOp(UAdd), not negation (USub)
        assert returns[0].original_op == "expr"

    def it_classifies_return_empty_str_literal():
        source = "def f() -> str:\n    return ''\n"
        points = find_mutation_points(source, "test.py", "test")
        returns = [p for p in points if p.node_type == "Return"]
        assert len(returns) == 1
        assert returns[0].original_op == "empty_str_literal"

    def it_returns_distinct_values_for_return_types():
        """Verify each return classification gives a unique string."""
        classifications = set()
        test_cases = [
            "def f1(): return True\n",
            "def f2(): return False\n",
            "def f3(): return None\n",
            "def f4(): return 42\n",
            "def f5(): return 3.14\n",
            "def f6(): return 'hello'\n",
            "def f7(x): return -x\n",
            "def f8(x): return x\n",
            "def f9(): return 0\n",
            "def f10(): return 0.0\n",
            "def f11(): return ''\n",
        ]
        for src in test_cases:
            points = find_mutation_points(src, "test.py", "test")
            returns = [p for p in points if p.node_type == "Return"]
            assert len(returns) >= 1
            classifications.add(returns[0].original_op)
        # All 11 should have distinct classifications
        assert len(classifications) == 11


    def describe_bitwise_operators():
        def it_finds_bitand():
            source = "def f(x: int, y: int) -> int:\n    return x & y\n"
            points = find_mutation_points(source, "test.py", "test")
            binops = [p for p in points if p.node_type == "BinOp"]
            assert len(binops) == 1
            assert binops[0].original_op == "BitAnd"

        def it_finds_bitor():
            source = "def f(x: int, y: int) -> int:\n    return x | y\n"
            points = find_mutation_points(source, "test.py", "test")
            binops = [p for p in points if p.node_type == "BinOp"]
            assert len(binops) == 1
            assert binops[0].original_op == "BitOr"

        def it_finds_bitxor():
            source = "def f(x: int, y: int) -> int:\n    return x ^ y\n"
            points = find_mutation_points(source, "test.py", "test")
            binops = [p for p in points if p.node_type == "BinOp"]
            assert len(binops) == 1
            assert binops[0].original_op == "BitXor"

        def it_finds_lshift():
            source = "def f(x: int, y: int) -> int:\n    return x << y\n"
            points = find_mutation_points(source, "test.py", "test")
            binops = [p for p in points if p.node_type == "BinOp"]
            assert len(binops) == 1
            assert binops[0].original_op == "LShift"

        def it_finds_rshift():
            source = "def f(x: int, y: int) -> int:\n    return x >> y\n"
            points = find_mutation_points(source, "test.py", "test")
            binops = [p for p in points if p.node_type == "BinOp"]
            assert len(binops) == 1
            assert binops[0].original_op == "RShift"


def describe_find_mutation_points_in_file():
    def it_reads_file_and_finds_points(tmp_path):
        from pytest_leela.ast_analysis import find_mutation_points_in_file

        target = tmp_path / "sample.py"
        target.write_text("def f(x, y):\n    return x + y\n")
        points = find_mutation_points_in_file(str(target))
        assert len(points) >= 1
        # Module name should be the file stem
        assert all(p.module_name == "sample" for p in points)
