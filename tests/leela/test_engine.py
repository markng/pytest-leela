"""Plain pytest tests for ``pytest_leela.engine``.

Mirrors the ``describe_engine.py`` block-style tests so the
daemon's coverage collector (running under system Python
without ``pytest-describe``) can discover them. Targets every
mutated path in ``engine.py``.
"""

from __future__ import annotations

import os
import sys
import typing
from pathlib import Path


sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "src"),
)

from pytest_leela.engine import (  # noqa: E402
    Engine,
    _clean_process_state,
    _module_name_from_path,
)


# ---------------------------------------------------------------------------
# _module_name_from_path
# ---------------------------------------------------------------------------


def test_module_name_from_path_resolves_under_sys_path(tmp_path: Path) -> None:
    """Path under a ``sys.path`` entry becomes dotted module name.

    Kills ``+ -> -`` mutation on line 51 (``abs_path.startswith(base + os.sep)``):
    a ``-`` operator on a ``str`` raises ``TypeError`` and the function
    cannot return a result.
    """
    src_dir = tmp_path / "src"
    pkg_dir = src_dir / "pkg" / "sub"
    pkg_dir.mkdir(parents=True)
    file_path = pkg_dir / "mod.py"
    file_path.write_text("x = 1\n")
    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(src_dir))
        result = _module_name_from_path(str(file_path))
        assert result == "pkg.sub.mod"
    finally:
        sys.path[:] = old_path


def test_module_name_from_path_strips_py_suffix(tmp_path: Path) -> None:
    """``.py`` suffix is stripped from the module name."""
    src_dir = tmp_path / "src"
    pkg_dir = src_dir / "pkg"
    pkg_dir.mkdir(parents=True)
    file_path = pkg_dir / "mod.py"
    file_path.write_text("x = 1\n")
    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(src_dir))
        result = _module_name_from_path(str(file_path))
        assert not result.endswith(".py")
    finally:
        sys.path[:] = old_path


def test_module_name_from_path_prefers_longest_sys_path(tmp_path: Path) -> None:
    """Longest matching ``sys.path`` entry wins."""
    longer = tmp_path / "longer"
    shorter = tmp_path / "short"
    longer.mkdir()
    shorter.mkdir()
    target_dir = longer / "sub"
    target_dir.mkdir()
    file_path = target_dir / "mod.py"
    file_path.write_text("x = 1\n")

    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(shorter))
        sys.path.insert(0, str(longer))
        result = _module_name_from_path(str(file_path))
        # With longer prefix, module is "sub.mod"
        assert result == "sub.mod"
    finally:
        sys.path[:] = old_path


def test_module_name_from_path_falls_back_to_relpath(tmp_path: Path) -> None:
    """When no ``sys.path`` entry matches, fall back to CWD-relative."""
    file_path = tmp_path / "lone_file.py"
    file_path.write_text("x = 1\n")
    result = _module_name_from_path(str(file_path))
    # Result is some dotted path that ends in lone_file
    assert result.endswith("lone_file")


# ---------------------------------------------------------------------------
# _clean_process_state
# ---------------------------------------------------------------------------


def test_clean_process_state_removes_mutating_finders() -> None:
    """``_clean_process_state`` removes ``MutatingFinder`` from ``sys.meta_path``.

    Kills ``Not -> _remove`` mutation on line 80:
    ``if not _remove(f, MutatingFinder):`` raises ``NameError``.
    """
    from unittest.mock import MagicMock

    from pytest_leela.import_hook import MutatingFinder

    # MutatingFinder requires target_modules and mutant — mock them
    fake = MagicMock(spec=MutatingFinder)
    sys.meta_path.append(fake)
    try:
        _clean_process_state()
        assert fake not in sys.meta_path
    finally:
        if fake in sys.meta_path:
            sys.meta_path.remove(fake)


def test_clean_process_state_removes_temp_modules(tmp_path: Path) -> None:
    """``_clean_process_state`` removes temp-dir modules from ``sys.modules``."""
    import types
    import tempfile

    # Create a fake module in a temp dir that matches tempfile.gettempdir()
    tmpdir = tempfile.mkdtemp()
    mod = types.ModuleType("leela_test_temp_module_xyz")
    mod.__file__ = os.path.join(tmpdir, "leela_test_temp_module_xyz.py")
    sys.modules["leela_test_temp_module_xyz"] = mod
    try:
        _clean_process_state()
        assert "leela_test_temp_module_xyz" not in sys.modules
    finally:
        if "leela_test_temp_module_xyz" in sys.modules:
            del sys.modules["leela_test_temp_module_xyz"]


def test_clean_process_state_preserves_non_temp_modules(tmp_path: Path) -> None:
    """Modules NOT in temp dir are preserved."""
    import types

    # Create a fake module NOT in a temp dir
    mod = types.ModuleType("leela_test_preserve_me")
    mod.__file__ = "/some/normal/dir/leela_test_preserve_me.py"
    sys.modules["leela_test_preserve_me"] = mod
    try:
        _clean_process_state()
        assert "leela_test_preserve_me" in sys.modules
    finally:
        del sys.modules["leela_test_preserve_me"]


# ---------------------------------------------------------------------------
# Engine — type annotation mutations are killable via get_type_hints
# ---------------------------------------------------------------------------


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


def test_engine_run_diff_base_filters_to_changed_lines(tmp_path: Path) -> None:
    """``diff_base`` filters mutants to only changed lines.

    Kills ``And -> Or`` on line 182 (the ``and`` joining file and line
    membership in ``diff_lines``): with ``Or``, every mutant that has
    EITHER a changed file OR a changed line gets tested, defeating
    the filter. We pass a sentinel and check the tested lines are
    restricted to the diff.
    """
    from unittest.mock import patch

    target = tmp_path / "t_diff.py"
    target.write_text(
        "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n"
    )
    abs_target = str(target.resolve())
    test_dir = tmp_path / "t_diff_tests"
    test_dir.mkdir()
    (test_dir / "test_t_diff.py").write_text(
        f"import sys\nsys.path.insert(0, {str(target.parent)!r})\n"
        "from t_diff import add, sub\n"
        "def test_add():\n    assert add(1, 2) == 3\n"
        "def test_sub():\n    assert sub(5, 3) == 2\n"
    )

    engine = Engine(use_types=False, use_coverage=False)

    # Without diff_base: all mutants are tested (both line 2 and line 5)
    result_all = engine.run([str(target)], str(test_dir))
    assert result_all.total_mutants > 1

    # With diff_base: only line 2 changed
    with patch("pytest_leela.engine.changed_lines") as mock_cl:
        mock_cl.return_value = {abs_target: {2}}
        result_diff = engine.run([str(target)], str(test_dir), diff_base="main")

    assert 0 < result_diff.mutants_tested < result_all.mutants_tested
    tested_lines = {r.mutant.point.lineno for r in result_diff.results}
    assert tested_lines == {2}
