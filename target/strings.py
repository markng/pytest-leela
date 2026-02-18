"""String operations with strict type annotations."""

from __future__ import annotations


def greet(name: str) -> str:
    return "Hello, " + name + "!"


def repeat(text: str, times: int) -> str:
    return text * times


def is_empty(text: str) -> bool:
    return len(text) == 0


def is_not_empty(text: str) -> bool:
    return len(text) > 0


def truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def contains_word(text: str, word: str) -> bool:
    return word in text


def first_char(text: str) -> str | None:
    if len(text) == 0:
        return None
    return text[0]


def safe_upper(text: str | None) -> str:
    return (text or "").upper()


def pad_left(text: str, width: int, char: str) -> str:
    return text.rjust(width, char)
