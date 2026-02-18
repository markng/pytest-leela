"""Terminal reporter and structured output for mutation testing results."""

from __future__ import annotations

import json
import os

from pytest_leela.models import MutantResult, RunResult


def _op_display(original: str, replacement: str) -> str:
    """Create a human-readable mutation description."""
    op_symbols: dict[str, str] = {
        "Add": "+", "Sub": "-", "Mult": "*", "Div": "/",
        "FloorDiv": "//", "Mod": "%", "Pow": "**",
        "Eq": "==", "NotEq": "!=", "Lt": "<", "LtE": "<=",
        "Gt": ">", "GtE": ">=", "Is": "is", "IsNot": "is not",
        "In": "in", "NotIn": "not in", "And": "and", "Or": "or",
        "USub": "-", "UAdd": "+", "Not": "not",
    }

    orig_sym = op_symbols.get(original, original)
    repl_sym = op_symbols.get(replacement, replacement)

    # Special return mutations
    if replacement in ("negate", "negate_expr"):
        return f"return x \u2192 return -x"
    if replacement == "remove_negation":
        return f"return -x \u2192 return x"
    if replacement == "empty_str":
        return f'return "..." \u2192 return ""'
    if replacement in ("True", "False", "None") and original in ("True", "False", "None", "expr"):
        return f"return {original} \u2192 return {replacement}"
    if replacement == "_remove":
        return f"{orig_sym} x \u2192 x"

    return f"{orig_sym} \u2192 {repl_sym}"


def _pct(killed: int, total: int) -> float:
    """Calculate kill percentage, defaulting to 100% when no mutants exist."""
    return killed / total * 100 if total > 0 else 100.0


def format_terminal_report(result: RunResult) -> str:
    """Format a terminal-friendly mutation testing report."""
    lines: list[str] = []

    lines.append("")
    lines.append("=" * 70)
    lines.append("leela mutation testing")
    lines.append("=" * 70)

    # Target summary
    n_files = len(result.target_files)
    file_label = "file" if n_files == 1 else "files"
    lines.append(f"Target: {n_files} {file_label}")
    lines.append(f"Mutants: {result.total_mutants} candidates, {result.mutants_pruned} pruned by type analysis")
    lines.append("")

    # Per-file results
    file_results: dict[str, list[MutantResult]] = {}
    for r in result.results:
        fp = r.mutant.point.file_path
        if fp not in file_results:
            file_results[fp] = []
        file_results[fp].append(r)

    for file_path in sorted(file_results.keys()):
        basename = os.path.basename(file_path)
        file_res = file_results[file_path]

        killed = sum(1 for r in file_res if r.killed)
        total = len(file_res)
        pct = _pct(killed, total)

        lines.append(f"  {basename:<30s} {killed}/{total} killed ({pct:.1f}%)")

        # Show survived mutants
        survived = [r for r in file_res if not r.killed]
        for r in survived:
            m = r.mutant
            desc = _op_display(m.point.original_op, m.replacement_op)
            lines.append(f"    line {m.point.lineno}: {desc:<45s} SURVIVED")

    lines.append("")

    # Overall summary
    lines.append(
        f"Overall: {result.killed}/{result.mutants_tested} killed "
        f"({result.mutation_score:.1f}%) in {result.wall_time_seconds:.1f}s"
    )
    if result.mutants_pruned > 0:
        lines.append(
            f"  ({result.total_mutants} candidates, "
            f"{result.mutants_pruned} pruned by type analysis)"
        )
    lines.append("")

    return "\n".join(lines)


def format_json_report(result: RunResult) -> str:
    """Format a JSON mutation testing report."""
    data = {
        "target_files": result.target_files,
        "total_mutants": result.total_mutants,
        "mutants_tested": result.mutants_tested,
        "mutants_pruned": result.mutants_pruned,
        "killed": result.killed,
        "survived": len(result.survived),
        "mutation_score": round(result.mutation_score, 2),
        "wall_time_seconds": round(result.wall_time_seconds, 2),
        "survived_mutants": [
            {
                "file": r.mutant.point.file_path,
                "line": r.mutant.point.lineno,
                "original": r.mutant.point.original_op,
                "replacement": r.mutant.replacement_op,
                "description": _op_display(
                    r.mutant.point.original_op, r.mutant.replacement_op
                ),
            }
            for r in result.survived
        ],
    }
    return json.dumps(data, indent=2)
