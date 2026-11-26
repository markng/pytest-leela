"""Opt-in mutant ordering and terminal reporting controls."""

from types import SimpleNamespace

import pytest

from pytest_leela.runner import _FastFirst


def test_fast_first_keeps_all_items_and_stable_ties():
    parent = object()
    items = [SimpleNamespace(nodeid=name, parent=parent) for name in (
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


def test_fast_first_preserves_parent_runs_and_parameter_barriers():
    first, second = object(), object()
    items = [SimpleNamespace(nodeid=name, parent=parent, callspec=call) for name, parent, call in (
        ('slow', first, None), ('fast', first, None), ('param', first, object()),
        ('after', first, None), ('other', second, None), ('return', first, None),
    )]
    hook = _FastFirst({'slow': 9, 'fast': 2, 'param': 0, 'after': 1,
                       'other': 0, 'return': 0}).pytest_collection_modifyitems(items)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)
    assert [item.nodeid for item in items] == ['fast', 'slow', 'param', 'after', 'other', 'return']


@pytest.mark.parametrize('global_sort', [True, False])
def test_module_fixture_setup_count(pytester, global_sort):
    pytester.makeconftest('''
        import pytest
        from pathlib import Path
        from pytest_leela.runner import _FastFirst

        times = {
            'test_a.py::test_one': 3, 'test_a.py::test_two': 1,
            'test_b.py::test_one': 4, 'test_b.py::test_two': 2,
        }

        @pytest.fixture(scope='module', autouse=True)
        def setup(request):
            with Path('setups.txt').open('a') as log:
                log.write(request.module.__name__ + '\\n')
    ''' + ('''
        def pytest_collection_modifyitems(items):
            items.sort(key=lambda item: times[item.nodeid])
    ''' if global_sort else '''
        pytest_collection_modifyitems = _FastFirst(times).pytest_collection_modifyitems
    '''))
    pytester.makepyfile(
        test_a='def test_one(): pass\ndef test_two(): pass',
        test_b='def test_one(): pass\ndef test_two(): pass',
    )
    result = pytester.runpytest_subprocess('-v', '-p', 'no:cacheprovider')
    result.assert_outcomes(passed=4)
    setups = (pytester.path / 'setups.txt').read_text().splitlines()
    assert setups == (['test_a', 'test_b'] * 2 if global_sort else ['test_a', 'test_b'])
    output = result.stdout.str()
    assert output.index('test_a.py::test_two PASSED') < output.index('test_a.py::test_one PASSED')


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
