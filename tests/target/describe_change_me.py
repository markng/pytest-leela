"""Tests for the sentinel target module ``pytest_leela.change_me``.

The daemon uses ``change_me.py`` to demonstrate the
watch-and-reanalyze cycle. These tests exist so the
mutations applied to ``greet`` and ``add`` are killed by
real assertions, not just observed.
"""

import pytest_leela.change_me as change_me


def describe_greet():
    def it_greets_by_name():
        assert change_me.greet("world") == "hello, world"

    def it_returns_a_string():
        # Mutant: ``return f"hello, {name}"`` -> ``return None``.
        # If the daemon flips the return, this should fail.
        assert isinstance(change_me.greet("x"), str)

    def it_embeds_the_name_in_the_greeting():
        assert "alice" in change_me.greet("alice")


def describe_add():
    def it_adds_two_positive_integers():
        assert change_me.add(2, 3) == 5

    def it_adds_negative_numbers():
        assert change_me.add(-1, -2) == -3

    def it_returns_zero_for_zero_inputs():
        assert change_me.add(0, 0) == 0

    def it_adds_large_numbers():
        # Catch the ``x + y`` -> ``x - y`` mutation: ``1000 + 1``
        # is ``1001``, not ``999``.
        assert change_me.add(1000, 1) == 1001
