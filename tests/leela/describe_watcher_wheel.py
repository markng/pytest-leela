"""Assert the actual built distribution, not editable-import behavior."""
import os
from pathlib import Path
import subprocess
import sys
import zipfile


def describe_watcher_wheel():
    def it_excludes_sentinel_and_imports_in_an_isolated_interpreter(tmp_path):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
             '--wheel-dir', str(tmp_path), str(root)],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        wheel, = tmp_path.glob('pytest_leela-*.whl')
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            assert 'pytest_leela/daemon.py' in names
            assert 'pytest_leela/coverage_collector.py' in names
            assert not any('change_me' in name for name in names)
        code = '''
import sys
sys.path.insert(0, sys.argv[1])
import pytest_leela
import pytest_leela.daemon
import pytest_leela.coverage_collector
assert sys.argv[1] in pytest_leela.__file__, pytest_leela.__file__
assert sys.argv[1] in pytest_leela.daemon.__file__
print(pytest_leela.__file__)
'''
        result = subprocess.run(
            [sys.executable, '-I', '-c', code, str(wheel)],
            cwd=tmp_path, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert str(wheel) in result.stdout
