"""Plain pytest tests for ``pytest_leela.config``.

Mirrors the ``describe_config.py`` block-style tests so the
daemon's coverage collector (running under system Python
without ``pytest-describe``) can discover them and the
config-loader mutations get killed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "src"),
)

from pytest_leela.config import (  # noqa: E402
    ALL_OPERATORS,
    DEFAULT_OPERATORS,
    LeelaConfig,
    load_config,
)


# ---------------------------------------------------------------------------
# LeelaConfig dataclass
# ---------------------------------------------------------------------------


def test_leela_config_has_empty_exclude_by_default() -> None:
    """``LeelaConfig()`` has empty exclude tuple."""
    config = LeelaConfig()
    assert config.exclude == ()


def test_leela_config_has_default_operators_by_default() -> None:
    """``LeelaConfig()`` has DEFAULT_OPERATORS."""
    config = LeelaConfig()
    assert config.operators == DEFAULT_OPERATORS


def test_leela_config_accepts_custom_exclude() -> None:
    """``LeelaConfig(exclude=...)`` stores the value."""
    config = LeelaConfig(exclude=("tests/*", "migrations/*"))
    assert config.exclude == ("tests/*", "migrations/*")


def test_leela_config_accepts_custom_operators() -> None:
    """``LeelaConfig(operators=...)`` stores the value."""
    config = LeelaConfig(operators=("arithmetic", "boolean"))
    assert config.operators == ("arithmetic", "boolean")


def test_leela_config_is_frozen() -> None:
    """``LeelaConfig`` is a frozen dataclass."""
    config = LeelaConfig()
    with pytest.raises(AttributeError):
        config.exclude = ("new",)  # type: ignore[misc]


def test_leela_config_stores_tuples_not_lists() -> None:
    """``LeelaConfig`` stores tuples, not lists."""
    config = LeelaConfig(exclude=("a",), operators=("b",))
    assert isinstance(config.exclude, tuple)
    assert isinstance(config.operators, tuple)


# ---------------------------------------------------------------------------
# load_config function
# ---------------------------------------------------------------------------


def test_load_config_reads_pyproject_toml(tmp_path: Path) -> None:
    """``load_config`` reads ``[tool.pytest-leela]`` from pyproject.toml.

    Kills ``/ -> *`` mutation on line 26 (``rootpath / "pyproject.toml"``):
    a ``*`` operator on ``Path`` raises ``TypeError``, so reaching this
    line is impossible without the correct division operator.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[tool.pytest-leela]\n"
        'exclude = ["tests/*", "migrations/*"]\n'
        'operators = ["arithmetic", "comparison"]\n'
    )
    config = load_config(tmp_path)
    assert config.exclude == ("tests/*", "migrations/*")
    assert config.operators == ("arithmetic", "comparison")


def test_load_config_returns_tuples(tmp_path: Path) -> None:
    """``load_config`` returns tuples, not lists."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.pytest-leela]\nexclude = ["a"]\noperators = ["arithmetic"]\n'
    )
    config = load_config(tmp_path)
    assert isinstance(config.exclude, tuple)
    assert isinstance(config.operators, tuple)


def test_load_config_defaults_when_no_pyproject_toml(tmp_path: Path) -> None:
    """No ``pyproject.toml`` -> defaults."""
    config = load_config(tmp_path)
    assert config.exclude == ()
    assert config.operators == DEFAULT_OPERATORS


def test_load_config_defaults_when_no_tool_section(tmp_path: Path) -> None:
    """``[project]`` only, no ``[tool.*]`` -> defaults."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'myproject'\n")
    config = load_config(tmp_path)
    assert config.exclude == ()
    assert config.operators == DEFAULT_OPERATORS


def test_load_config_defaults_when_no_pytest_leela_section(tmp_path: Path) -> None:
    """``[tool.pytest]`` only -> defaults."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.pytest]\ntestpaths = ['tests']\n")
    config = load_config(tmp_path)
    assert config.exclude == ()
    assert config.operators == DEFAULT_OPERATORS


def test_load_config_uses_default_operators_when_only_exclude_specified(
    tmp_path: Path,
) -> None:
    """Only ``exclude`` in config -> default operators."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.pytest-leela]\nexclude = ["vendor/*"]\n')
    config = load_config(tmp_path)
    assert config.exclude == ("vendor/*",)
    assert config.operators == DEFAULT_OPERATORS


def test_load_config_uses_empty_exclude_when_only_operators_specified(
    tmp_path: Path,
) -> None:
    """Only ``operators`` in config -> empty exclude."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.pytest-leela]\noperators = ["boolean", "return"]\n')
    config = load_config(tmp_path)
    assert config.exclude == ()
    assert config.operators == ("boolean", "return")


def test_load_config_expands_all_to_full_operator_list(tmp_path: Path) -> None:
    """``operators = ["all"]`` -> ALL_OPERATORS."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.pytest-leela]\noperators = ["all"]\n')
    config = load_config(tmp_path)
    assert config.operators == ALL_OPERATORS


def test_load_config_expands_all_when_mixed(tmp_path: Path) -> None:
    """``["arithmetic", "all"]`` -> ALL_OPERATORS."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.pytest-leela]\noperators = ["arithmetic", "all"]\n')
    config = load_config(tmp_path)
    assert config.operators == ALL_OPERATORS


def test_load_config_returns_defaults_for_empty_pytest_leela_section(
    tmp_path: Path,
) -> None:
    """``[tool.pytest-leela]`` empty -> defaults."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.pytest-leela]\n")
    config = load_config(tmp_path)
    assert config.exclude == ()
    assert config.operators == DEFAULT_OPERATORS


def test_load_config_raises_on_unknown_operator_category(tmp_path: Path) -> None:
    """Unknown operator category -> ValueError."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.pytest-leela]\noperators = ["arithmetic", "nonexistent_category"]\n'
    )
    with pytest.raises(ValueError, match="Unknown operator categories"):
        load_config(tmp_path)


def test_load_config_raises_when_exclude_is_a_string(tmp_path: Path) -> None:
    """``exclude = "tests/*"`` (string, not list) -> ValueError."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.pytest-leela]\nexclude = "tests/*"\n')
    with pytest.raises(ValueError, match="'exclude' must be a list of strings"):
        load_config(tmp_path)


def test_load_config_raises_when_operators_is_a_string(tmp_path: Path) -> None:
    """``operators = "arithmetic"`` (string, not list) -> ValueError."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.pytest-leela]\noperators = "arithmetic"\n')
    with pytest.raises(ValueError, match="'operators' must be a list of strings"):
        load_config(tmp_path)


def test_load_config_raises_when_exclude_contains_non_string_element(
    tmp_path: Path,
) -> None:
    """``exclude = ["ok", 123]`` -> ValueError."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.pytest-leela]\nexclude = ["ok", 123]\n')
    with pytest.raises(ValueError, match="'exclude' elements must be strings"):
        load_config(tmp_path)


def test_load_config_raises_when_operators_contains_non_string_element(
    tmp_path: Path,
) -> None:
    """``operators = ["arithmetic", true]`` -> ValueError."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.pytest-leela]\noperators = ["arithmetic", true]\n')
    with pytest.raises(ValueError, match="'operators' elements must be strings"):
        load_config(tmp_path)
