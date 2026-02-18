"""Tests for target.collections_utils module."""

from target.collections_utils import (
    is_non_empty,
    first_or_none,
    last_or_none,
    contains,
    safe_get,
    merge_dicts,
    keys_with_value,
    sum_values,
    count_positives,
)


def describe_is_non_empty():
    def it_returns_true_for_non_empty_list():
        assert is_non_empty([1, 2]) is True

    def it_returns_false_for_empty_list():
        assert is_non_empty([]) is False


def describe_first_or_none():
    def it_returns_first_element():
        assert first_or_none([10, 20, 30]) == 10

    def it_returns_none_for_empty_list():
        assert first_or_none([]) is None

    def it_returns_only_element():
        assert first_or_none([42]) == 42


def describe_last_or_none():
    def it_returns_last_element():
        assert last_or_none([10, 20, 30]) == 30

    def it_returns_none_for_empty_list():
        assert last_or_none([]) is None

    def it_returns_only_element():
        assert last_or_none([42]) == 42


def describe_contains():
    def it_finds_item_in_list():
        assert contains([1, 2, 3], 2) is True

    def it_returns_false_when_not_found():
        assert contains([1, 2, 3], 4) is False

    def it_returns_false_for_empty_list():
        assert contains([], 1) is False


def describe_safe_get():
    def it_returns_element_at_valid_index():
        assert safe_get([10, 20, 30], 1) == 20

    def it_returns_none_for_out_of_range_index():
        assert safe_get([10, 20], 5) is None

    def it_returns_none_for_negative_index():
        assert safe_get([10, 20], -1) is None

    def it_returns_first_element_at_zero():
        assert safe_get([10, 20], 0) == 10

    def it_returns_none_for_empty_list():
        assert safe_get([], 0) is None


def describe_merge_dicts():
    def it_merges_two_dicts():
        assert merge_dicts({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def it_second_dict_overrides_first():
        assert merge_dicts({"a": 1}, {"a": 2}) == {"a": 2}

    def it_merges_with_empty_dict():
        assert merge_dicts({"a": 1}, {}) == {"a": 1}


def describe_keys_with_value():
    def it_finds_keys_matching_value():
        result = keys_with_value({"a": 1, "b": 2, "c": 1}, 1)
        assert sorted(result) == ["a", "c"]

    def it_returns_empty_when_no_match():
        assert keys_with_value({"a": 1}, 2) == []

    def it_returns_empty_for_empty_dict():
        assert keys_with_value({}, 1) == []


def describe_sum_values():
    def it_sums_positive_numbers():
        assert sum_values([1, 2, 3]) == 6

    def it_returns_zero_for_empty_list():
        assert sum_values([]) == 0

    def it_sums_with_negatives():
        assert sum_values([1, -2, 3]) == 2


def describe_count_positives():
    def it_counts_positive_numbers():
        assert count_positives([1, -2, 3, 0, 5]) == 3

    def it_returns_zero_for_empty_list():
        assert count_positives([]) == 0

    def it_returns_zero_when_no_positives():
        assert count_positives([-1, -2, 0]) == 0

    def it_excludes_zero():
        assert count_positives([0]) == 0
