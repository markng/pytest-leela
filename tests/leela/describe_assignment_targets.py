"""Tests for the assignment-dataflow target module.

These tests provide full coverage for tests/target/describe_assignment_targets.py,
ensuring every branch and expression is exercised so mutation testing can run
against the target.
"""

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
    def it_returns_hello_with_name_and_suffix():
        result = build_greeting("World", "!")
        assert result == "Hello, World!"

    def it_works_with_empty_suffix():
        result = build_greeting("Alice", "")
        assert result == "Hello, Alice"

    def it_works_with_question_suffix():
        result = build_greeting("Bob", "?")
        assert result == "Hello, Bob?"


def describe_slugify():
    def it_joins_text_and_replacement():
        result = slugify("hello world", "-")
        assert result == "hello world-"

    def it_works_with_underscore():
        result = slugify("foo bar", "_")
        assert result == "foo bar_"


def describe_format_tag():
    def it_formats_key_value_pair():
        result = format_tag("env", "prod")
        assert result == "tag:env=prod"

    def it_formats_with_different_values():
        result = format_tag("region", "us-east")
        assert result == "tag:region=us-east"


def describe_repeat_separator():
    def it_creates_repeated_separator():
        result = repeat_separator("=", 3)
        assert result == "-=-=-="  # ("-" + "=") * 3

    def it_works_with_zero_times():
        result = repeat_separator("*", 0)
        assert result == ""

    def it_works_with_one_time():
        result = repeat_separator("~", 1)
        assert result == "-~"


def describe_join_parts():
    def it_joins_three_parts_with_separator():
        result = join_parts("a", "b", "c")
        assert result == "a | b | c"

    def it_works_with_longer_strings():
        result = join_parts("foo", "bar", "baz")
        assert result == "foo | bar | baz"


def describe_compute_area():
    def it_returns_product_of_width_and_height():
        result = compute_area(4, 5)
        assert result == 20

    def it_returns_zero_for_zero_dimension():
        result = compute_area(0, 10)
        assert result == 0

    def it_works_with_one_by_one():
        result = compute_area(1, 1)
        assert result == 1


def describe_accumulate_score():
    def it_applies_multiplier_to_accumulated_score():
        # score = (base_points + bonus) * multiplier
        result = accumulate_score(10, 3, 5)
        assert result == 45  # (10 + 5) * 3

    def it_works_with_zero_bonus():
        result = accumulate_score(10, 2, 0)
        assert result == 20

    def it_works_with_multiplier_of_one():
        result = accumulate_score(7, 1, 3)
        assert result == 10


def describe_count_items():
    def it_computes_number_of_batches():
        result = count_items(10, 3)
        assert result == 3  # 10 // 3

    def it_returns_zero_for_fewer_items_than_batch():
        result = count_items(2, 5)
        assert result == 0

    def it_handles_exact_multiple():
        result = count_items(9, 3)
        assert result == 3


def describe_compute_discount():
    def it_applies_percentage_discount():
        # price=100, discount_pct=20 → net=20, result=80
        result = compute_discount(100, 20)
        assert result == 80

    def it_handles_no_discount():
        result = compute_discount(50, 0)
        assert result == 50

    def it_handles_full_discount():
        result = compute_discount(200, 100)
        assert result == 0


def describe_bounded_increment():
    def it_increments_within_bounds():
        result = bounded_increment(5, 3, 100)
        assert result == 8  # 0 + 5 + 3

    def it_clamps_to_maximum():
        result = bounded_increment(50, 60, 100)
        assert result == 100

    def it_returns_maximum_when_exceeded():
        result = bounded_increment(90, 20, 100)
        assert result == 100

    def it_returns_exact_maximum():
        result = bounded_increment(50, 50, 100)
        assert result == 100


def describe_sum_range():
    def it_sums_a_range():
        # count = end - start = 9, total = 9 * (1 + 10) = 99, total //2 = 49
        result = sum_range(1, 10)
        assert result == 49

    def it_handles_adjacent_values():
        # count = 1, total = 1 * (3 + 4) = 7, //2 = 3
        result = sum_range(3, 4)
        assert result == 3

    def it_handles_zero_range():
        # count = 0, total = 0 * n = 0, //2 = 0
        result = sum_range(5, 5)
        assert result == 0


def describe_build_report_line():
    def it_builds_label_with_percentage():
        # 5 * 100 // 10 = 50
        result = build_report_line("items", 5, 10)
        assert result == "items: 50%"

    def it_works_with_different_labels():
        # 8 * 100 // 10 = 80
        result = build_report_line("score", 8, 10)
        assert result == "score: 80%"


def describe_score_attempt():
    def it_scores_with_penalty_for_wrong():
        # correct=5, wrong=2, skipped=1
        # raw = 5 - 2*2 = 1, bonus = 1, final = 2
        result = score_attempt(5, 2, 1)
        assert result == 2

    def it_handles_all_correct():
        result = score_attempt(10, 0, 0)
        assert result == 10

    def it_handles_negative_score():
        result = score_attempt(0, 5, 0)
        assert result == -10

    def it_adds_skipped_as_bonus():
        result = score_attempt(3, 0, 2)
        assert result == 5


def describe_classify_score():
    def it_returns_a_for_90_and_above():
        assert classify_score(90) == "A"
        assert classify_score(100) == "A"
        assert classify_score(95) == "A"

    def it_returns_b_for_80_to_89():
        assert classify_score(80) == "B"
        assert classify_score(85) == "B"
        assert classify_score(89) == "B"

    def it_returns_c_for_70_to_79():
        assert classify_score(70) == "C"
        assert classify_score(75) == "C"
        assert classify_score(79) == "C"

    def it_returns_f_for_below_70():
        assert classify_score(69) == "F"
        assert classify_score(0) == "F"
        assert classify_score(50) == "F"


def describe_compute_fibonacci_step():
    def it_returns_sum_of_two_values():
        result = compute_fibonacci_step(3, 5)
        assert result == 8

    def it_handles_zero_values():
        result = compute_fibonacci_step(0, 1)
        assert result == 1

    def it_handles_equal_values():
        result = compute_fibonacci_step(4, 4)
        assert result == 8


def describe_pack_rgb():
    def it_packs_rgb_channels():
        # r=1, g=0, b=0 → 1*16 + 0*8 + 0 = 16
        result = pack_rgb(1, 0, 0)
        assert result == 16

    def it_packs_all_channels():
        # r=1, g=1, b=1 → 16 + 8 + 1 = 25
        result = pack_rgb(1, 1, 1)
        assert result == 25

    def it_handles_zero_inputs():
        result = pack_rgb(0, 0, 0)
        assert result == 0

    def it_packs_larger_values():
        # r=2, g=3, b=5 → 2*16 + 3*8 + 5 = 32 + 24 + 5 = 61
        result = pack_rgb(2, 3, 5)
        assert result == 61
