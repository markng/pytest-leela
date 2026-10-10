"""Executable discovery contracts: no plain test twins or implicit plugins."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pytest_leela.coverage_tracker import collect_coverage_subprocess

ROOT = Path(__file__).resolve().parents[2]


def _coverage_collector():
    return importlib.import_module('pytest_leela.coverage_collector')


def describe_coverage_collector():
    @pytest.mark.parametrize(
        'name, suffix',
        [('test_plain.py', '.py'), ('describe_notes.txt', '.txt')],
    )
    def it_ignores_non_describe_python_and_non_python_paths(name, suffix):
        from types import SimpleNamespace

        parent = SimpleNamespace(
            config=SimpleNamespace(
                pluginmanager=SimpleNamespace(get_plugins=lambda: [])
            )
        )
        file_path = SimpleNamespace(name=name, suffix=suffix)
        _coverage_collector()._DiscoveryCheck().pytest_collect_file(file_path, parent)

    @pytest.mark.parametrize('plugins', [[], [object()], [type('Unrelated', (), {'__name__': 'other_plugin'})()]])
    def it_rejects_describe_python_without_pytest_describe(plugins):
        from types import SimpleNamespace

        parent = SimpleNamespace(
            config=SimpleNamespace(
                pluginmanager=SimpleNamespace(get_plugins=lambda: plugins)
            )
        )
        with pytest.raises(pytest.UsageError, match='require pytest-describe'):
            _coverage_collector()._DiscoveryCheck().pytest_collect_file(
                Path('describe_example.py'), parent
            )

    def it_recognizes_pytest_describe_plugin():
        from types import SimpleNamespace

        plugin = SimpleNamespace(__name__='pytest_describe.plugin')
        parent = SimpleNamespace(
            config=SimpleNamespace(
                pluginmanager=SimpleNamespace(get_plugins=lambda: [plugin])
            )
        )
        _coverage_collector()._DiscoveryCheck().pytest_collect_file(
            Path('describe_example.py'), parent
        )

    @pytest.mark.parametrize(
        'status', [pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED]
    )
    def it_writes_successful_empty_coverage_report(status, tmp_path, monkeypatch):
        output = tmp_path / 'coverage.json'
        monkeypatch.setattr(sys, 'argv', ['coverage_collector', '--output', str(output)])
        monkeypatch.setattr(pytest, 'main', lambda *args, **kwargs: status)

        assert _coverage_collector().main() == 0
        assert json.loads(output.read_text()) == {
            'line_to_tests': {},
            'test_times': {},
        }

    def it_returns_exact_error_without_success_report(tmp_path, monkeypatch):
        output = tmp_path / 'coverage.json'
        status = pytest.ExitCode.INTERRUPTED
        monkeypatch.setattr(sys, 'argv', ['coverage_collector', '--output', str(output)])
        monkeypatch.setattr(pytest, 'main', lambda *args, **kwargs: status)

        assert _coverage_collector().main() == int(status)
        assert not output.exists()

    @pytest.mark.parametrize('module', ['config', 'coverage_tracker', 'engine', 'git_diff', 'operators'])
    def it_collects_original_describe_suites(module, tmp_path, monkeypatch):
        # macOS /var alias differs from getcwd; give nested suites a canonical temp root.
        monkeypatch.setenv("TMPDIR", str(tmp_path.resolve()))
        assert not (ROOT / f'tests/leela/test_{module}.py').exists()
        cov = collect_coverage_subprocess(
            [str(ROOT / f'src/pytest_leela/{module}.py')],
            [f'tests/leela/describe_{module}.py'], cwd=str(ROOT),
        )
        assert cov.test_times
        assert cov.line_to_tests
        assert all(f'describe_{module}.py::' in test for test in cov.test_times)

    def it_reports_missing_plugin_in_the_invoking_environment(tmp_path, monkeypatch):
        (tmp_path / 'describe_missing.py').write_text('def describe_x():\n    def it_works():\n        assert True\n')
        # Supported pytest configuration: plugin autoload deliberately disabled.
        monkeypatch.setenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD', '1')
        with pytest.raises(RuntimeError, match='require pytest-describe'):
            collect_coverage_subprocess(['unused.py'], ['describe_missing.py'], str(tmp_path))

    def it_supports_plain_tests_without_the_optional_plugin(tmp_path, monkeypatch):
        monkeypatch.setenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD', '1')
        (tmp_path / 'calc.py').write_text('def add(a, b):\n    return a + b\n')
        (tmp_path / 'test_calc.py').write_text('from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n')
        cov = collect_coverage_subprocess(['calc.py'], ['test_calc.py'], str(tmp_path))
        assert cov.tests_for(str((tmp_path / 'calc.py').resolve()), 2)

    def it_accepts_a_genuinely_empty_test_suite(tmp_path):
        cov = collect_coverage_subprocess(['unused.py'], [], str(tmp_path))
        assert cov.line_to_tests == {}
        assert cov.test_times == {}

    def it_reports_collection_errors(tmp_path):
        (tmp_path / 'test_bad.py').write_text('import no_such_project_module\n')
        with pytest.raises(RuntimeError, match='no_such_project_module'):
            collect_coverage_subprocess(['unused.py'], ['test_bad.py'], str(tmp_path))

    def it_propagates_unavailable_interpreter(tmp_path, monkeypatch):
        monkeypatch.setattr(sys, 'executable', str(tmp_path / 'missing-python'))
        with pytest.raises(FileNotFoundError):
            collect_coverage_subprocess(['unused.py'], [], str(tmp_path))

    def it_bounds_collection_and_removes_output_on_timeout(tmp_path, monkeypatch):
        output = []
        def timeout(cmd, **kwargs):
            assert kwargs['timeout'] == 120
            output.append(Path(cmd[cmd.index('--output') + 1]))
            raise subprocess.TimeoutExpired(cmd, kwargs['timeout'])
        monkeypatch.setattr(subprocess, 'run', timeout)
        with pytest.raises(subprocess.TimeoutExpired):
            collect_coverage_subprocess(['unused.py'], [], str(tmp_path))
        assert not output[0].exists()

    def it_isolates_hooks_modules_and_handles_colons_and_symlinks(tmp_path):
        from unittest.mock import MagicMock
        from pytest_leela.import_hook import MutatingFinder
        project = tmp_path / 'project:with space'
        project.mkdir()
        (project / 'calc.py').write_text('def add(a, b):\n    return a + b\n')
        (project / 'describe_calc.py').write_text('from calc import add\ndef describe_add():\n    def it_adds():\n        assert add(2, 3) == 5\n')
        link = tmp_path / 'link'
        link.symlink_to(project, target_is_directory=True)
        hook = MagicMock(spec=MutatingFinder)
        hook.find_spec.side_effect = AssertionError('parent hook leaked')
        before = list(sys.meta_path)
        old_module = sys.modules.get('calc')
        sys.meta_path.insert(0, hook)
        try:
            cov = collect_coverage_subprocess(['calc.py'], ['describe_calc.py'], str(link))
            assert cov.tests_for(str((project / 'calc.py').resolve()), 2)
            assert sys.meta_path == [hook, *before]
            assert sys.modules.get('calc') is old_module
        finally:
            sys.meta_path[:] = before


def test_successful_unrelated_tests_are_not_collection_failures(tmp_path):
    (tmp_path / 'test_stdlib.py').write_text('def test_stdlib():\n    assert len([1]) == 1\n')
    cov = collect_coverage_subprocess(['unused.py'], ['test_stdlib.py'], str(tmp_path))
    assert cov.line_to_tests == {}
    assert list(cov.test_times) == ['test_stdlib.py::test_stdlib']


def test_default_source_discovery_ignores_only_project_relative_skip_dirs(tmp_path):
    from pytest_leela.daemon import LeelaDaemon
    project = tmp_path / 'tests' / 'project'
    (project / 'src' / 'build').mkdir(parents=True)
    source = project / 'src' / 'calc.py'
    source.write_text('def f():\n    return 1\n')
    (project / 'src' / 'build' / 'generated.py').write_text('x = 1\n')
    daemon = LeelaDaemon(project)
    assert list(daemon._iter_source_files()) == [source]
