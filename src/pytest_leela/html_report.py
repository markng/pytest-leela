"""HTML report generation for mutation testing results."""

from __future__ import annotations

import ast
import json
import os
import textwrap
from datetime import datetime, timezone
from typing import Any

from pytest_leela.models import RunResult
from pytest_leela.output import _op_display


def _format_test_name(test_id: str) -> str:
    """Pretty-print a pytest node ID for human consumption.

    Splits on ``::`` and strips common test prefixes like ``test_``,
    ``describe_``, and ``it_``.  Underscores become spaces and parts are
    joined with `` > ``.

    Examples::

        "tests/test_views.py::TestHomeView::test_get" -> "TestHomeView > get"
        "tests/describe_foo.py::describe_bar::it_does_thing" -> "bar > does thing"
        "tests/test_x.py::test_simple" -> "simple"
        "tests/test_x.py::TestClass::test_method[param1]" -> "TestClass > method[param1]"
    """
    parts = test_id.split("::")
    # Drop the first part (file path)
    parts = parts[1:]

    if not parts:
        return test_id

    cleaned: list[str] = []
    for part in parts:
        # Extract parametrize brackets if present
        bracket_suffix = ""
        if "[" in part:
            bracket_idx = part.index("[")
            bracket_suffix = part[bracket_idx:]
            part = part[:bracket_idx]

        # Strip common prefixes
        for prefix in ("test_", "describe_", "it_", "context_"):
            if part.startswith(prefix):
                part = part[len(prefix):]
                break

        # Replace underscores with spaces
        part = part.replace("_", " ")

        # Reattach parametrize brackets
        part = part + bracket_suffix

        if part:
            cleaned.append(part)

    if not cleaned:
        return test_id

    return " > ".join(cleaned)


def _find_function_node(
    tree: ast.Module, containers: list[str], func_name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find a function node in an AST by traversing container scopes.

    ``containers`` is a list of class or function names to drill through
    (may be empty for top-level functions).  ``func_name`` is the target
    function name to locate within the innermost scope.
    """
    scope: ast.AST = tree
    for container_name in containers:
        found = False
        for node in ast.iter_child_nodes(scope):
            if (
                isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == container_name
            ):
                scope = node
                found = True
                break
        if not found:
            return None

    for node in ast.iter_child_nodes(scope):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == func_name
        ):
            return node
    return None


def _extract_test_sources(result: RunResult) -> dict[str, str]:
    """Extract source code for test functions referenced in the run result.

    Collects all unique test node IDs from mutant results and the coverage
    map, parses their source files, and returns a mapping from the original
    node ID to the dedented source code of that test function.
    """
    all_test_ids: set[str] = set()
    for mr in result.results:
        all_test_ids.update(mr.test_ids_run)
        all_test_ids.update(mr.killing_tests)
    if result.coverage_map is not None:
        for test_ids in result.coverage_map.line_to_tests.values():
            all_test_ids.update(test_ids)

    if not all_test_ids:
        return {}

    # Group lookups by file path so each file is read and parsed once.
    # Each entry is (containers, func_name, original_node_id).
    file_groups: dict[str, list[tuple[list[str], str, str]]] = {}
    for node_id in all_test_ids:
        parts = node_id.split("::")
        if len(parts) < 2:
            continue
        file_path = parts[0]
        last_part = parts[-1]
        func_name = last_part.split("[")[0] if "[" in last_part else last_part
        containers = list(parts[1:-1])
        if file_path not in file_groups:
            file_groups[file_path] = []
        file_groups[file_path].append((containers, func_name, node_id))

    sources: dict[str, str] = {}
    for file_path, lookups in file_groups.items():
        try:
            with open(file_path) as f:
                file_source = f.read()
            tree = ast.parse(file_source)
            file_lines = file_source.splitlines()
        except (OSError, SyntaxError):
            continue

        for containers, func_name, node_id in lookups:
            func_node = _find_function_node(tree, containers, func_name)
            if func_node is None or func_node.end_lineno is None:
                continue
            source_lines = file_lines[func_node.lineno - 1 : func_node.end_lineno]
            source = textwrap.dedent("\n".join(source_lines))
            sources[node_id] = source

    return sources


def _build_report_data(result: RunResult) -> dict[str, Any]:
    """Build a JSON-serializable data structure from a RunResult.

    Returns a dict conforming to the Leela HTML report schema (version 1).
    """
    # Determine common prefix for relative paths
    all_file_paths = list(result.target_files)
    if len(all_file_paths) > 1:
        common = os.path.commonpath(all_file_paths)
    elif len(all_file_paths) == 1:
        common = os.path.dirname(all_file_paths[0])
    else:
        common = ""

    def _relpath(fp: str) -> str:
        if not common:
            return fp
        return os.path.relpath(fp, common)

    # Group mutant results by file
    file_results: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for idx, mr in enumerate(result.results):
        fp = mr.mutant.point.file_path
        rel = _relpath(fp)
        if rel not in file_results:
            file_results[rel] = []

        desc = _op_display(mr.mutant.point.original_op, mr.mutant.replacement_op)
        original_disp, replacement_disp = desc.split(" \u2192 ", 1)

        mutant_data = {
            "id": idx,
            "lineno": mr.mutant.point.lineno,
            "col_offset": mr.mutant.point.col_offset,
            "node_type": mr.mutant.point.node_type,
            "original": original_disp,
            "replacement": replacement_disp,
            "description": desc,
            "killed": mr.killed,
            "tests_run": mr.tests_run,
            "time_seconds": mr.time_seconds,
            "killing_test": (
                {"display": _format_test_name(mr.killing_test), "id": mr.killing_test}
                if mr.killing_test
                else None
            ),
            "killing_tests": [
                {"display": _format_test_name(t), "id": t} for t in mr.killing_tests
            ],
            "test_ids_run": [
                {"display": _format_test_name(t), "id": t} for t in mr.test_ids_run
            ],
        }
        file_results[rel].append((idx, mutant_data))

    # Build files dict
    files: dict[str, dict[str, Any]] = {}
    for fp in result.target_files:
        rel = _relpath(fp)
        source = result.target_sources.get(fp, "")

        # Build coverage lines
        lines: dict[str, dict[str, Any]] = {}
        if result.coverage_map is not None:
            for (cov_fp, lineno), test_ids in result.coverage_map.line_to_tests.items():
                if cov_fp == fp:
                    formatted = sorted(
                        ({"display": _format_test_name(t), "id": t} for t in test_ids),
                        key=lambda x: x["display"],
                    )
                    lines[str(lineno)] = {"coverage": formatted}

        # Collect mutants for this file
        mutants_for_file = file_results.get(rel, [])
        mutant_list = [m for _, m in mutants_for_file]

        # Per-file stats
        total = len(mutant_list)
        killed = sum(1 for m in mutant_list if m["killed"])
        survived = total - killed
        score = (killed / total * 100.0) if total > 0 else 0.0

        files[rel] = {
            "source": source,
            "lines": lines,
            "mutants": mutant_list,
            "stats": {
                "total": total,
                "killed": killed,
                "survived": survived,
                "score": round(score, 2),
            },
        }

    # Build survived index sorted by file then line number
    survived_index: list[dict[str, Any]] = []
    for rel in sorted(files.keys()):
        for midx, m in enumerate(files[rel]["mutants"]):
            if not m["killed"]:
                survived_index.append({
                    "file": rel,
                    "lineno": m["lineno"],
                    "mutant_idx": midx,
                    "description": m["description"],
                })
    survived_index.sort(key=lambda x: (x["file"], x["lineno"]))

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_mutants": result.total_mutants,
            "mutants_tested": result.mutants_tested,
            "mutants_pruned": result.mutants_pruned,
            "killed": result.killed,
            "survived": len(result.survived),
            "mutation_score": round(result.mutation_score, 2),
            "wall_time_seconds": result.wall_time_seconds,
        },
        "files": files,
        "survived_index": survived_index,
        "test_sources": _extract_test_sources(result),
    }


def _escape_json_for_html(json_str: str) -> str:
    """Escape JSON for safe embedding in HTML script tags.

    Replaces ``</`` with ``<\\/`` to prevent premature script tag closure.
    ``\\/`` is valid JSON (RFC 8259 section 7) and evaluates to ``/`` at runtime.
    """
    return json_str.replace("</", "<\\/")


def _build_html_viewer(json_data: str) -> str:
    """Build a full interactive HTML viewer with inline JSON data.

    Returns a self-contained HTML string with inline CSS, JS, and data.
    The viewer renders a three-panel layout (sidebar / source / detail)
    with survivor navigation, syntax highlighting, and a dark
    Catppuccin-Mocha theme.
    """
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Leela Mutation Report</title>
<style>
*, *::before, *::after {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}}
html, body {{
    height: 100%;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #1e1e2e;
    color: #cdd6f4;
    overflow: hidden;
    min-width: 1000px;
}}
/* --- Header --- */
#header {{
    display: flex;
    align-items: center;
    gap: 12px;
    background: #181825;
    border-bottom: 1px solid #45475a;
    padding: 8px 16px;
    height: 48px;
    flex-shrink: 0;
}}
#header .logo {{
    font-weight: 700;
    font-size: 15px;
    color: #cdd6f4;
    margin-right: 4px;
}}
.score-badge {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-weight: 700;
    font-size: 14px;
    line-height: 1.5;
}}
.score-green  {{ background: #a6e3a1; color: #1e1e2e; }}
.score-yellow {{ background: #f9e2af; color: #1e1e2e; }}
.score-red    {{ background: #f38ba8; color: #1e1e2e; }}
#survived-count {{
    font-size: 14px;
    color: #f38ba8;
    font-weight: 600;
}}
.hdr-btn {{
    background: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 4px 12px;
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
}}
.hdr-btn:hover {{ background: #45475a; }}
.hdr-spacer {{ flex: 1; }}
.hdr-help {{
    font-size: 12px;
    color: #6c7086;
}}
/* --- Main Layout --- */
#main {{
    display: flex;
    height: calc(100% - 48px - 24px);
}}
/* --- Sidebar --- */
#sidebar {{
    width: 250px;
    min-width: 250px;
    background: #181825;
    border-right: 1px solid #45475a;
    overflow-y: auto;
    flex-shrink: 0;
}}
#sidebar .sidebar-title {{
    padding: 10px 12px 6px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #6c7086;
    font-weight: 600;
}}
.file-entry {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    cursor: pointer;
    font-size: 13px;
    border-left: 3px solid transparent;
    transition: background 0.15s;
}}
.file-entry:hover {{ background: #313244; }}
.file-entry.active {{
    background: #313244;
    border-left-color: #89b4fa;
}}
.file-entry .fname {{
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: #cdd6f4;
}}
.file-entry .fstats {{
    font-size: 11px;
    color: #a6adc8;
    white-space: nowrap;
}}
.file-badge {{
    display: inline-block;
    padding: 1px 6px;
    border-radius: 8px;
    font-size: 11px;
    font-weight: 700;
    min-width: 36px;
    text-align: center;
}}
/* --- Source View --- */
#source-panel {{
    flex: 1;
    overflow: auto;
    background: #1e1e2e;
}}
#source-table {{
    border-collapse: collapse;
    width: 100%;
    font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, "Courier New", monospace;
    font-size: 13px;
    line-height: 1.55;
}}
#source-table tr {{
    cursor: pointer;
    transition: background 0.1s;
}}
#source-table tr:hover {{ background: #313244; }}
#source-table tr.survived-line {{ background: rgba(243, 139, 168, 0.08); }}
#source-table tr.selected-line {{ background: rgba(137, 180, 250, 0.15) !important; }}
#source-table td {{
    padding: 0 8px;
    white-space: pre;
    vertical-align: top;
}}
.ln {{
    color: #6c7086;
    text-align: right;
    user-select: none;
    width: 1px;
    padding-right: 12px !important;
    border-right: 1px solid #45475a;
}}
.cov-cell {{
    width: 16px;
    text-align: center;
    padding: 0 4px !important;
}}
.cov-dot {{
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
}}
.cov-low  {{ background: rgba(137, 180, 250, 0.4); }}
.cov-med  {{ background: rgba(137, 180, 250, 0.7); }}
.cov-high {{ background: rgba(137, 180, 250, 1.0); }}
.mut-cell {{
    width: 40px;
    text-align: center;
    padding: 0 2px !important;
    font-size: 12px;
    white-space: nowrap;
}}
.mut-killed  {{ color: #a6e3a1; }}
.mut-survived {{ color: #f38ba8; font-weight: 700; }}
.code-cell {{
    padding-left: 12px !important;
}}
/* Syntax colors */
.syn-kw  {{ color: #cba6f7; }}
.syn-str {{ color: #a6e3a1; }}
.syn-com {{ color: #6c7086; font-style: italic; }}
.syn-num {{ color: #fab387; }}
.syn-dec {{ color: #f9e2af; }}
.syn-bi  {{ color: #89dceb; }}
/* --- Detail Panel --- */
#detail-panel {{
    width: 320px;
    min-width: 320px;
    background: #181825;
    border-left: 1px solid #45475a;
    overflow-y: auto;
    flex-shrink: 0;
    padding: 12px;
    font-size: 13px;
}}
#detail-panel.empty {{
    display: flex;
    align-items: center;
    justify-content: center;
    color: #6c7086;
}}
.detail-section {{
    margin-bottom: 16px;
}}
.detail-section h3 {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: #6c7086;
    margin-bottom: 6px;
    font-weight: 600;
}}
.detail-test {{
    padding: 3px 0;
    color: #a6adc8;
    font-size: 12px;
    word-break: break-all;
}}
.mutant-card {{
    background: #313244;
    border-radius: 6px;
    padding: 8px 10px;
    margin-bottom: 8px;
    border-left: 3px solid #a6e3a1;
}}
.mutant-card.survived {{
    border-left-color: #f38ba8;
    background: rgba(243, 139, 168, 0.08);
}}
.mutant-card .mc-header {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
}}
.status-badge {{
    display: inline-block;
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
}}
.status-killed   {{ background: #a6e3a1; color: #1e1e2e; }}
.status-survived {{ background: #f38ba8; color: #1e1e2e; }}
.mc-desc {{
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 13px;
    color: #cdd6f4;
}}
.mc-meta {{
    font-size: 11px;
    color: #6c7086;
    margin-top: 4px;
}}
.mc-tests {{
    margin-top: 6px;
}}
.mc-tests .mc-label {{
    font-size: 11px;
    color: #6c7086;
    margin-bottom: 2px;
}}
/* --- Survivor Overlay --- */
#overlay {{
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(17, 17, 27, 0.85);
    z-index: 100;
    align-items: center;
    justify-content: center;
}}
#overlay.visible {{
    display: flex;
}}
#overlay-box {{
    background: #313244;
    border: 1px solid #45475a;
    border-radius: 10px;
    width: 600px;
    max-height: 70vh;
    display: flex;
    flex-direction: column;
}}
#overlay-header {{
    display: flex;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid #45475a;
}}
#overlay-header h2 {{
    flex: 1;
    font-size: 15px;
    font-weight: 600;
}}
#overlay-close {{
    background: none;
    border: none;
    color: #a6adc8;
    font-size: 20px;
    cursor: pointer;
    padding: 4px 8px;
}}
#overlay-close:hover {{ color: #cdd6f4; }}
#overlay-list {{
    overflow-y: auto;
    padding: 8px 0;
}}
.overlay-group-header {{
    padding: 8px 16px 4px;
    font-size: 12px;
    font-weight: 600;
    color: #89b4fa;
}}
.overlay-item {{
    display: flex;
    gap: 10px;
    padding: 6px 16px;
    cursor: pointer;
    font-size: 13px;
    transition: background 0.1s;
}}
.overlay-item:hover {{ background: #45475a; }}
.overlay-item.current {{ background: rgba(137, 180, 250, 0.15); }}
.overlay-item .oi-line {{
    color: #6c7086;
    min-width: 50px;
}}
.overlay-item .oi-desc {{
    color: #f38ba8;
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 12px;
}}
/* --- Test Link --- */
.test-link {{
    color: #89b4fa;
    cursor: pointer;
}}
.test-link:hover {{
    text-decoration: underline;
}}
/* --- Test Overlay --- */
#test-overlay {{
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(17, 17, 27, 0.85);
    z-index: 100;
    align-items: center;
    justify-content: center;
}}
#test-overlay.visible {{
    display: flex;
}}
#test-overlay-box {{
    background: #313244;
    border: 1px solid #45475a;
    border-radius: 10px;
    width: 700px;
    max-height: 80vh;
    display: flex;
    flex-direction: column;
}}
#test-overlay-header {{
    display: flex;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid #45475a;
    gap: 8px;
}}
#test-overlay-header h2 {{
    font-size: 15px;
    font-weight: 600;
    word-break: break-all;
}}
#test-overlay-header .to-file {{
    flex: 1;
    font-size: 12px;
    color: #6c7086;
    text-align: right;
}}
#test-overlay-close {{
    background: none;
    border: none;
    color: #a6adc8;
    font-size: 20px;
    cursor: pointer;
    padding: 4px 8px;
}}
#test-overlay-close:hover {{ color: #cdd6f4; }}
#test-overlay-body {{
    overflow-y: auto;
    flex: 1;
}}
#test-overlay-source table {{
    border-collapse: collapse;
    width: 100%;
    font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, "Courier New", monospace;
    font-size: 13px;
    line-height: 1.55;
}}
#test-overlay-source td {{
    padding: 0 8px;
    white-space: pre;
    vertical-align: top;
}}
#test-overlay-footer {{
    border-top: 1px solid #45475a;
    padding: 10px 16px;
    font-size: 12px;
    max-height: 30vh;
    overflow-y: auto;
}}
.to-section {{
    margin-bottom: 8px;
}}
.to-section:last-child {{
    margin-bottom: 0;
}}
.to-label {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: #6c7086;
    font-weight: 600;
    margin-bottom: 2px;
}}
.to-mutant {{
    color: #a6e3a1;
    padding: 1px 0;
    font-size: 12px;
}}
.to-lines {{
    color: #a6adc8;
    font-size: 12px;
    padding: 1px 0;
}}
#test-overlay-nav {{
    display: flex;
    gap: 8px;
    align-items: center;
    border-top: 1px solid #45475a;
    padding: 8px 16px;
}}
.to-pos {{
    flex: 1;
    font-size: 12px;
    color: #6c7086;
    text-align: center;
}}
/* --- Footer --- */
#footer {{
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #181825;
    border-top: 1px solid #45475a;
    font-size: 11px;
    color: #6c7086;
    flex-shrink: 0;
}}
</style>
</head>
<body>
<!-- Header -->
<div id="header">
    <span class="logo">Leela</span>
    <span id="score-badge" class="score-badge"></span>
    <span id="survived-count"></span>
    <span class="hdr-spacer"></span>
    <button class="hdr-btn" id="btn-prev" title="Previous survivor (p)">&lt; Prev</button>
    <button class="hdr-btn" id="btn-next" title="Next survivor (n)">Next &gt;</button>
    <button class="hdr-btn" id="btn-list" title="Survivor list (l)">View List</button>
    <span class="hdr-help">n/p: next/prev &middot; l: list &middot; Esc: close</span>
</div>
<!-- Main layout -->
<div id="main">
    <div id="sidebar">
        <div class="sidebar-title">Files</div>
        <div id="file-list"></div>
    </div>
    <div id="source-panel">
        <table id="source-table"><tbody id="source-body"></tbody></table>
    </div>
    <div id="detail-panel" class="empty">Click a line to see details</div>
</div>
<!-- Survivor overlay -->
<div id="overlay">
    <div id="overlay-box">
        <div id="overlay-header">
            <h2>Survived Mutants</h2>
            <button id="overlay-close">&times;</button>
        </div>
        <div id="overlay-list"></div>
    </div>
</div>
<!-- Test overlay -->
<div id="test-overlay">
    <div id="test-overlay-box">
        <div id="test-overlay-header">
            <h2 id="test-overlay-name"></h2>
            <span class="to-file" id="test-overlay-file"></span>
            <button id="test-overlay-close">&times;</button>
        </div>
        <div id="test-overlay-body">
            <div id="test-overlay-source"></div>
        </div>
        <div id="test-overlay-footer"></div>
        <div id="test-overlay-nav">
            <button class="hdr-btn" id="btn-test-prev">&lt; Prev</button>
            <span id="test-overlay-pos" class="to-pos"></span>
            <button class="hdr-btn" id="btn-test-next">Next &gt;</button>
        </div>
    </div>
</div>
<!-- Footer -->
<div id="footer">Generated by pytest-leela</div>
<script>
window.LEELA_DATA = {json_data};
(function() {{
    "use strict";
    var D = window.LEELA_DATA;
    var summary = D.summary;
    var files = D.files;
    var survivedIndex = D.survived_index;
    var testSources = D.test_sources || {{}};
    var testIndex = {{}};
    var allTestIds = [];
    var testOverlayPos = -1;
    var currentFile = null;
    var selectedLine = null;
    var survivorPos = -1;

    /* --- Helpers --- */
    function esc(s) {{
        var d = document.createElement("div");
        d.appendChild(document.createTextNode(s));
        return d.innerHTML;
    }}

    function scoreClass(score) {{
        if (score >= 80) return "score-green";
        if (score >= 60) return "score-yellow";
        return "score-red";
    }}

    function fileBadgeColor(score) {{
        return scoreClass(score);
    }}

    /* --- Syntax Highlighting --- */
    function highlight(code) {{
        var tokens = [];
        var i = 0;
        var src = code;
        var kwSet = new Set([
            "def","class","return","if","else","elif","for","while","import","from",
            "try","except","finally","with","as","raise","yield","pass","break",
            "continue","and","or","not","in","is","None","True","False","lambda",
            "assert","del","global","nonlocal","async","await"
        ]);
        var biSet = new Set([
            "self","cls","print","len","range","enumerate","zip","map","filter",
            "isinstance","type","super","property","staticmethod","classmethod"
        ]);
        while (i < src.length) {{
            var ch = src[i];
            // Comments
            if (ch === "#") {{
                tokens.push('<span class="syn-com">' + esc(src.slice(i)) + "</span>");
                break;
            }}
            // Triple-quoted strings
            if ((ch === '"' || ch === "'") && src.slice(i, i + 3) === ch + ch + ch) {{
                var q3 = ch + ch + ch;
                var end3 = src.indexOf(q3, i + 3);
                if (end3 === -1) end3 = src.length - 3;
                var s3 = src.slice(i, end3 + 3);
                tokens.push('<span class="syn-str">' + esc(s3) + "</span>");
                i = end3 + 3;
                continue;
            }}
            // Strings
            if (ch === '"' || ch === "'") {{
                var j = i + 1;
                while (j < src.length) {{
                    if (src[j] === "\\\\") {{ j += 2; continue; }}
                    if (src[j] === ch) {{ j++; break; }}
                    j++;
                }}
                tokens.push('<span class="syn-str">' + esc(src.slice(i, j)) + "</span>");
                i = j;
                continue;
            }}
            // Decorators
            if (ch === "@" && (i === 0 || /\\s/.test(src[i - 1]))) {{
                var m = src.slice(i).match(/^@[\\w.]+/);
                if (m) {{
                    tokens.push('<span class="syn-dec">' + esc(m[0]) + "</span>");
                    i += m[0].length;
                    continue;
                }}
            }}
            // Numbers
            if (/[0-9]/.test(ch) && (i === 0 || !/[\\w]/.test(src[i - 1]))) {{
                var nm = src.slice(i).match(/^(\\d+\\.?\\d*([eE][+-]?\\d+)?|0[xXoObB][\\da-fA-F_]+)/);
                if (nm) {{
                    tokens.push('<span class="syn-num">' + esc(nm[0]) + "</span>");
                    i += nm[0].length;
                    continue;
                }}
            }}
            // Words (keywords / builtins / identifiers)
            if (/[a-zA-Z_]/.test(ch)) {{
                var wm = src.slice(i).match(/^[a-zA-Z_]\\w*/);
                if (wm) {{
                    var w = wm[0];
                    if (kwSet.has(w)) {{
                        tokens.push('<span class="syn-kw">' + esc(w) + "</span>");
                    }} else if (biSet.has(w)) {{
                        tokens.push('<span class="syn-bi">' + esc(w) + "</span>");
                    }} else {{
                        tokens.push(esc(w));
                    }}
                    i += w.length;
                    continue;
                }}
            }}
            // Default character
            tokens.push(esc(ch));
            i++;
        }}
        return tokens.join("");
    }}

    /* --- Header --- */
    function renderHeader() {{
        var scoreVal = summary.mutation_score;
        var badge = document.getElementById("score-badge");
        badge.textContent = scoreVal.toFixed(1) + "%";
        badge.className = "score-badge " + scoreClass(scoreVal);
        var sc = document.getElementById("survived-count");
        sc.textContent = summary.survived + " survived";
        if (summary.survived === 0) sc.style.color = "#a6e3a1";
    }}

    /* --- Sidebar --- */
    function renderSidebar() {{
        var list = document.getElementById("file-list");
        var sortedFiles = Object.keys(files).sort();
        var html = "";
        sortedFiles.forEach(function(fname) {{
            var f = files[fname];
            var st = f.stats;
            var cls = fname === currentFile ? " active" : "";
            html += '<div class="file-entry' + cls + '" data-file="' + esc(fname) + '">';
            html += '<span class="fname" title="' + esc(fname) + '">' + esc(fname) + "</span>";
            html += '<span class="file-badge ' + fileBadgeColor(st.score) + '">' + st.score.toFixed(0) + "%</span>";
            html += '<span class="fstats">' + st.killed + "/" + st.total + "</span>";
            html += "</div>";
        }});
        list.innerHTML = html;
        list.querySelectorAll(".file-entry").forEach(function(el) {{
            el.addEventListener("click", function() {{
                loadFile(el.getAttribute("data-file"));
            }});
        }});
    }}

    /* --- Source View --- */
    function loadFile(fname) {{
        if (!files[fname]) return;
        currentFile = fname;
        selectedLine = null;
        renderSidebar();
        renderSource();
        renderDetail();
    }}

    function renderSource() {{
        var f = files[currentFile];
        var lines = f.source.split("\\n");
        // Remove trailing empty line from trailing newline
        if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
        // Build line->mutant info
        var lineMutants = {{}};
        f.mutants.forEach(function(m) {{
            if (!lineMutants[m.lineno]) lineMutants[m.lineno] = [];
            lineMutants[m.lineno].push(m);
        }});
        var linesCov = f.lines;
        var tbody = document.getElementById("source-body");
        var html = "";
        lines.forEach(function(line, idx) {{
            var ln = idx + 1;
            var lnStr = String(ln);
            // Survived?
            var hasSurvived = false;
            var hasKilled = false;
            var mutCount = 0;
            if (lineMutants[ln]) {{
                lineMutants[ln].forEach(function(m) {{
                    if (m.killed) hasKilled = true;
                    else hasSurvived = true;
                    mutCount++;
                }});
            }}
            var trClass = hasSurvived ? "survived-line" : "";
            html += '<tr class="' + trClass + '" data-ln="' + ln + '">';
            // Line number
            html += '<td class="ln">' + ln + "</td>";
            // Coverage dot
            var covInfo = linesCov[lnStr];
            var covHtml = "";
            if (covInfo && covInfo.coverage && covInfo.coverage.length > 0) {{
                var cnt = covInfo.coverage.length;
                var dotCls = cnt <= 2 ? "cov-low" : (cnt <= 5 ? "cov-med" : "cov-high");
                covHtml = '<span class="cov-dot ' + dotCls + '" title="' + cnt + ' tests"></span>';
            }}
            html += '<td class="cov-cell">' + covHtml + "</td>";
            // Mutation indicator
            var mutHtml = "";
            if (mutCount > 0) {{
                if (hasSurvived) {{
                    var sCount = lineMutants[ln].filter(function(m) {{ return !m.killed; }}).length;
                    mutHtml = '<span class="mut-survived">' + (sCount > 1 ? "\u2717" + sCount : "\u2717") + "</span>";
                    if (hasKilled) {{
                        var kCount = lineMutants[ln].filter(function(m) {{ return m.killed; }}).length;
                        mutHtml += ' <span class="mut-killed">' + (kCount > 1 ? "\u2713" + kCount : "\u2713") + "</span>";
                    }}
                }} else {{
                    var kc = mutCount;
                    mutHtml = '<span class="mut-killed">' + (kc > 1 ? "\u2713" + kc : "\u2713") + "</span>";
                }}
            }}
            html += '<td class="mut-cell">' + mutHtml + "</td>";
            // Code
            html += '<td class="code-cell">' + highlight(line) + "</td>";
            html += "</tr>";
        }});
        tbody.innerHTML = html;
        // Click handler
        tbody.querySelectorAll("tr").forEach(function(tr) {{
            tr.addEventListener("click", function() {{
                var ln = parseInt(tr.getAttribute("data-ln"), 10);
                selectLine(ln);
            }});
        }});
    }}

    function selectLine(ln) {{
        selectedLine = ln;
        document.querySelectorAll("#source-body tr.selected-line").forEach(function(el) {{
            el.classList.remove("selected-line");
        }});
        var row = document.querySelector('#source-body tr[data-ln="' + ln + '"]');
        if (row) row.classList.add("selected-line");
        renderDetail();
    }}

    function scrollToLine(ln) {{
        var row = document.querySelector('#source-body tr[data-ln="' + ln + '"]');
        if (row) {{
            row.scrollIntoView({{ block: "center" }});
        }}
    }}

    /* --- Detail Panel --- */
    function renderDetail() {{
        var panel = document.getElementById("detail-panel");
        if (!currentFile || !selectedLine) {{
            panel.className = "empty";
            panel.innerHTML = "Click a line to see details";
            return;
        }}
        panel.className = "";
        var f = files[currentFile];
        var lnStr = String(selectedLine);
        var html = '<div style="margin-bottom:8px;font-weight:600;color:#89b4fa;">Line ' + selectedLine + "</div>";
        // Coverage
        var covInfo = f.lines[lnStr];
        if (covInfo && covInfo.coverage && covInfo.coverage.length > 0) {{
            html += '<div class="detail-section"><h3>Coverage (' + covInfo.coverage.length + " tests)</h3>";
            covInfo.coverage.forEach(function(t) {{
                html += '<div class="detail-test"><span class="test-link" data-test-id="' + esc(t.id) + '">' + esc(t.display) + "</span></div>";
            }});
            html += "</div>";
        }} else {{
            html += '<div class="detail-section"><h3>Coverage</h3><div class="detail-test" style="color:#6c7086;">No coverage data</div></div>';
        }}
        // Mutants on this line
        var mutants = f.mutants.filter(function(m) {{ return m.lineno === selectedLine; }});
        if (mutants.length > 0) {{
            html += '<div class="detail-section"><h3>Mutants (' + mutants.length + ")</h3>";
            mutants.forEach(function(m) {{
                var sclass = m.killed ? "" : " survived";
                html += '<div class="mutant-card' + sclass + '">';
                html += '<div class="mc-header">';
                if (m.killed) {{
                    html += '<span class="status-badge status-killed">Killed</span>';
                }} else {{
                    html += '<span class="status-badge status-survived">Survived</span>';
                }}
                html += '<span class="mc-desc">' + esc(m.description) + "</span>";
                html += "</div>";
                html += '<div class="mc-meta">' + esc(m.node_type) + " col " + m.col_offset + " &middot; " + (m.time_seconds * 1000).toFixed(0) + "ms</div>";
                if (m.killed && m.killing_tests && m.killing_tests.length > 0) {{
                    html += '<div class="mc-tests"><div class="mc-label">Killing tests:</div>';
                    m.killing_tests.forEach(function(t) {{
                        html += '<div class="detail-test"><span class="test-link" data-test-id="' + esc(t.id) + '" style="color:#a6e3a1;">' + esc(t.display) + "</span></div>";
                    }});
                    html += "</div>";
                }}
                if (m.test_ids_run && m.test_ids_run.length > 0) {{
                    html += '<div class="mc-tests"><div class="mc-label">Tests run (' + m.tests_run + "):</div>";
                    m.test_ids_run.forEach(function(t) {{
                        html += '<div class="detail-test"><span class="test-link" data-test-id="' + esc(t.id) + '">' + esc(t.display) + "</span></div>";
                    }});
                    html += "</div>";
                }}
                html += "</div>";
            }});
            html += "</div>";
        }}
        panel.innerHTML = html;
    }}

    /* --- Survivor Navigation --- */
    function navigateSurvivor(pos) {{
        if (survivedIndex.length === 0) return;
        if (pos < 0) pos = survivedIndex.length - 1;
        if (pos >= survivedIndex.length) pos = 0;
        survivorPos = pos;
        var s = survivedIndex[survivorPos];
        if (currentFile !== s.file) loadFile(s.file);
        selectLine(s.lineno);
        scrollToLine(s.lineno);
        renderOverlayHighlight();
    }}

    function nextSurvivor() {{ navigateSurvivor(survivorPos + 1); }}
    function prevSurvivor() {{ navigateSurvivor(survivorPos - 1); }}

    /* --- Overlay --- */
    function renderOverlay() {{
        var list = document.getElementById("overlay-list");
        var html = "";
        var lastFile = null;
        survivedIndex.forEach(function(s, idx) {{
            if (s.file !== lastFile) {{
                lastFile = s.file;
                html += '<div class="overlay-group-header">' + esc(s.file) + "</div>";
            }}
            var cls = idx === survivorPos ? " current" : "";
            html += '<div class="overlay-item' + cls + '" data-idx="' + idx + '">';
            html += '<span class="oi-line">L' + s.lineno + "</span>";
            html += '<span class="oi-desc">' + esc(s.description) + "</span>";
            html += "</div>";
        }});
        list.innerHTML = html;
        list.querySelectorAll(".overlay-item").forEach(function(el) {{
            el.addEventListener("click", function() {{
                var idx = parseInt(el.getAttribute("data-idx"), 10);
                toggleOverlay(false);
                navigateSurvivor(idx);
            }});
        }});
    }}

    function renderOverlayHighlight() {{
        document.querySelectorAll(".overlay-item.current").forEach(function(el) {{
            el.classList.remove("current");
        }});
        var el = document.querySelector('.overlay-item[data-idx="' + survivorPos + '"]');
        if (el) el.classList.add("current");
    }}

    function toggleOverlay(show) {{
        var ov = document.getElementById("overlay");
        if (show === undefined) show = !ov.classList.contains("visible");
        if (show) {{
            renderOverlay();
            ov.classList.add("visible");
        }} else {{
            ov.classList.remove("visible");
        }}
    }}

    /* --- Test Index --- */
    function buildTestIndex() {{
        var idSet = {{}};
        Object.keys(files).forEach(function(fname) {{
            var f = files[fname];
            f.mutants.forEach(function(m) {{
                if (m.killing_tests) m.killing_tests.forEach(function(t) {{
                    idSet[t.id] = true;
                    if (!testIndex[t.id]) testIndex[t.id] = {{mutants_killed: [], lines_covered: {{}}}};
                    testIndex[t.id].mutants_killed.push({{file: fname, lineno: m.lineno, description: m.description}});
                }});
                if (m.test_ids_run) m.test_ids_run.forEach(function(t) {{ idSet[t.id] = true; }});
            }});
            Object.keys(f.lines).forEach(function(lnStr) {{
                var covInfo = f.lines[lnStr];
                if (covInfo && covInfo.coverage) {{
                    covInfo.coverage.forEach(function(t) {{
                        idSet[t.id] = true;
                        if (!testIndex[t.id]) testIndex[t.id] = {{mutants_killed: [], lines_covered: {{}}}};
                        if (!testIndex[t.id].lines_covered[fname]) testIndex[t.id].lines_covered[fname] = [];
                        testIndex[t.id].lines_covered[fname].push(parseInt(lnStr, 10));
                    }});
                }}
            }});
        }});
        allTestIds = Object.keys(idSet).sort();
    }}

    /* --- Test Overlay --- */
    function showTestOverlay(testId) {{
        toggleOverlay(false);
        var source = testSources[testId];
        var info = testIndex[testId] || {{mutants_killed: [], lines_covered: {{}}}};
        var pos = allTestIds.indexOf(testId);
        testOverlayPos = pos;

        var parts = testId.split("::");
        var filePath = parts[0];
        var testName = parts.slice(1).join(" :: ");
        document.getElementById("test-overlay-name").textContent = testName;
        document.getElementById("test-overlay-file").textContent = filePath;

        var sourceEl = document.getElementById("test-overlay-source");
        if (source) {{
            var lines = source.split("\\n");
            var shtml = "<table><tbody>";
            lines.forEach(function(line, idx) {{
                shtml += '<tr><td class="ln">' + (idx + 1) + '</td><td class="code-cell">' + highlight(line) + "</td></tr>";
            }});
            shtml += "</tbody></table>";
            sourceEl.innerHTML = shtml;
        }} else {{
            sourceEl.innerHTML = '<div style="padding:16px;color:#6c7086;">Source not available</div>';
        }}

        var footerEl = document.getElementById("test-overlay-footer");
        var fhtml = "";
        if (info.mutants_killed.length > 0) {{
            fhtml += '<div class="to-section"><div class="to-label">Kills ' + info.mutants_killed.length + " mutant(s)</div>";
            info.mutants_killed.forEach(function(mk) {{
                fhtml += '<div class="to-mutant">' + esc(mk.file) + ":" + mk.lineno + " " + esc(mk.description) + "</div>";
            }});
            fhtml += "</div>";
        }}
        var covFiles = Object.keys(info.lines_covered).sort();
        if (covFiles.length > 0) {{
            fhtml += '<div class="to-section"><div class="to-label">Covers lines</div>';
            covFiles.forEach(function(cf) {{
                var lineNums = info.lines_covered[cf].sort(function(a, b) {{ return a - b; }});
                fhtml += '<div class="to-lines">' + esc(cf) + ": " + lineNums.join(", ") + "</div>";
            }});
            fhtml += "</div>";
        }}
        footerEl.innerHTML = fhtml;

        document.getElementById("test-overlay-pos").textContent = (pos + 1) + " / " + allTestIds.length;
        document.getElementById("test-overlay").classList.add("visible");
    }}

    function closeTestOverlay() {{
        document.getElementById("test-overlay").classList.remove("visible");
    }}

    function nextTest() {{
        if (allTestIds.length === 0) return;
        var pos = testOverlayPos + 1;
        if (pos >= allTestIds.length) pos = 0;
        showTestOverlay(allTestIds[pos]);
    }}

    function prevTest() {{
        if (allTestIds.length === 0) return;
        var pos = testOverlayPos - 1;
        if (pos < 0) pos = allTestIds.length - 1;
        showTestOverlay(allTestIds[pos]);
    }}

    /* --- Event Binding --- */
    document.getElementById("btn-next").addEventListener("click", nextSurvivor);
    document.getElementById("btn-prev").addEventListener("click", prevSurvivor);
    document.getElementById("btn-list").addEventListener("click", function() {{ toggleOverlay(); }});
    document.getElementById("overlay-close").addEventListener("click", function() {{ toggleOverlay(false); }});
    document.getElementById("overlay").addEventListener("click", function(e) {{
        if (e.target === this) toggleOverlay(false);
    }});
    document.getElementById("btn-test-prev").addEventListener("click", prevTest);
    document.getElementById("btn-test-next").addEventListener("click", nextTest);
    document.getElementById("test-overlay-close").addEventListener("click", closeTestOverlay);
    document.getElementById("test-overlay").addEventListener("click", function(e) {{
        if (e.target === this) closeTestOverlay();
    }});
    document.addEventListener("click", function(e) {{
        var link = e.target.closest(".test-link");
        if (link) {{
            e.stopPropagation();
            var testId = link.getAttribute("data-test-id");
            if (testId) showTestOverlay(testId);
        }}
    }});
    document.addEventListener("keydown", function(e) {{
        if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
        var testOvVisible = document.getElementById("test-overlay").classList.contains("visible");
        var survivorOvVisible = document.getElementById("overlay").classList.contains("visible");
        if (e.key === "Escape") {{
            if (testOvVisible) closeTestOverlay();
            else if (survivorOvVisible) toggleOverlay(false);
        }} else if (testOvVisible) {{
            if (e.key === "ArrowRight" || e.key === "n") nextTest();
            else if (e.key === "ArrowLeft" || e.key === "p") prevTest();
        }} else {{
            if (e.key === "n") nextSurvivor();
            else if (e.key === "p") prevSurvivor();
            else if (e.key === "l") toggleOverlay();
        }}
    }});

    /* --- Init --- */
    buildTestIndex();
    renderHeader();
    var sortedFileNames = Object.keys(files).sort();
    if (sortedFileNames.length > 0) loadFile(sortedFileNames[0]);
    renderSidebar();
}})();
</script>
</body>
</html>
"""


def generate_html_report(result: RunResult, output_path: str) -> None:
    """Generate a single self-contained HTML mutation testing report."""
    data = _build_report_data(result)
    json_str = json.dumps(data)
    escaped = _escape_json_for_html(json_str)
    html_content = _build_html_viewer(escaped)
    with open(output_path, "w") as f:
        f.write(html_content)
