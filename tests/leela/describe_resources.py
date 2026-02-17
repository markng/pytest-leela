"""Tests for pytest_leela.resources — CPU and memory resource limiting."""

from unittest.mock import mock_open, patch

from pytest_leela.resources import (
    ResourceLimits,
    apply_cpu_limit,
    apply_limits,
    check_memory_usage,
    is_memory_ok,
)


def describe_resource_limits():
    def describe_effective_cores():
        def it_defaults_to_half_available_cores():
            limits = ResourceLimits()
            with patch("pytest_leela.resources.os.cpu_count", return_value=8):
                assert limits.effective_cores == 4

        def it_caps_at_max_cores_when_set():
            limits = ResourceLimits(max_cores=2)
            with patch("pytest_leela.resources.os.cpu_count", return_value=8):
                assert limits.effective_cores == 2

        def it_uses_available_when_max_cores_exceeds_available():
            limits = ResourceLimits(max_cores=16)
            with patch("pytest_leela.resources.os.cpu_count", return_value=4):
                assert limits.effective_cores == 4

        def it_returns_minimum_one_core_when_only_one_available():
            limits = ResourceLimits()
            with patch("pytest_leela.resources.os.cpu_count", return_value=1):
                assert limits.effective_cores == 1

        def it_falls_back_to_4_when_cpu_count_returns_none():
            limits = ResourceLimits()
            with patch("pytest_leela.resources.os.cpu_count", return_value=None):
                # available=4, default=4//2=2
                assert limits.effective_cores == 2


def describe_apply_cpu_limit():
    def it_calls_sched_setaffinity_with_correct_cores():
        with (
            patch("pytest_leela.resources.os.cpu_count", return_value=8),
            patch("pytest_leela.resources.os.sched_setaffinity") as mock_set,
        ):
            apply_cpu_limit(4)
            mock_set.assert_called_once_with(0, {0, 1, 2, 3})

    def it_caps_cores_to_available(self=None):
        with (
            patch("pytest_leela.resources.os.cpu_count", return_value=2),
            patch("pytest_leela.resources.os.sched_setaffinity") as mock_set,
        ):
            apply_cpu_limit(8)
            mock_set.assert_called_once_with(0, {0, 1})

    def it_uses_fallback_when_cpu_count_returns_none():
        """cpu_count() or 4: when None, should use 4."""
        with (
            patch("pytest_leela.resources.os.cpu_count", return_value=None),
            patch("pytest_leela.resources.os.sched_setaffinity") as mock_set,
        ):
            apply_cpu_limit(2)
            mock_set.assert_called_once_with(0, {0, 1})

    def it_handles_attribute_error():
        """Gracefully handles platforms without sched_setaffinity."""
        with (
            patch("pytest_leela.resources.os.cpu_count", return_value=4),
            patch(
                "pytest_leela.resources.os.sched_setaffinity",
                side_effect=AttributeError,
            ),
        ):
            # Should not raise
            apply_cpu_limit(2)


def describe_check_memory_usage():
    def it_returns_correct_percentage():
        """Verify arithmetic: (1 - available/total) * 100."""
        meminfo = (
            "MemTotal:       8000000 kB\n"
            "MemFree:        1000000 kB\n"
            "MemAvailable:   4000000 kB\n"
        )
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = check_memory_usage()
        # (1 - 4000000/8000000) * 100 = 50.0
        assert result == 50.0

    def it_returns_correct_for_high_usage():
        """Verify the subtraction and multiplication are correct."""
        meminfo = (
            "MemTotal:       10000000 kB\n"
            "MemFree:         100000 kB\n"
            "MemAvailable:    2000000 kB\n"
        )
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = check_memory_usage()
        # (1 - 2000000/10000000) * 100 = 80.0
        assert result == 80.0

    def it_returns_correct_for_low_usage():
        meminfo = (
            "MemTotal:       10000000 kB\n"
            "MemFree:        5000000 kB\n"
            "MemAvailable:    7500000 kB\n"
        )
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = check_memory_usage()
        # (1 - 7500000/10000000) * 100 = 25.0
        assert result == 25.0

    def it_returns_zero_on_os_error():
        with patch("builtins.open", side_effect=OSError):
            result = check_memory_usage()
        assert result == 0.0

    def it_returns_zero_when_mem_total_is_zero():
        """When mem_total is 0, should not divide by zero."""
        meminfo = (
            "MemTotal:       0 kB\n"
            "MemAvailable:   0 kB\n"
        )
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = check_memory_usage()
        assert result == 0.0

    def it_returns_non_negative_result():
        """Result should always be >= 0 (not negated)."""
        meminfo = (
            "MemTotal:       8000000 kB\n"
            "MemAvailable:   4000000 kB\n"
        )
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = check_memory_usage()
        assert result >= 0.0

    def it_returns_value_less_than_or_equal_100():
        meminfo = (
            "MemTotal:       8000000 kB\n"
            "MemAvailable:   0 kB\n"
        )
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = check_memory_usage()
        assert result == 100.0

    def it_returns_zero_on_missing_meminfo_fields():
        """When MemTotal line is missing, mem_total stays 0 → returns 0.0."""
        meminfo = "SomeOtherField: 1234 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = check_memory_usage()
        assert result == 0.0


def describe_apply_limits():
    def it_applies_cpu_limit_when_max_cores_set():
        limits = ResourceLimits(max_cores=2)
        with patch("pytest_leela.resources.apply_cpu_limit") as mock_apply:
            apply_limits(limits)
            mock_apply.assert_called_once_with(2)

    def it_does_not_apply_cpu_limit_when_max_cores_none():
        limits = ResourceLimits(max_cores=None)
        with patch("pytest_leela.resources.apply_cpu_limit") as mock_apply:
            apply_limits(limits)
            mock_apply.assert_not_called()


def describe_is_memory_ok():
    def it_returns_true_when_no_memory_limit_set():
        limits = ResourceLimits(max_memory_percent=None)
        assert is_memory_ok(limits) is True

    def it_returns_true_when_usage_below_limit():
        limits = ResourceLimits(max_memory_percent=90)
        with patch("pytest_leela.resources.check_memory_usage", return_value=50.0):
            assert is_memory_ok(limits) is True

    def it_returns_false_when_usage_exceeds_limit():
        limits = ResourceLimits(max_memory_percent=80)
        with patch("pytest_leela.resources.check_memory_usage", return_value=85.0):
            assert is_memory_ok(limits) is False

    def it_returns_false_when_usage_equals_limit():
        limits = ResourceLimits(max_memory_percent=80)
        with patch("pytest_leela.resources.check_memory_usage", return_value=80.0):
            assert is_memory_ok(limits) is False
