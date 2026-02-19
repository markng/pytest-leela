"""Tests for pytest_leela.html_report — HTML report generation."""

from __future__ import annotations

import json
import os

from pytest_leela.html_report import (
    _build_html_viewer,
    _build_report_data,
    _escape_json_for_html,
    _format_test_name,
    generate_html_report,
)
from pytest_leela.models import (
    CoverageMap,
    Mutant,
    MutantResult,
    MutationPoint,
    RunResult,
)


def _make_point(**overrides) -> MutationPoint:
    defaults = dict(
        file_path="/src/app.py",
        module_name="app",
        lineno=10,
        col_offset=4,
        node_type="BinOp",
        original_op="Add",
        inferred_type=None,
    )
    defaults.update(overrides)
    return MutationPoint(**defaults)


def _make_mutant_result(
    killed: bool,
    mutant_id: int = 1,
    **point_overrides,
) -> MutantResult:
    point = _make_point(**point_overrides)
    mutant = Mutant(point=point, replacement_op="Sub", mutant_id=mutant_id)
    return MutantResult(
        mutant=mutant,
        killed=killed,
        tests_run=3,
        killing_test="tests/test_app.py::test_add" if killed else None,
        time_seconds=0.05,
        test_ids_run=["tests/test_app.py::test_add", "tests/test_app.py::test_sub"],
        killing_tests=["tests/test_app.py::test_add"] if killed else [],
    )


def _make_run_result(
    results: list[MutantResult] | None = None,
    coverage_map: CoverageMap | None = None,
    target_files: list[str] | None = None,
    target_sources: dict[str, str] | None = None,
    total_mutants: int | None = None,
    mutants_tested: int | None = None,
    mutants_pruned: int = 0,
    wall_time_seconds: float = 1.5,
) -> RunResult:
    if results is None:
        results = [_make_mutant_result(True, 1), _make_mutant_result(False, 2)]
    if target_files is None:
        target_files = ["/src/app.py"]
    if target_sources is None:
        target_sources = {"/src/app.py": "def add(a, b):\n    return a + b\n"}
    if total_mutants is None:
        total_mutants = len(results)
    if mutants_tested is None:
        mutants_tested = len(results)
    return RunResult(
        target_files=target_files,
        total_mutants=total_mutants,
        mutants_tested=mutants_tested,
        mutants_pruned=mutants_pruned,
        results=results,
        wall_time_seconds=wall_time_seconds,
        coverage_map=coverage_map,
        target_sources=target_sources,
    )


def describe_format_test_name():
    def it_strips_file_path_prefix():
        result = _format_test_name("tests/test_views.py::TestHomeView::test_get")
        assert "tests/test_views.py" not in result

    def it_strips_test_and_describe_prefixes():
        assert _format_test_name(
            "tests/test_views.py::TestHomeView::test_get"
        ) == "TestHomeView > get"
        assert _format_test_name(
            "tests/describe_foo.py::describe_bar::it_does_thing"
        ) == "bar > does thing"

    def it_replaces_underscores_with_spaces():
        result = _format_test_name(
            "tests/test_x.py::test_handles_multiple_args"
        )
        assert result == "handles multiple args"

    def it_joins_parts_with_angle_bracket():
        result = _format_test_name(
            "tests/test_views.py::TestHomeView::test_get"
        )
        assert " > " in result
        assert result == "TestHomeView > get"

    def it_handles_parametrized_tests():
        result = _format_test_name(
            "tests/test_x.py::TestClass::test_method[param1]"
        )
        assert result == "TestClass > method[param1]"

    def it_preserves_multiple_parametrize_brackets():
        result = _format_test_name(
            "tests/test_x.py::test_thing[a-b]"
        )
        assert result == "thing[a-b]"

    def it_returns_simple_name_for_single_test():
        result = _format_test_name("tests/test_x.py::test_simple")
        assert result == "simple"

    def it_returns_raw_id_for_simple_input():
        result = _format_test_name("bare_name")
        assert result == "bare_name"

    def it_handles_empty_after_stripping():
        # Edge case: "tests/test_x.py::test_" -> prefix is "test_", rest is ""
        result = _format_test_name("tests/test_x.py::test_")
        # After stripping "test_" prefix, nothing remains
        assert result == "tests/test_x.py::test_"

    def it_handles_deeply_nested_describe_blocks():
        result = _format_test_name(
            "tests/describe_auth.py::describe_login::describe_with_valid_creds::it_returns_200"
        )
        assert result == "login > with valid creds > returns 200"

    def it_strips_context_prefix():
        result = _format_test_name("tests/describe_auth.py::describe_login::context_with_valid_creds::it_returns_200")
        assert result == "login > with valid creds > returns 200"


def describe_build_report_data():
    def it_includes_summary_fields():
        run = _make_run_result(mutants_pruned=3, total_mutants=5)
        data = _build_report_data(run)

        assert data["version"] == 1
        assert "generated_at" in data
        summary = data["summary"]
        assert summary["total_mutants"] == 5
        assert summary["mutants_tested"] == 2
        assert summary["mutants_pruned"] == 3
        assert summary["killed"] == 1
        assert summary["survived"] == 1
        assert summary["mutation_score"] == 50.0
        assert summary["wall_time_seconds"] == 1.5

    def it_includes_source_code_for_each_file():
        source = "def add(a, b):\n    return a + b\n"
        run = _make_run_result(target_sources={"/src/app.py": source})
        data = _build_report_data(run)

        file_data = list(data["files"].values())[0]
        assert file_data["source"] == source

    def it_serializes_coverage_map_to_per_line_structure():
        cov = CoverageMap()
        cov.add("/src/app.py", 10, "tests/test_app.py::test_add")
        cov.add("/src/app.py", 10, "tests/test_app.py::test_sub")
        cov.add("/src/app.py", 11, "tests/test_app.py::test_add")

        run = _make_run_result(coverage_map=cov)
        data = _build_report_data(run)

        file_data = list(data["files"].values())[0]
        lines = file_data["lines"]
        assert "10" in lines
        assert "11" in lines
        # Coverage entries should be formatted test names, sorted
        assert lines["10"]["coverage"] == ["add", "sub"]
        assert lines["11"]["coverage"] == ["add"]

    def it_includes_all_mutants_not_just_survivors():
        killed = _make_mutant_result(True, mutant_id=1)
        survived = _make_mutant_result(False, mutant_id=2)
        run = _make_run_result(results=[killed, survived])
        data = _build_report_data(run)

        file_data = list(data["files"].values())[0]
        assert len(file_data["mutants"]) == 2
        assert file_data["mutants"][0]["killed"] is True
        assert file_data["mutants"][1]["killed"] is False

    def it_builds_survived_index_in_file_line_order():
        s1 = _make_mutant_result(False, mutant_id=1, lineno=20)
        s2 = _make_mutant_result(False, mutant_id=2, lineno=5)
        k1 = _make_mutant_result(True, mutant_id=3, lineno=10)
        run = _make_run_result(results=[s1, s2, k1])
        data = _build_report_data(run)

        si = data["survived_index"]
        assert len(si) == 2
        # Sorted by line number within the same file
        assert si[0]["lineno"] == 5
        assert si[1]["lineno"] == 20

    def it_handles_none_coverage_map():
        run = _make_run_result(coverage_map=None)
        data = _build_report_data(run)

        file_data = list(data["files"].values())[0]
        assert file_data["lines"] == {}

    def it_uses_op_display_for_descriptions():
        run = _make_run_result()
        data = _build_report_data(run)

        file_data = list(data["files"].values())[0]
        mutant = file_data["mutants"][0]
        # Add -> Sub should show "+ → -" via _op_display
        assert mutant["description"] == "+ \u2192 -"
        assert mutant["original"] == "+"
        assert mutant["replacement"] == "-"

    def it_includes_test_ids_run_and_killing_tests():
        mr = _make_mutant_result(True, mutant_id=1)
        run = _make_run_result(results=[mr])
        data = _build_report_data(run)

        mutant_data = list(data["files"].values())[0]["mutants"][0]
        # Test names are formatted via _format_test_name
        assert mutant_data["test_ids_run"] == ["add", "sub"]
        assert mutant_data["killing_tests"] == ["add"]
        assert mutant_data["killing_test"] == "add"

    def it_handles_empty_results():
        run = _make_run_result(
            results=[],
            target_files=["/src/app.py"],
            target_sources={"/src/app.py": "x = 1\n"},
            total_mutants=0,
            mutants_tested=0,
        )
        data = _build_report_data(run)

        assert data["summary"]["total_mutants"] == 0
        assert data["summary"]["killed"] == 0
        assert data["summary"]["survived"] == 0
        assert data["summary"]["mutation_score"] == 0.0
        assert data["survived_index"] == []
        file_data = list(data["files"].values())[0]
        assert file_data["mutants"] == []
        assert file_data["stats"]["total"] == 0

    def it_uses_relative_file_paths():
        results = [
            _make_mutant_result(True, mutant_id=1, file_path="/home/user/project/src/app.py"),
            _make_mutant_result(False, mutant_id=2, file_path="/home/user/project/src/views.py"),
        ]
        run = _make_run_result(
            results=results,
            target_files=[
                "/home/user/project/src/app.py",
                "/home/user/project/src/views.py",
            ],
            target_sources={
                "/home/user/project/src/app.py": "# app\n",
                "/home/user/project/src/views.py": "# views\n",
            },
        )
        data = _build_report_data(run)

        assert "app.py" in data["files"]
        assert "views.py" in data["files"]

    def it_handles_single_file_relative_path():
        run = _make_run_result(
            target_files=["/home/user/project/src/app.py"],
            target_sources={"/home/user/project/src/app.py": "x = 1\n"},
        )
        data = _build_report_data(run)

        # Single file: dirname is common, so relpath is just the basename
        assert "app.py" in data["files"]

    def it_includes_per_file_stats():
        killed = _make_mutant_result(True, mutant_id=1)
        survived = _make_mutant_result(False, mutant_id=2)
        run = _make_run_result(results=[killed, survived])
        data = _build_report_data(run)

        file_data = list(data["files"].values())[0]
        stats = file_data["stats"]
        assert stats["total"] == 2
        assert stats["killed"] == 1
        assert stats["survived"] == 1
        assert stats["score"] == 50.0

    def it_has_iso_format_generated_at():
        run = _make_run_result()
        data = _build_report_data(run)

        # Should be parseable as ISO format and contain timezone info
        generated_at = data["generated_at"]
        assert "T" in generated_at
        assert "+" in generated_at or "Z" in generated_at

    def it_includes_mutant_col_offset_and_node_type():
        mr = _make_mutant_result(True, mutant_id=1, col_offset=8, node_type="Compare")
        run = _make_run_result(results=[mr])
        data = _build_report_data(run)

        mutant_data = list(data["files"].values())[0]["mutants"][0]
        assert mutant_data["col_offset"] == 8
        assert mutant_data["node_type"] == "Compare"

    def it_uses_results_list_index_for_mutant_id():
        mr1 = _make_mutant_result(True, mutant_id=42)
        mr2 = _make_mutant_result(False, mutant_id=99)
        run = _make_run_result(results=[mr1, mr2])
        data = _build_report_data(run)

        mutants = list(data["files"].values())[0]["mutants"]
        # id should be index in results list, not mutant_id
        assert mutants[0]["id"] == 0
        assert mutants[1]["id"] == 1

    def it_builds_survived_index_sorted_across_files():
        r1 = _make_mutant_result(
            False, mutant_id=1, file_path="/src/b.py", lineno=5,
        )
        r2 = _make_mutant_result(
            False, mutant_id=2, file_path="/src/a.py", lineno=10,
        )
        run = _make_run_result(
            results=[r1, r2],
            target_files=["/src/a.py", "/src/b.py"],
            target_sources={"/src/a.py": "# a\n", "/src/b.py": "# b\n"},
        )
        data = _build_report_data(run)

        si = data["survived_index"]
        assert len(si) == 2
        # Sorted by file first (a.py before b.py), then by lineno
        assert si[0]["file"] == "a.py"
        assert si[1]["file"] == "b.py"

    def it_handles_empty_target_files():
        result = RunResult(
            target_files=[],
            total_mutants=0,
            mutants_tested=0,
            mutants_pruned=0,
            results=[],
            wall_time_seconds=0.0,
        )
        data = _build_report_data(result)
        assert data["files"] == {}
        assert data["survived_index"] == []
        assert data["summary"]["total_mutants"] == 0

    def it_returns_filepath_unchanged_when_common_prefix_is_empty():
        """When target_files contains a bare filename (no directory), the
        common prefix is empty and _relpath must return the path unchanged.

        This kills the mutation: ``return fp`` -> ``return None`` on line 80.
        """
        mr = _make_mutant_result(True, mutant_id=1, file_path="app.py")
        run = _make_run_result(
            results=[mr],
            target_files=["app.py"],
            target_sources={"app.py": "x = 1\n"},
        )
        data = _build_report_data(run)

        # The file key must be the original path string, not None
        assert "app.py" in data["files"]
        file_data = data["files"]["app.py"]
        assert file_data["source"] == "x = 1\n"
        assert len(file_data["mutants"]) == 1
        assert file_data["mutants"][0]["killed"] is True


def describe_escape_json_for_html():
    def it_replaces_closing_script_tag():
        assert _escape_json_for_html("</script>") == "<\\/script>"

    def it_leaves_safe_json_unchanged():
        assert _escape_json_for_html('{"key": "value"}') == '{"key": "value"}'

    def it_handles_multiple_occurrences():
        result = _escape_json_for_html("</a></b></c>")
        assert result == "<\\/a><\\/b><\\/c>"

    def it_handles_empty_string():
        assert _escape_json_for_html("") == ""

    def it_round_trips_through_json_parser():
        """Escaped string is still valid JSON per RFC 8259."""
        original = {"key": "</script>", "nested": "</style>"}
        json_str = json.dumps(original)
        escaped = _escape_json_for_html(json_str)
        assert json.loads(escaped) == original


def describe_build_html_viewer():
    def it_returns_valid_html():
        html = _build_html_viewer("{}")
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def it_embeds_json_data_inline():
        html = _build_html_viewer('{"test": true}')
        assert 'window.LEELA_DATA = {"test": true}' in html

    def it_references_window_leela_data():
        html = _build_html_viewer("{}")
        assert "window.LEELA_DATA" in html

    def it_includes_title():
        html = _build_html_viewer("{}")
        assert "<title>Leela Mutation Report</title>" in html

    def it_wraps_data_in_script_tags():
        html = _build_html_viewer('{}')
        # Verify script tag opens before data and closes after the IIFE
        assert '<script>\nwindow.LEELA_DATA' in html
        assert '</script>\n</body>' in html

    def it_contains_required_dom_structure():
        html = _build_html_viewer("{}")
        assert 'id="sidebar"' in html
        assert 'id="source-panel"' in html
        assert 'id="detail-panel"' in html
        assert 'id="header"' in html
        assert 'id="main"' in html
        assert 'id="footer"' in html
        assert 'id="overlay"' in html


def _extract_json_from_html(html_content: str) -> dict:
    """Extract the LEELA_DATA JSON from an inline HTML report."""
    start_marker = "window.LEELA_DATA = "
    start = html_content.index(start_marker) + len(start_marker)
    # Find the end: look for the semicolon before (function()
    end_marker = ";\n(function()"
    end = html_content.index(end_marker, start)
    json_str = html_content[start:end]
    # Unescape the HTML-safe escaping
    json_str = json_str.replace("<\\/", "</")
    return json.loads(json_str)


def describe_generate_html_report():
    def it_creates_html_file_at_given_path(tmp_path):
        run = _make_run_result()
        output = str(tmp_path / "report.html")
        generate_html_report(run, output)
        assert os.path.exists(output)

    def it_embeds_valid_json_in_single_file(tmp_path):
        run = _make_run_result()
        output = str(tmp_path / "report.html")
        generate_html_report(run, output)

        with open(output) as f:
            html = f.read()
        data = _extract_json_from_html(html)
        assert data["version"] == 1
        assert "summary" in data
        assert "files" in data

    def it_handles_run_result_with_no_coverage(tmp_path):
        run = _make_run_result(coverage_map=None)
        output = str(tmp_path / "report.html")
        generate_html_report(run, output)

        with open(output) as f:
            html = f.read()
        data = _extract_json_from_html(html)
        file_data = list(data["files"].values())[0]
        assert file_data["lines"] == {}

    def it_preserves_all_mutant_data_through_round_trip(tmp_path):
        """Verify data integrity: build, serialize, deserialize, check."""
        killed = _make_mutant_result(True, mutant_id=1, lineno=10)
        survived = _make_mutant_result(False, mutant_id=2, lineno=20)
        run = _make_run_result(results=[killed, survived])
        output = str(tmp_path / "report.html")
        generate_html_report(run, output)

        with open(output) as f:
            html = f.read()
        data = _extract_json_from_html(html)

        file_data = list(data["files"].values())[0]
        assert len(file_data["mutants"]) == 2
        assert file_data["mutants"][0]["killed"] is True
        assert file_data["mutants"][1]["killed"] is False
        assert file_data["stats"]["killed"] == 1
        assert file_data["stats"]["survived"] == 1
        assert len(data["survived_index"]) == 1

    def it_produces_single_file_with_no_external_dependencies(tmp_path):
        run = _make_run_result()
        output = str(tmp_path / "report.html")
        generate_html_report(run, output)

        files_created = list(tmp_path.iterdir())
        assert len(files_created) == 1
        assert files_created[0].name == "report.html"

    def it_escapes_script_closing_tags_in_source_code(tmp_path):
        source = 'x = "</script>"\n'
        mr = _make_mutant_result(True, mutant_id=1, file_path="/src/app.py")
        run = _make_run_result(
            results=[mr],
            target_sources={"/src/app.py": source},
        )
        output = str(tmp_path / "report.html")
        generate_html_report(run, output)

        with open(output) as f:
            html = f.read()

        # The data portion (before the IIFE) must not contain a raw </script>
        data_end = html.index(";\n(function()")
        data_portion = html[:data_end]
        assert "</script>" not in data_portion

        # But the JSON should still be extractable and correct
        data = _extract_json_from_html(html)
        file_data = list(data["files"].values())[0]
        assert file_data["source"] == source

    def it_does_not_create_data_js_file(tmp_path):
        run = _make_run_result()
        output = str(tmp_path / "report.html")
        generate_html_report(run, output)

        data_js = tmp_path / "report.data.js"
        assert not data_js.exists()
