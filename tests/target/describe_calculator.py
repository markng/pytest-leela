"""Tests for target.calculator module."""

import pytest

from target.calculator import (
    add,
    subtract,
    multiply,
    integer_divide,
    negate,
    absolute,
    clamp,
    sum_of_squares,
    distance,
    average,
)


def describe_add():
    def it_adds_two_positive_integers():
        assert add(2, 3) == 5

    def it_adds_negative_numbers():
        assert add(-1, -2) == -3

    def it_returns_zero_for_zero_inputs():
        assert add(0, 0) == 0


def describe_subtract():
    def it_subtracts_two_positive_integers():
        assert subtract(5, 3) == 2

    def it_returns_negative_when_second_is_larger():
        assert subtract(3, 5) == -2

    def it_subtracts_zero():
        assert subtract(7, 0) == 7


def describe_multiply():
    def it_multiplies_two_positive_integers():
        assert multiply(3, 4) == 12

    def it_returns_zero_when_one_is_zero():
        assert multiply(5, 0) == 0

    def it_multiplies_negative_numbers():
        assert multiply(-2, -3) == 6

    def it_multiplies_positive_by_negative():
        assert multiply(3, -2) == -6


def describe_integer_divide():
    def it_divides_evenly():
        assert integer_divide(10, 2) == 5

    def it_truncates_toward_zero():
        assert integer_divide(7, 2) == 3

    def it_returns_zero_for_division_by_zero():
        assert integer_divide(5, 0) == 0

    def it_divides_negative_numbers():
        assert integer_divide(-6, 3) == -2


def describe_negate():
    def it_negates_positive():
        assert negate(5) == -5

    def it_negates_negative():
        assert negate(-3) == 3

    def it_negates_zero():
        assert negate(0) == 0


def describe_absolute():
    def it_returns_positive_for_negative_input():
        assert absolute(-5) == 5

    def it_returns_same_for_positive_input():
        assert absolute(5) == 5

    def it_returns_zero_for_zero():
        assert absolute(0) == 0


def describe_clamp():
    def it_returns_value_when_in_range():
        assert clamp(5, 1, 10) == 5

    def it_returns_low_when_value_below():
        assert clamp(-1, 0, 10) == 0

    def it_returns_high_when_value_above():
        assert clamp(15, 0, 10) == 10

    def it_returns_boundary_when_value_equals_low():
        assert clamp(0, 0, 10) == 0

    def it_returns_boundary_when_value_equals_high():
        assert clamp(10, 0, 10) == 10


def describe_sum_of_squares():
    def it_computes_for_positive_numbers():
        assert sum_of_squares(3, 4) == 25

    def it_computes_for_zero():
        assert sum_of_squares(0, 0) == 0

    def it_computes_for_negative_numbers():
        assert sum_of_squares(-2, 3) == 13


def describe_distance():
    def it_computes_horizontal_distance():
        assert distance(0, 0, 3, 0) == pytest.approx(3.0)

    def it_computes_diagonal_distance():
        assert distance(0, 0, 3, 4) == pytest.approx(5.0)

    def it_returns_zero_for_same_point():
        assert distance(1, 1, 1, 1) == pytest.approx(0.0)


def describe_average():
    def it_averages_two_positive_numbers():
        assert average(4.0, 6.0) == pytest.approx(5.0)

    def it_averages_positive_and_negative():
        assert average(-2.0, 2.0) == pytest.approx(0.0)

    def it_averages_two_zeros():
        assert average(0.0, 0.0) == pytest.approx(0.0)
