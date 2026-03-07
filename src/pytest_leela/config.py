"""Configuration loader for pytest-leela — reads [tool.pytest-leela] from pyproject.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pytest_leela.operators import ALL_OPERATORS, DEFAULT_OPERATORS


@dataclass(frozen=True)
class LeelaConfig:
    """Configuration for pytest-leela mutation testing."""

    exclude: list[str] = field(default_factory=list)
    operators: list[str] = field(default_factory=lambda: list(DEFAULT_OPERATORS))


def load_config(rootpath: Path) -> LeelaConfig:
    """Read [tool.pytest-leela] from pyproject.toml and return a LeelaConfig.

    Falls back to defaults if pyproject.toml doesn't exist or has no
    [tool.pytest-leela] section.
    """
    pyproject_path = rootpath / "pyproject.toml"
    if not pyproject_path.exists():
        return LeelaConfig()

    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    leela_config = data.get("tool", {}).get("pytest-leela", {})
    if not leela_config:
        return LeelaConfig()

    exclude = leela_config.get("exclude", [])
    operators = leela_config.get("operators", list(DEFAULT_OPERATORS))

    # Validate operator names before expansion
    valid_names = set(ALL_OPERATORS) | {"all"}
    unknown = [op for op in operators if op not in valid_names]
    if unknown:
        raise ValueError(
            f"Unknown operator categories in [tool.pytest-leela] operators: {unknown}. "
            f"Valid categories: {sorted(valid_names)}"
        )

    # Expand "all" to the full operator list
    if "all" in operators:
        operators = list(ALL_OPERATORS)

    return LeelaConfig(exclude=exclude, operators=operators)
