"""Configuration loader for pytest-leela — reads [tool.pytest-leela] from pyproject.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pytest_leela.operators import ALL_OPERATORS, DEFAULT_OPERATORS


@dataclass(frozen=True)
class LeelaConfig:
    """Configuration for pytest-leela mutation testing."""

    exclude: tuple[str, ...] = ()
    operators: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_OPERATORS))


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

    # Validate types — a bare string silently iterates characters (Guidelines 2:5)
    if not isinstance(exclude, list):
        raise ValueError(
            f"[tool.pytest-leela] 'exclude' must be a list of strings in pyproject.toml, "
            f"got {type(exclude).__name__}: {exclude!r}"
        )
    if not isinstance(operators, list):
        raise ValueError(
            f"[tool.pytest-leela] 'operators' must be a list of strings in pyproject.toml, "
            f"got {type(operators).__name__}: {operators!r}"
        )

    # Validate inner element types — every element must be a string (Guidelines 2:5)
    for item in exclude:
        if not isinstance(item, str):
            raise ValueError(
                f"[tool.pytest-leela] 'exclude' elements must be strings, "
                f"got {type(item).__name__}: {item!r}"
            )
    for item in operators:
        if not isinstance(item, str):
            raise ValueError(
                f"[tool.pytest-leela] 'operators' elements must be strings, "
                f"got {type(item).__name__}: {item!r}"
            )

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

    return LeelaConfig(exclude=tuple(exclude), operators=tuple(operators))
