"""In-process coverage of the shard guards, visible to the mutation self-check."""

import importlib
from unittest.mock import Mock

import pytest

from pytest_leela import engine
from pytest_leela.models import MutantResult
from pytest_leela.shard import Shard, ShardError


def test_shard_engine_accepts_completed_assignments(tmp_path, monkeypatch):
    target = tmp_path / 'calc.py'
    target.write_text('def value(x):\n    return x + 2\n')
    monkeypatch.setattr(engine, '_clean_process_state', lambda: None)
    monkeypatch.setattr(engine, '_prepare_source_bytecode', lambda *_: None)
    monkeypatch.setattr(engine.Engine, '_check_baseline', lambda *_: None)
    monkeypatch.setattr(Shard, 'resolve', lambda *_: {})
    monkeypatch.setattr(engine, 'run_tests_for_mutant',
                        lambda mutant, *_args, **_kwargs: MutantResult(mutant, True, 1, 'test', 0.0))
    shard = Shard(tmp_path / 'plan.json', tmp_path, index=0, count=2)
    result = engine.Engine(use_coverage=False).run([str(target)], test_node_ids=['test'], shard=shard)
    assert result.results and [m.mutant.mutant_id for m in result.results] == list(range(0, len(result.results) * 2, 2))


def test_plan_only_option_reaches_planner(tmp_path):
    plugin = importlib.import_module('pytest_leela.plugin')
    config = Mock(rootpath=tmp_path, inipath=None)
    options = {'leela_write_plan': 'plan.json', 'leela_shard_count': 2}
    config.getoption.side_effect = lambda key, default=None: options.get(key, default)
    shard = plugin._shard_options(config)
    assert shard is not None and shard.write and shard.count == 2


def test_empty_shard_is_not_an_ordinary_no_target_run(tmp_path, monkeypatch):
    plugin = importlib.import_module('pytest_leela.plugin')
    config = Mock(rootpath=tmp_path)
    config.getoption.side_effect = lambda key, default=None: default
    config.pluginmanager.hasplugin.return_value = False
    instance = plugin.LeelaPlugin(config, Shard(tmp_path / 'plan.json', tmp_path, write=True))
    monkeypatch.setattr(instance, '_resolve_target_files', lambda _: [])
    open_events = Mock(side_effect=ShardError('test: entered shard guard'))
    monkeypatch.setattr(plugin, 'open_mutant_events', open_events)
    session = Mock(config=config, items=[], exitstatus=0)
    instance.pytest_sessionfinish(session, exitstatus=0)
    open_events.assert_called_once_with(None)
    assert session.exitstatus == 1
