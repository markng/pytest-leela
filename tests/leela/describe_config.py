"""Tests for pytest_leela.config — pyproject.toml config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from pytest_leela.config import (
    ALL_OPERATORS,
    DEFAULT_OPERATORS,
    LeelaConfig,
    load_config,
)


def describe_leela_config():
    def it_has_empty_exclude_by_default():
        config = LeelaConfig()
        assert config.exclude == ()

    def it_has_default_operators_by_default():
        config = LeelaConfig()
        assert config.operators == DEFAULT_OPERATORS

    def it_accepts_custom_exclude():
        config = LeelaConfig(exclude=("tests/*", "migrations/*"))
        assert config.exclude == ("tests/*", "migrations/*")

    def it_accepts_custom_operators():
        config = LeelaConfig(operators=("arithmetic", "boolean"))
        assert config.operators == ("arithmetic", "boolean")

    def it_is_frozen():
        config = LeelaConfig()
        with pytest.raises(AttributeError):
            config.exclude = ("new",)  # type: ignore[misc]

    def it_stores_tuples_not_lists():
        config = LeelaConfig(exclude=("a",), operators=("b",))
        assert isinstance(config.exclude, tuple)
        assert isinstance(config.operators, tuple)


def describe_load_config():
    def it_loads_valid_config_from_pyproject_toml(tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.pytest-leela]\n'
            'exclude = ["tests/*", "migrations/*"]\n'
            'operators = ["arithmetic", "comparison"]\n'
        )
        config = load_config(tmp_path)
        assert config.exclude == ("tests/*", "migrations/*")
        assert config.operators == ("arithmetic", "comparison")

    def it_returns_tuples_from_pyproject_toml(tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.pytest-leela]\n'
            'exclude = ["a"]\n'
            'operators = ["arithmetic"]\n'
        )
        config = load_config(tmp_path)
        assert isinstance(config.exclude, tuple)
        assert isinstance(config.operators, tuple)

    def describe_defaults():
        def it_returns_defaults_when_no_pyproject_toml(tmp_path: Path):
            config = load_config(tmp_path)
            assert config.exclude == ()
            assert config.operators == DEFAULT_OPERATORS

        def it_returns_defaults_when_no_tool_section(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text("[project]\nname = 'myproject'\n")
            config = load_config(tmp_path)
            assert config.exclude == ()
            assert config.operators == DEFAULT_OPERATORS

        def it_returns_defaults_when_no_pytest_leela_section(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                "[tool.pytest]\ntestpaths = ['tests']\n"
            )
            config = load_config(tmp_path)
            assert config.exclude == ()
            assert config.operators == DEFAULT_OPERATORS

    def describe_partial_config():
        def it_uses_default_operators_when_only_exclude_specified(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'exclude = ["vendor/*"]\n'
            )
            config = load_config(tmp_path)
            assert config.exclude == ("vendor/*",)
            assert config.operators == DEFAULT_OPERATORS

        def it_uses_empty_exclude_when_only_operators_specified(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'operators = ["boolean", "return"]\n'
            )
            config = load_config(tmp_path)
            assert config.exclude == ()
            assert config.operators == ("boolean", "return")

    def describe_all_operators_expansion():
        def it_expands_all_to_full_operator_list(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'operators = ["all"]\n'
            )
            config = load_config(tmp_path)
            assert config.operators == ALL_OPERATORS

        def it_expands_all_even_when_mixed_with_other_operators(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'operators = ["arithmetic", "all"]\n'
            )
            config = load_config(tmp_path)
            assert config.operators == ALL_OPERATORS

    def it_returns_defaults_for_empty_pytest_leela_section(tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.pytest-leela]\n")
        config = load_config(tmp_path)
        assert config.exclude == ()
        assert config.operators == DEFAULT_OPERATORS

    def it_raises_on_unknown_operator_category(tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.pytest-leela]\n'
            'operators = ["arithmetic", "nonexistent_category"]\n'
        )
        with pytest.raises(ValueError, match="Unknown operator categories"):
            load_config(tmp_path)

    def describe_type_validation():
        def it_raises_when_exclude_is_a_string(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'exclude = "tests/*"\n'
            )
            with pytest.raises(ValueError, match="'exclude' must be a list of strings"):
                load_config(tmp_path)

        def it_raises_when_operators_is_a_string(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'operators = "arithmetic"\n'
            )
            with pytest.raises(ValueError, match="'operators' must be a list of strings"):
                load_config(tmp_path)

        def it_includes_actual_type_in_exclude_error(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'exclude = "tests/*"\n'
            )
            with pytest.raises(ValueError, match="got str"):
                load_config(tmp_path)

        def it_includes_actual_type_in_operators_error(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'operators = "arithmetic"\n'
            )
            with pytest.raises(ValueError, match="got str"):
                load_config(tmp_path)

        def it_raises_when_exclude_is_an_integer(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'exclude = 42\n'
            )
            with pytest.raises(ValueError, match="'exclude' must be a list of strings"):
                load_config(tmp_path)

        def it_raises_when_operators_is_a_boolean(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'operators = true\n'
            )
            with pytest.raises(ValueError, match="'operators' must be a list of strings"):
                load_config(tmp_path)

        def it_raises_when_exclude_contains_non_string_element(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'exclude = ["ok", 123]\n'
            )
            with pytest.raises(ValueError, match="'exclude' elements must be strings"):
                load_config(tmp_path)

        def it_raises_when_operators_contains_non_string_element(tmp_path: Path):
            pyproject = tmp_path / "pyproject.toml"
            pyproject.write_text(
                '[tool.pytest-leela]\n'
                'operators = ["arithmetic", true]\n'
            )
            with pytest.raises(ValueError, match="'operators' elements must be strings"):
                load_config(tmp_path)
