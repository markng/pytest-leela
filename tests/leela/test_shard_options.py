"""Exercise shard arguments through pytest's real configure and collection hooks."""

import json

import pytest


@pytest.fixture
def plan_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD', '1')
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    monkeypatch.delenv('PYTEST_PLUGINS', raising=False)
    target = tmp_path / 'calc.py'
    target.write_text('def double(x):\n    return x * 2\n')
    (tmp_path / 'test_calc.py').write_text('from calc import double\n\ndef test_double():\n    assert double(3) == 6\n')
    (tmp_path / 'pytest.ini').write_text('[pytest]\n')

    def run(*options):
        collected = []

        class Collection:
            def pytest_collection_finish(self, session):
                collected.extend(item.nodeid for item in session.items)

        status = pytest.main(
            ['-p', 'pytest_leela.plugin', '-p', 'no:cacheprovider', '-q', '--target', str(target), *options],
            plugins=[Collection()],
        )
        return status, collected

    return run


def test_plan_options_write_after_collection(plan_cli, tmp_path):
    assert plan_cli('--leela-write-plan=plan.json', '--leela-shard-count=1') == (
        pytest.ExitCode.OK, ['test_calc.py::test_double'],
    )
    plan = json.loads((tmp_path / 'plan.json').read_text())
    assert plan['count'] == 1 and plan['runnable'] > 0
    assert not list(tmp_path.glob('*.jsonl'))


@pytest.mark.parametrize(
    ('options', 'diagnostic'),
    [
        (['--leela-write-plan=plan.json', '--leela-shard-count=1', '--leela-fast-first'], 'without diff, fast-first'),
        (['--leela-shard-count=1'], 'use --leela-write-plan'),
        (['--leela-write-plan=plan.json', '--leela-shard-count=1', '--leela-plan=other.json'], 'use --leela-write-plan'),
        (['--leela-write-plan=plan.json', '--leela-shard-count=1', '--leela-shard=0/1'], 'use --leela-write-plan'),
        (['--leela-write-plan=plan.json'], 'use --leela-write-plan'),
        (['--leela-write-plan=plan.json', '--leela-shard-count=0'], 'use --leela-write-plan'),
        (['--leela-write-plan=plan.json', '--leela-shard-count=1', '--leela-jsonl=events.jsonl'], 'does not emit mutant events'),
    ],
    ids=['incompatible-mode', 'no-write-path', 'read-and-write', 'index-on-writer', 'missing-count', 'zero-count', 'writer-jsonl'],
)
def test_invalid_plan_options_stop_before_collection(plan_cli, tmp_path, capsys, options, diagnostic):
    assert plan_cli(*options) == (pytest.ExitCode.USAGE_ERROR, [])
    assert diagnostic in capsys.readouterr().err
    assert not (tmp_path / 'plan.json').exists()
    assert not (tmp_path / 'events.jsonl').exists()
