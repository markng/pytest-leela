"""Tests for target.assignment_targets — coverage for the realistic target module."""

from __future__ import annotations

import pytest

from target.assignment_targets import (
    accumulate_score,
    bounded_increment,
    build_greeting,
    build_report_line,
    classify_score,
    compute_area,
    compute_discount,
    compute_fibonacci_step,
    count_items,
    format_tag,
    join_parts,
    pack_rgb,
    repeat_separator,
    score_attempt,
    slugify,
    sum_range,
)


def describe_build_greeting():
    def it_joins_label_separator_name_and_suffix():
        assert build_greeting("Alice", "!") == "Hello, Alice!"

    def it_uses_empty_suffix():
        assert build_greeting("Bob", "") == "Hello, Bob"

    def it_uses_empty_name():
        assert build_greeting("", "?") == "Hello, ?"


def describe_slugify():
    def it_concatenates_text_and_replacement():
        assert slugify("hello world", "-") == "hello world-"

    def it_handles_empty_replacement():
        assert slugify("test", "") == "test"

    def it_handles_empty_text():
        assert slugify("", "/") == "/"


def describe_format_tag():
    def it_formats_key_value_with_prefix():
        assert format_tag("env", "prod") == "tag:env=prod"

    def it_handles_empty_key_and_value():
        assert format_tag("", "") == "tag:="

    def it_handles_numeric_looking_value():
        assert format_tag("port", "8080") == "tag:port=8080"


def describe_repeat_separator():
    def it_builds_separator_with_base_prefix():
        assert repeat_separator(">", 3) == "->->->"

    def it_handles_zero_times():
        assert repeat_separator("+", 0) == ""

    def it_handles_single_repetition():
        assert repeat_separator(".", 1) == "-."


def describe_join_parts():
    def it_joins_three_parts_with_pipe():
        assert join_parts("a", "b", "c") == "a | b | c"

    def it_handles_empty_parts():
        assert join_parts("", "", "") == " |  | "

    def it_handles_longer_strings():
        assert join_parts("foo", "bar", "baz") == "foo | bar | baz"


def describe_compute_area():
    def it_multiplies_width_and_height():
        assert compute_area(4, 5) == 20

    def it_returns_zero_for_zero_dimension():
        assert compute_area(0, 10) == 0

    def it_handles_equal_dimensions():
        assert compute_area(7, 7) == 49


def describe_accumulate_score():
    def it_computes_score_with_bonus_and_multiplier():
        assert accumulate_score(10, 2, 5) == 30  # (10+5)*2

    def it_returns_zero_for_all_zero_inputs():
        assert accumulate_score(0, 1, 0) == 0

    def it_handles_zero_multiplier():
        assert accumulate_score(100, 0, 50) == 0


def describe_count_items():
    def it_divides_count_by_batch_size():
        assert count_items(10, 3) == 3  # 10 // 3

    def it_returns_zero_for_zero_items():
        assert count_items(0, 5) == 0

    def it_handles_exact_division():
        assert count_items(12, 4) == 3


def describe_compute_discount():
    def it_subtracts_percentage_discount():
        assert compute_discount(100, 20) == 80  # 100 - (100*20)//100

    def it_returns_full_price_for_zero_discount():
        assert compute_discount(50, 0) == 50

    def it_handles_fifty_percent_discount():
        assert compute_discount(200, 50) == 100


def describe_bounded_increment():
    def it_adds_value_and_step():
        assert bounded_increment(5, 3, 100) == 8

    def it_clamps_to_maximum():
        assert bounded_increment(90, 20, 100) == 100

    def it_handles_zero_step():
        assert bounded_increment(42, 0, 100) == 42

    def it_clamps_exactly_at_maximum():
        assert bounded_increment(50, 50, 100) == 100


def describe_sum_range():
    def it_sums_range_from_one_to_ten():
        # sum(1..10) = 55; formula: count=9, total += 9*(1+10)=99, total=99//2=49 (integer arith)
        # Actually: count=10-1=9, total += 9*(1+10)=99, total=99//2=49
        result = sum_range(1, 10)
        assert result == 49

    def it_returns_zero_for_empty_range():
        assert sum_range(5, 5) == 0

    def it_handles_range_of_two():
        # count=1, total += 1*(2+3)=5, total=5//2=2
        assert sum_range(2, 3) == 2


def describe_build_report_line():
    def it_formats_label_with_sep_and_pct():
        assert build_report_line("Score", 75, 100) == "Score: %"

    def it_handles_zero_count():
        assert build_report_line("Items", 0, 10) == "Items: %"

    def it_handles_empty_label():
        assert build_report_line("", 50, 100) == ": %"


def describe_score_attempt():
    def it_penalises_wrong_answers():
        assert score_attempt(10, 3, 1) == 5  # 10 - 3*2 + 0 + 1 = 5

    def it_handles_no_wrong_answers():
        assert score_attempt(8, 0, 2) == 10  # 8 - 0 + 2

    def it_handles_all_skipped():
        assert score_attempt(0, 0, 5) == 5


def describe_classify_score():
    def it_returns_a_for_ninety_or_above():
        assert classify_score(95) == "A"
        assert classify_score(90) == "A"

    def it_returns_b_for_eighty_to_eighty_nine():
        assert classify_score(85) == "B"
        assert classify_score(80) == "B"

    def it_returns_c_for_seventy_to_seventy_nine():
        assert classify_score(75) == "C"
        assert classify_score(70) == "C"

    def it_returns_f_for_below_seventy():
        assert classify_score(69) == "F"
        assert classify_score(0) == "F"


def describe_compute_fibonacci_step():
    def it_returns_sum_of_a_and_b():
        assert compute_fibonacci_step(3, 5) == 8

    def it_handles_zero_inputs():
        assert compute_fibonacci_step(0, 0) == 0

    def it_handles_first_step():
        assert compute_fibonacci_step(0, 1) == 1


def describe_pack_rgb():
    def it_packs_rgb_into_integer():
        # r * 16 + g * 8 + b
        assert pack_rgb(2, 3, 4) == 2 * 16 + 3 * 8 + 4  # 32 + 24 + 4 = 60

    def it_handles_all_zeros():
        assert pack_rgb(0, 0, 0) == 0

    def it_handles_single_channel():
        assert pack_rgb(1, 0, 0) == 16
