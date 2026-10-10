"""Unique probes retained from the removed discovery twins."""

from pathlib import Path
import typing
from pytest_leela.engine import Engine
from pytest_leela.git_diff import _parse_diff_hunks, changed_files
from pytest_leela.operators import build_allowed_keys, mutations_for, count_pruned

def test_engine_init_enabled_categories_annotation_is_union() -> None:
    """``enabled_categories`` annotation is a union (``tuple | list | None``).

    Kills ``BitOr -> BitAnd`` on line 103. With ``|`` mutated to ``&``,
    ``tuple[str, ...] & list[str, ...] & None`` is a ``TypeError`` when
    ``get_type_hints`` evaluates the string.
    """
    hints = typing.get_type_hints(Engine.__init__)
    assert "enabled_categories" in hints

def test_engine_run_test_dir_annotation_is_union() -> None:
    """``test_dir`` annotation is a union (``str | None``).

    Kills ``BitOr -> BitAnd`` on line 117. With ``|`` mutated to ``&``,
    ``str & None`` is a ``TypeError`` when ``get_type_hints`` evaluates.
    """
    hints = typing.get_type_hints(Engine.run)
    assert "test_dir" in hints

def test_engine_run_skips_apply_limits_when_none(tmp_path: Path) -> None:
    """``apply_limits`` is NOT called when ``limits`` is ``None``.

    Kills ``IsNot -> Is`` on line 128 (``if limits is not None``):
    with the mutation, ``apply_limits`` is called with ``None`` as
    limits, which raises (or applies no-op limits). The patch below
    would fail to be entered, so the test asserts no call.
    """
    from unittest.mock import patch

    # Create a minimal target + test file
    target_dir = tmp_path / "src"
    test_dir = tmp_path / "tests"
    target_dir.mkdir()
    test_dir.mkdir()
    target_file = target_dir / "tiny.py"
    target_file.write_text("def add(a, b):\n    return a + b\n")
    test_file = test_dir / "test_tiny.py"
    test_file.write_text(
        f"import sys\nsys.path.insert(0, {str(target_dir)!r})\n"
        "from tiny import add\n"
        "def test_add():\n    assert add(2, 3) == 5\n"
    )

    with patch("pytest_leela.engine.apply_limits") as mock_apply:
        # limits=None so apply_limits should NOT be called.
        # With mutation limits=None would trigger apply_limits(None) — bad.
        engine = Engine(use_types=False, use_coverage=False)
        engine.run(
            [str(target_file)],
            str(test_dir),
            limits=None,
        )
        mock_apply.assert_not_called()

def test_engine_run_calls_apply_limits_when_provided(tmp_path: Path) -> None:
    """``apply_limits`` IS called when ``limits`` is not ``None``.

    Counter-test for the ``IsNot -> Is`` mutation: with the mutation,
    ``apply_limits`` would be called with ``None`` instead of the real
    limits, so the real ``ResourceLimits`` would never reach the apply
    function. We pass a sentinel and assert it gets through.
    """
    from unittest.mock import patch

    from pytest_leela.resources import ResourceLimits

    target_dir = tmp_path / "src"
    test_dir = tmp_path / "tests"
    target_dir.mkdir()
    test_dir.mkdir()
    target_file = target_dir / "tiny.py"
    target_file.write_text("def add(a, b):\n    return a + b\n")
    test_file = test_dir / "test_tiny.py"
    test_file.write_text(
        f"import sys\nsys.path.insert(0, {str(target_dir)!r})\n"
        "from tiny import add\n"
        "def test_add():\n    assert add(2, 3) == 5\n"
    )

    sentinel_limits = ResourceLimits(max_memory_percent=42)
    with patch("pytest_leela.engine.apply_limits") as mock_apply:
        engine = Engine(use_types=False, use_coverage=False)
        engine.run(
            [str(target_file)],
            str(test_dir),
            limits=sentinel_limits,
        )
        mock_apply.assert_called_once_with(sentinel_limits)

def test_parse_diff_hunks_annotation_is_union() -> None:
    """``_parse_diff_hunks`` return annotation is a union.

    Kills ``BitOr -> BitAnd`` on line 80: ``dict[str, set[int]] | None`` mutated
    to ``&`` raises ``TypeError`` when ``get_type_hints`` evaluates.
    """
    hints = typing.get_type_hints(_parse_diff_hunks)
    assert "return" in hints

def test_changed_files_annotation_is_union() -> None:
    """``changed_files`` annotation is a union.

    Kills ``BitOr -> BitAnd`` on line 12.
    """
    hints = typing.get_type_hints(changed_files)
    assert "return" in hints

def test_build_allowed_keys_annotation_is_union() -> None:
    """``enabled_categories`` annotation is a union.

    Kills ``BitOr -> BitAnd`` on line 265: ``tuple[str, ...] | list[str, ...] | None``
    mutated to ``&`` raises ``TypeError`` when ``get_type_hints`` evaluates.
    """
    hints = typing.get_type_hints(build_allowed_keys)
    assert "enabled_categories" in hints

def test_mutations_for_annotation_is_union() -> None:
    """``allowed_keys`` annotation is a union.

    Kills ``BitOr -> BitAnd`` on line 293: ``frozenset[...] | None`` mutated
    to ``&`` raises ``TypeError`` when ``get_type_hints`` evaluates.
    """
    hints = typing.get_type_hints(mutations_for)
    assert "allowed_keys" in hints

def test_count_pruned_annotation_is_union() -> None:
    """``allowed_keys`` annotation is a union.

    Kills ``BitOr -> BitAnd`` on line 320.
    """
    hints = typing.get_type_hints(count_pruned)
    assert "allowed_keys" in hints
