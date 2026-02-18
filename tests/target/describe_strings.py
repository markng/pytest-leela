"""Tests for target.strings module."""

from target.strings import (
    greet,
    repeat,
    is_empty,
    is_not_empty,
    truncate,
    contains_word,
    first_char,
    safe_upper,
    pad_left,
)


def describe_greet():
    def it_greets_a_name():
        assert greet("Alice") == "Hello, Alice!"

    def it_greets_empty_string():
        assert greet("") == "Hello, !"


def describe_repeat():
    def it_repeats_text_multiple_times():
        assert repeat("ab", 3) == "ababab"

    def it_returns_empty_for_zero_times():
        assert repeat("hi", 0) == ""

    def it_repeats_once():
        assert repeat("x", 1) == "x"


def describe_is_empty():
    def it_returns_true_for_empty_string():
        assert is_empty("") is True

    def it_returns_false_for_non_empty_string():
        assert is_empty("a") is False


def describe_is_not_empty():
    def it_returns_true_for_non_empty_string():
        assert is_not_empty("hello") is True

    def it_returns_false_for_empty_string():
        assert is_not_empty("") is False


def describe_truncate():
    def it_returns_text_when_within_limit():
        assert truncate("hello", 10) == "hello"

    def it_truncates_long_text():
        assert truncate("hello world", 5) == "hello..."

    def it_returns_text_when_exactly_at_limit():
        assert truncate("hello", 5) == "hello"

    def it_truncates_when_one_over_limit():
        assert truncate("hello!", 5) == "hello..."


def describe_contains_word():
    def it_finds_word_in_text():
        assert contains_word("hello world", "world") is True

    def it_returns_false_when_not_found():
        assert contains_word("hello world", "xyz") is False

    def it_finds_empty_word():
        assert contains_word("hello", "") is True


def describe_first_char():
    def it_returns_first_character():
        assert first_char("hello") == "h"

    def it_returns_none_for_empty_string():
        assert first_char("") is None


def describe_safe_upper():
    def it_uppercases_a_string():
        assert safe_upper("hello") == "HELLO"

    def it_returns_empty_for_none():
        assert safe_upper(None) == ""

    def it_returns_empty_for_empty_string():
        assert safe_upper("") == ""


def describe_pad_left():
    def it_pads_short_text():
        assert pad_left("hi", 5, "*") == "***hi"

    def it_returns_text_when_already_wide_enough():
        assert pad_left("hello", 3, "*") == "hello"

    def it_returns_text_when_exactly_at_width():
        assert pad_left("hello", 5, "*") == "hello"
