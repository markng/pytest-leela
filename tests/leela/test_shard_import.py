"""The shard verifier must not run its CLI when imported as a library."""

import importlib
import sys

import pytest
import pytest_leela


def test_importing_shard_does_not_parse_cli_arguments(monkeypatch):
    # Force an import during the test call so Leela can select this test for
    # import-time mutations instead of falling back to unrelated collection.
    monkeypatch.delitem(sys.modules, 'pytest_leela.shard', raising=False)
    monkeypatch.delattr(pytest_leela, 'shard', raising=False)
    monkeypatch.setattr(sys, 'argv', ['pytest_leela.shard'])
    try:
        module = importlib.import_module('pytest_leela.shard')
    except SystemExit as exc:
        pytest.fail(f'shard CLI ran on import (exit {exc.code})')
    assert callable(module.main)
