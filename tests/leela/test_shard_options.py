"""Exercise shard arguments through pytest's real configure and collection hooks."""

import hashlib
import importlib
import json

import pytest


@pytest.fixture
def plan_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD', '1')
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    monkeypatch.delenv('PYTEST_PLUGINS', raising=False)
    # Each nested session needs its own names: the clean baseline and mutant
    # may restore modules from earlier pytest runs with a different tmp_path.
    suffix = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:16]
    module = f'calc_{suffix}'
    target = tmp_path / f'{module}.py'
    target.write_text('def double(x):\n    return x * 2\n')
    (tmp_path / f'test_{module}.py').write_text(f'from {module} import double\n\ndef test_double():\n    assert double(3) == 6\n')
    node = f'test_{module}.py::test_double'
    (tmp_path / 'pytest.ini').write_text('[pytest]\n')

    def run(*options, stop_after_collection=False):
        collected = []

        class Collection:
            def pytest_collection_finish(self, session):
                collected.extend(item.nodeid for item in session.items)
                if stop_after_collection:
                    registered = session.config.pluginmanager.get_plugin('leela-plugin')
                    assert registered is not None
                    shard = registered._shard
                    assert shard is not None, 'reader shard was not configured'
                    assert (shard.path.name, shard.index, shard.count, shard.write) == ('plan.json', 0, 1, False)
                    pytest.exit('reader configuration accepted', returncode=pytest.ExitCode.INTERRUPTED)

        plugin = importlib.import_module('pytest_leela.plugin')
        status = pytest.main(
            ['-p', 'no:leela', '-p', 'no:cacheprovider', '-q', '--target', str(target), *options],
            # Reuse the module selected by the enclosing run. Asking the nested
            # plugin manager to import by name can load unmutated source via its
            # new assertion rewriter instead of the enclosing mutation finder.
            plugins=[plugin, Collection()],
        )
        return status, collected

    return run, node


def test_plan_options_write_after_collection(plan_cli, tmp_path):
    run, node = plan_cli
    assert run('--leela-write-plan=plan.json', '--leela-shard-count=1') == (
        pytest.ExitCode.OK, [node],
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
        (['--leela-plan=plan.json', '--leela-shard=0/1', '--leela-shard-count=1', '--leela-jsonl=events.jsonl'], 'use --leela-write-plan'),
    ],
    ids=['incompatible-mode', 'no-write-path', 'read-and-write', 'index-on-writer', 'missing-count', 'zero-count', 'writer-jsonl', 'reader-with-count'],
)
def test_invalid_plan_options_stop_before_collection(plan_cli, tmp_path, capsys, options, diagnostic):
    run, _ = plan_cli
    assert run(*options) == (pytest.ExitCode.USAGE_ERROR, [])
    assert diagnostic in capsys.readouterr().err
    assert not (tmp_path / 'plan.json').exists()
    assert not (tmp_path / 'events.jsonl').exists()


def test_reader_rejects_index_equal_to_shard_count(plan_cli, tmp_path, capsys):
    run, node = plan_cli
    opts = ('--leela-plan=plan.json', '--leela-jsonl=events.jsonl')
    assert run(*opts, '--leela-shard=1/1') == (pytest.ExitCode.USAGE_ERROR, [])
    assert 'use --leela-write-plan' in capsys.readouterr().err
    assert not (tmp_path / 'events.jsonl').exists()
    assert run(*opts, '--leela-shard=0/1', stop_after_collection=True) == (
        pytest.ExitCode.INTERRUPTED, [node],
    )
    assert not (tmp_path / 'events.jsonl').exists()


def test_plan_reader_reaches_collection(plan_cli, tmp_path):
    run, node = plan_cli
    assert run('--leela-plan=plan.json', '--leela-shard=0/1', '--leela-jsonl=events.jsonl',
               stop_after_collection=True) == (pytest.ExitCode.INTERRUPTED, [node])
    assert not (tmp_path / 'events.jsonl').exists()
