"""Tests for the sentinel target module.

The daemon uses ``change_me`` to demonstrate the watch-and-reanalyze
cycle. The module is now created dynamically by the ``change_me``
fixture below so the shipped package contains no trivial demo module.
The fixture never edits sys.path or sys.modules.
"""

import importlib.util
from pathlib import Path

import pytest

# Source of the sentinel ``change_me`` module. It previously lived
# at ``src/pytest_leela/change_me.py`` and was therefore published
# in the wheel. It is now created dynamically by the ``change_me``
# fixture so the shipped package contains no trivial demo module.
_CHANGE_ME_SOURCE = '''\
"""Sentinel module used to exercise the leela daemon.

This file is intentionally trivial. The daemon should pick up its
creation, then re-analyze it whenever it changes. Edit me and
watch the daemon fire.
"""  # touched to trigger daemon re-analysis


def greet(name: str) -> str:
    """Return a friendly greeting for the given ``name``."""
    return f"hello, {name}"


def add(x: int, y: int) -> int:
    """Add two integers and return the sum."""
    return x + y
'''


@pytest.fixture
def change_me(tmp_path: Path):
    path = tmp_path / "change_me.py"
    path.write_text(_CHANGE_ME_SOURCE)
    spec = importlib.util.spec_from_file_location("change_me", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def describe_greet():
    def it_greets_by_name(change_me):
        assert change_me.greet("world") == "hello, world"

    def it_returns_a_string(change_me):
        # Mutant: ``return f"hello, {name}"`` -> ``return None``.
        assert isinstance(change_me.greet("x"), str)

    def it_embeds_the_name_in_the_greeting(change_me):
        assert "alice" in change_me.greet("alice")


def describe_add():
    def it_adds_two_positive_integers(change_me):
        assert change_me.add(2, 3) == 5

    def it_adds_negative_numbers(change_me):
        assert change_me.add(-1, -2) == -3

    def it_returns_zero_for_zero_inputs(change_me):
        assert change_me.add(0, 0) == 0

    def it_adds_large_numbers(change_me):
        # Catch the ``x + y`` -> ``x - y`` mutation: ``1000 + 1``
        # is ``1001``, not ``999``.
        assert change_me.add(1000, 1) == 1001
