"""Validation functions with strict type annotations."""

from __future__ import annotations


def is_positive(n: int) -> bool:
    return n > 0


def is_negative(n: int) -> bool:
    return n < 0


def is_zero(n: int) -> bool:
    return n == 0


def is_even(n: int) -> bool:
    return n % 2 == 0


def is_odd(n: int) -> bool:
    return n % 2 != 0


def is_in_range(value: int, low: int, high: int) -> bool:
    return value >= low and value <= high


def is_valid_percentage(value: float) -> bool:
    return value >= 0.0 and value <= 100.0


def is_valid_age(age: int) -> bool:
    return age >= 0 and age <= 150


def validate_name(name: str) -> str | None:
    if len(name) == 0:
        return None
    if len(name) > 100:
        return None
    return name


def validate_email_simple(email: str) -> bool:
    return "@" in email and "." in email


def is_non_negative(n: int) -> bool:
    return n >= 0


def clamp_to_range(value: int, low: int, high: int) -> int:
    if value < low:
        return low
    if value > high:
        return high
    return value
