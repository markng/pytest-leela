"""Shared test hooks for pytest-leela's own suite."""

import sys

import pytest


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """Put back any module a test evicted from ``sys.modules``.

    Many tests exercise leela's real eviction code.  Under a self-mutation
    that widens eviction (e.g. stdlib), the evicted ``warnings`` module
    would crash pytest's own teardown before the test pinning that contract
    gets to run and fail.
    """
    saved = dict(sys.modules)
    try:
        return (yield)
    finally:
        for name, module in saved.items():
            sys.modules.setdefault(name, module)
