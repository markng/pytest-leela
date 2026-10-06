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
