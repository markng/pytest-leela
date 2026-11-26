"""Opt-in mutant ordering and terminal reporting controls."""

from types import SimpleNamespace

import pytest

from pytest_leela.runner import _FastFirst


def test_fast_first_keeps_all_items_and_stable_ties():
    items = [SimpleNamespace(nodeid=name) for name in (
        'missing', 'slow', 'tie_b', 'tie_a', 'invalid', 'missing_again',
    )]
    original = items.copy()
    ordering = _FastFirst({'slow': 8, 'tie_a': 1, 'tie_b': 1, 'invalid': float('nan')})
    hook = ordering.pytest_collection_modifyitems(items)
    next(hook)
    assert items == original
    with pytest.raises(StopIteration):
        next(hook)
    assert items == [original[i] for i in (2, 3, 1, 0, 4, 5)]


@pytest.mark.parametrize('enabled', [False, True])
def test_fast_first_summary_once_without_changing_outer_order(pytester, enabled):
    pytester.makepyfile(calc='def double(x):\n    return x * 2')
    pytester.makepyfile(test_calc='''
        from calc import double

        def test_first():
            assert double(0) == 0

        def test_second():
            assert double(3) == 6
    ''')
    flags = ['--leela-fast-first'] if enabled else []
    result = pytester.runpytest_subprocess(
        '-v', '--leela', '--target', 'calc.py', '-p', 'no:cacheprovider', *flags,
    )
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=2)
    output = result.stdout.str()
    assert output.count('Fast-first ordering:') == 1
    expected = 'on' if enabled else 'off (default); try --leela-fast-first'
    assert f'Fast-first ordering: {expected}' in output
    assert output.index('test_calc.py::test_first PASSED') < output.index('test_calc.py::test_second PASSED')
