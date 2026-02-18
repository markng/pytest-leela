"""Collection operations with strict type annotations."""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")


def is_non_empty(items: list[T]) -> bool:
    return len(items) > 0


def first_or_none(items: list[T]) -> T | None:
    if len(items) == 0:
        return None
    return items[0]


def last_or_none(items: list[T]) -> T | None:
    if len(items) == 0:
        return None
    return items[-1]


def contains(items: list[T], item: T) -> bool:
    return item in items


def safe_get(items: list[T], index: int) -> T | None:
    if index < 0 or index >= len(items):
        return None
    return items[index]


def merge_dicts(a: dict[K, V], b: dict[K, V]) -> dict[K, V]:
    result: dict[K, V] = {}
    for key in a:
        result[key] = a[key]
    for key in b:
        result[key] = b[key]
    return result


def keys_with_value(d: dict[K, V], value: V) -> list[K]:
    result: list[K] = []
    for k, v in d.items():
        if v == value:
            result.append(k)
    return result


def sum_values(items: list[int]) -> int:
    total: int = 0
    for item in items:
        total = total + item
    return total


def count_positives(items: list[int]) -> int:
    count: int = 0
    for item in items:
        if item > 0:
            count = count + 1
    return count
