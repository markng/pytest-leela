"""Find unambiguous descendants of expression-removing mutations."""

from __future__ import annotations

import ast
from collections import defaultdict

from pytest_leela.models import Mutant


def removal_descendants(
    mutants: list[Mutant],
    target_sources: dict[str, str],
    module_to_file: dict[str, str],
) -> dict[int, set[int]]:
    """Map eligible return removals to candidates inside the erased expression.

    The current loader keys by start coordinate and node kind, even across
    target modules. Do not use an ambiguous selector on either end of this
    relation. AST membership, not coordinate ranges, proves containment.
    """
    if not any(
        m.point.node_type == "Return" and m.point.original_op == "expr"
        and m.replacement_op == "None" for m in mutants
    ):
        return {}

    selectors: dict[tuple[int, int, str], list[tuple[str, ast.AST]]] = defaultdict(list)
    for module, source in target_sources.items():
        for node in ast.walk(ast.parse(source, filename=module_to_file[module])):
            if isinstance(node, (ast.expr, ast.stmt, ast.ExceptHandler)):
                selectors[(node.lineno, node.col_offset, type(node).__name__)].append(
                    (module, node)
                )

    candidates: dict[ast.AST, list[int]] = defaultdict(list)
    removals: list[tuple[int, ast.expr]] = []
    for mutant in mutants:
        point = mutant.point
        matches = selectors[(point.lineno, point.col_offset, point.node_type)]
        if len(matches) != 1:
            continue
        module, node = matches[0]
        if module != point.module_name or module_to_file[module] != point.file_path:
            continue

        candidates[node].append(mutant.mutant_id)
        if (
            isinstance(node, ast.Return)
            and node.value is not None
            and point.original_op == "expr"
            and mutant.replacement_op == "None"
        ):
            removals.append((mutant.mutant_id, node.value))

    descendants = {}
    for parent, expression in removals:
        erased = {
            child
            for node in ast.walk(expression)
            for child in candidates.get(node, ())
        }
        if erased:
            descendants[parent] = erased
    return descendants
