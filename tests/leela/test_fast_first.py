"""Opt-in mutant ordering and terminal reporting controls."""

from types import SimpleNamespace

import pytest

from pytest_leela.ast_analysis import find_mutation_points
from pytest_leela.models import Mutant
from pytest_leela.runner import InnerSession, ProjectModuleScope, _FastFirst, run_tests_for_mutant


def test_fast_first_keeps_all_items_and_stable_ties():
    parent = object()
    items = [SimpleNamespace(nodeid=name, parent=parent) for name in (
        'missing', 'slow', 'tie_b', 'tie_a', 'invalid', 'missing_again', 'zero', 'negative',
    )]
    original = items.copy()
    ordering = _FastFirst({'slow': 8, 'tie_a': 1, 'tie_b': 1, 'invalid': float('nan'),
                           'zero': 0.0, 'negative': -1.0})
    hook = ordering.pytest_collection_modifyitems(items)
    next(hook)
    assert items == original
    with pytest.raises(StopIteration):
        next(hook)
    assert items == [original[i] for i in (6, 2, 3, 1, 0, 4, 5, 7)]


@pytest.mark.parametrize(('enabled', 'selected', 'timed', 'expected'), [
    (False, True, True, False),
    (True, False, True, False),
    (True, True, False, False),
    (True, True, True, True),
])
def test_mutant_fast_first_requires_opt_in_selection_and_timings(
    monkeypatch, enabled, selected, timed, expected,
):
    nodeid = 'test_calc.py::test_a'
    recorded = []

    def fake_main(args, *, plugins):
        recorded.extend(plugins)
        return pytest.ExitCode.OK

    monkeypatch.setattr(pytest, 'main', fake_main)
    session = InnerSession(
        object(), [nodeid] if selected else None, None, ProjectModuleScope(),
        test_times={nodeid: 0.0} if timed else None, fast_first=enabled,
    )
    session._run_pytest()
    assert session.crash is None
    assert bool(recorded)
    assert any(isinstance(plugin, _FastFirst) for plugin in recorded) is expected


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


@pytest.mark.parametrize('failure_phase', ['setup', 'call', 'causal', 'unchanged', 'single'])
def test_fast_first_clean_control_does_not_credit_order_dependent_failure(tmp_path, monkeypatch, failure_phase):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    source = 'def neutral(value, zero):\n    return value + zero\n'
    target = tmp_path / 'neutral_target.py'
    target.write_text(source)
    tests = '''\
import pytest
from neutral_target import neutral

@pytest.fixture(scope='module')
def shared():
    return []

@pytest.fixture
def prepared(shared):
    if not shared:
        raise RuntimeError('ambient setup requires prior test')

def test_prepare(shared):
    shared.append(neutral(1, 0))
    assert shared == [1]

def test_consume(prepared):
    assert neutral(2, 0) == 2
'''
    if failure_phase == 'call':
        tests = tests.replace('def test_consume(prepared):', 'def test_consume(shared):\n    assert shared, "ambient call requires prior test"')
    elif failure_phase in {'causal', 'unchanged', 'single'}:
        tests = tests.replace('def test_consume(prepared):', 'def test_consume():')
        tests = tests.replace('neutral(2, 0) == 2', 'neutral(2, 1) == 3')
    (tmp_path / 'test_neutral.py').write_text(tests)
    point = next(p for p in find_mutation_points(source, str(target), 'neutral_target')
                 if p.node_type == 'BinOp' and p.original_op == 'Add')
    first, second = 'test_neutral.py::test_prepare', 'test_neutral.py::test_consume'
    selected = [second] if failure_phase == 'single' else [first, second]
    timings = {first: 1.0, second: 2.0} if failure_phase == 'unchanged' else {first: 2.0, second: 1.0}
    common = dict(test_ids=selected, test_dir=str(tmp_path), test_times=timings,
                  scope=ProjectModuleScope(str(tmp_path)))
    args = Mutant(point, 'Sub', 0), {'neutral_target': source}, {'neutral_target': str(target)}
    ordinary = run_tests_for_mutant(*args, **common)
    calls = []
    main = pytest.main

    def observe(argv, *, plugins):
        calls.append('mutant' if '-x' in argv else 'clean')
        return main(argv, plugins=plugins)

    monkeypatch.setattr(pytest, 'main', observe)
    ordered = run_tests_for_mutant(*args, **common, fast_first=True)
    assert calls == (['mutant'] if failure_phase in {'unchanged', 'single'} else ['mutant', 'clean'])
    if failure_phase in {'causal', 'unchanged', 'single'}:
        assert ordinary.status == ordered.status == 'killed'
        assert ordered.killing_test == second
    else:
        assert ordinary.status == 'survived'
        assert ordered.status == 'error', ordered
        assert 'control run also failed' in (ordered.error or '')


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
    expected = (
        'on (experimental; reordered kills can add a clean run)' if enabled
        else 'off (default; --leela-fast-first is experimental)'
    )
    assert f'Fast-first ordering: {expected}' in output
    assert output.index('test_calc.py::test_first PASSED') < output.index('test_calc.py::test_second PASSED')
