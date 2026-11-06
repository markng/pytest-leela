"""Plain pytest tests for the sentinel target module.

These use the ``test_*`` naming convention so the daemon's
coverage collector (running under system Python without
``pytest-describe``) can discover them. Mirrors the
describe-block tests in ``tests/target/describe_change_me.py``
so the mutants generated for ``change_me.add`` and
``change_me.greet`` are killed whether discovery uses
``pytest-describe`` or vanilla ``pytest``.
"""

import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "src"),
)

import pytest_leela.change_me as change_me  # noqa: E402


def test_greet_greets_by_name() -> None:
    """``change_me.greet("world")`` returns ``"hello, world"``."""
    assert change_me.greet("world") == "hello, world"


def test_greet_returns_a_string() -> None:
    """Kills the ``return f"hello, {name}"`` -> ``return None`` mutant.

    If the return value flips to ``None``, ``isinstance(..., str)``
    is False and this assertion fails.
    """
    assert isinstance(change_me.greet("x"), str)


def test_greet_embeds_the_name() -> None:
    """Kills the ``f"hello, {name}"`` -> ``f"hi, {name}"`` mutant.

    Mutating the literal prefix to anything other than ``"hello, "``
    means ``"alice"`` is no longer in the output.
    """
    assert "alice" in change_me.greet("alice")


def test_add_two_positive_integers() -> None:
    """``change_me.add(2, 3) == 5`` — kills ``+ -> -`` mutation."""
    assert change_me.add(2, 3) == 5


def test_add_negative_numbers() -> None:
    """``change_me.add(-1, -2) == -3``.

    A ``+`` -> ``-`` mutation would give ``change_me.add(-1, -2) == 1``.
    """
    assert change_me.add(-1, -2) == -3


def test_add_zero_inputs() -> None:
    """``change_me.add(0, 0) == 0``.

    A mutation that always returns a constant would fail this
    if it returned ``1`` or ``-1`` instead.
    """
    assert change_me.add(0, 0) == 0


def test_add_large_numbers_catches_x_minus_y() -> None:
    """``1000 + 1`` is ``1001``, not ``999``.

    This kills the ``x + y`` -> ``x - y`` mutant: with the
    mutation, the function returns ``999`` and the assertion fails.
    """
    assert change_me.add(1000, 1) == 1001
