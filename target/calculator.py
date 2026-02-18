"""Arithmetic operations with strict type annotations."""

from __future__ import annotations


def add(x: int, y: int) -> int:
    return x + y


def subtract(x: int, y: int) -> int:
    return x - y


def multiply(x: int, y: int) -> int:
    return x * y


def integer_divide(x: int, y: int) -> int:
    if y == 0:
        return 0
    return x // y


def negate(x: int) -> int:
    return -x


def absolute(x: int) -> int:
    if x < 0:
        return -x
    return x


def clamp(value: int, low: int, high: int) -> int:
    if value < low:
        return low
    if value > high:
        return high
    return value


def sum_of_squares(a: int, b: int) -> int:
    return a * a + b * b


def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    dx: float = x2 - x1
    dy: float = y2 - y1
    return (dx * dx + dy * dy) ** 0.5


def average(a: float, b: float) -> float:
    return (a + b) / 2.0
