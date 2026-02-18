"""Tests for target.validators module."""

from target.validators import (
    is_positive,
    is_negative,
    is_zero,
    is_even,
    is_odd,
    is_in_range,
    is_valid_percentage,
    is_valid_age,
    validate_name,
    validate_email_simple,
    is_non_negative,
    clamp_to_range,
)


def describe_is_positive():
    def it_returns_true_for_positive():
        assert is_positive(1) is True

    def it_returns_false_for_zero():
        assert is_positive(0) is False

    def it_returns_false_for_negative():
        assert is_positive(-1) is False


def describe_is_negative():
    def it_returns_true_for_negative():
        assert is_negative(-1) is True

    def it_returns_false_for_zero():
        assert is_negative(0) is False

    def it_returns_false_for_positive():
        assert is_negative(1) is False


def describe_is_zero():
    def it_returns_true_for_zero():
        assert is_zero(0) is True

    def it_returns_false_for_non_zero():
        assert is_zero(1) is False


def describe_is_even():
    def it_returns_true_for_even():
        assert is_even(4) is True

    def it_returns_false_for_odd():
        assert is_even(3) is False

    def it_returns_true_for_zero():
        assert is_even(0) is True


def describe_is_odd():
    def it_returns_true_for_odd():
        assert is_odd(3) is True

    def it_returns_false_for_even():
        assert is_odd(4) is False

    def it_returns_false_for_zero():
        assert is_odd(0) is False


def describe_is_in_range():
    def it_returns_true_when_in_range():
        assert is_in_range(5, 1, 10) is True

    def it_returns_true_at_low_boundary():
        assert is_in_range(1, 1, 10) is True

    def it_returns_true_at_high_boundary():
        assert is_in_range(10, 1, 10) is True

    def it_returns_false_below_range():
        assert is_in_range(0, 1, 10) is False

    def it_returns_false_above_range():
        assert is_in_range(11, 1, 10) is False


def describe_is_valid_percentage():
    def it_returns_true_for_valid_percentage():
        assert is_valid_percentage(50.0) is True

    def it_returns_true_at_zero():
        assert is_valid_percentage(0.0) is True

    def it_returns_true_at_hundred():
        assert is_valid_percentage(100.0) is True

    def it_returns_false_below_zero():
        assert is_valid_percentage(-0.1) is False

    def it_returns_false_above_hundred():
        assert is_valid_percentage(100.1) is False


def describe_is_valid_age():
    def it_returns_true_for_valid_age():
        assert is_valid_age(25) is True

    def it_returns_true_at_zero():
        assert is_valid_age(0) is True

    def it_returns_true_at_max():
        assert is_valid_age(150) is True

    def it_returns_false_for_negative():
        assert is_valid_age(-1) is False

    def it_returns_false_above_max():
        assert is_valid_age(151) is False


def describe_validate_name():
    def it_returns_name_when_valid():
        assert validate_name("Alice") == "Alice"

    def it_returns_none_for_empty():
        assert validate_name("") is None

    def it_returns_none_for_too_long():
        assert validate_name("a" * 101) is None

    def it_returns_name_at_max_length():
        assert validate_name("a" * 100) == "a" * 100


def describe_validate_email_simple():
    def it_returns_true_for_valid_email():
        assert validate_email_simple("a@b.com") is True

    def it_returns_false_without_at():
        assert validate_email_simple("ab.com") is False

    def it_returns_false_without_dot():
        assert validate_email_simple("a@bcom") is False


def describe_is_non_negative():
    def it_returns_true_for_positive():
        assert is_non_negative(1) is True

    def it_returns_true_for_zero():
        assert is_non_negative(0) is True

    def it_returns_false_for_negative():
        assert is_non_negative(-1) is False


def describe_clamp_to_range():
    def it_returns_value_when_in_range():
        assert clamp_to_range(5, 0, 10) == 5

    def it_returns_low_when_below():
        assert clamp_to_range(-1, 0, 10) == 0

    def it_returns_high_when_above():
        assert clamp_to_range(15, 0, 10) == 10

    def it_returns_value_at_low_boundary():
        assert clamp_to_range(0, 0, 10) == 0

    def it_returns_value_at_high_boundary():
        assert clamp_to_range(10, 0, 10) == 10
