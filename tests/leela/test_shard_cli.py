import json
import subprocess
import sys
from collections import Counter


def test_shared_plan_keeps_exact_occurrences_and_verdicts(pytester):
    pytester.makepyfile(
        calc='def value(x):\n    return x + 2 + 0\n\ndef other(x):\n    return x * 2',
        test_calc='from calc import value, other\n\ndef test_value():\n    assert value(2) == 4\n\ndef test_other():\n    assert other(0) == 0',
        test_unrelated='def test_unrelated():\n    assert True',
    )
    pytester.makeini('[pytest]')
    config = pytester.path / 'pyproject.toml'
    config.write_text('[tool.pytest-leela]\nfail_on_error=true\n')
    helper = pytester.path / 'helper.py'
    helper.write_text('OFFSET = 0\n')
    test = pytester.path / 'test_calc.py'
    test.write_text('from helper import OFFSET\n' + test.read_text().replace('== 4', '== 4 + OFFSET'))
    args = ('--target', 'calc.py', '-q', '--basetemp', str(pytester.path.with_name(pytester.path.name + '-runtime')))

    def run(*flags):
        return pytester.run(sys.executable, '-m', 'pytest', *args, *flags, timeout=12)

    def rows(name):
        records = [json.loads(line) for line in (pytester.path / name).read_text().splitlines()]
        for row in records:
            row.pop('wall_time_seconds')
        return records

    assert run('--leela-write-plan=plan.json', '--leela-shard-count=2').ret == 0
    assert run('--leela-jsonl=full.jsonl').ret == 1
    full = rows('full.jsonl')
    shards = []
    for i in range(2):
        out = f'shard{i}.jsonl'
        result = run('--leela-plan=plan.json', f'--leela-shard={i}/2', f'--leela-jsonl={out}')
        subset = rows(out)
        assert subset and result.ret == int(any(m['verdict'] != 'killed' for m in subset))
        assert all(m['mutant_id'] % 2 == i for m in subset)
        shards.extend(subset)
    assert sorted(shards, key=lambda m: m['mutant_id']) == full
    assert {m['verdict'] for m in full} == {'killed', 'survived'}
    assert max(Counter(tuple((k, v) for k, v in m.items() if k != 'mutant_id') for m in full).values()) > 1

    def verify(*outputs):
        return subprocess.run([sys.executable, '-m', 'pytest_leela.shard', 'plan.json', *outputs],
                              cwd=pytester.path, capture_output=True, text=True, timeout=5)

    output0, output1 = ('shard0.jsonl', 'shard1.jsonl')
    good = verify(output0, output1)
    assert good.returncode == 1 and json.loads(good.stdout) == dict(Counter(m['verdict'] for m in full) | {'error': 0})
    assert verify(output0).returncode == 2
    assert verify(output0, output0).returncode == 2
    sidecar = pytester.path / (output1 + '.receipt.json')
    old_receipt = sidecar.read_bytes()
    sidecar.unlink()
    assert verify(output0, output1).returncode == 2
    wrong = json.loads(old_receipt)
    wrong['plan'] = 'different plan'
    sidecar.write_text(json.dumps(wrong))
    assert verify(output0, output1).returncode == 2
    sidecar.write_bytes(old_receipt)
    result_file = pytester.path / output1
    intact = result_file.read_bytes()
    result_file.write_bytes(intact[:-1])
    assert verify(output0, output1).returncode == 2
    changed = [json.loads(line) for line in intact.splitlines()]
    changed[0]['wall_time_seconds'] += 1
    result_file.write_text(''.join(json.dumps(row) + '\n' for row in changed))
    assert verify(output0, output1).returncode == 2
    result_file.write_bytes(intact)

    for drifted, replacement in ((config, '[tool.pytest-leela]\nfail_on_error=false\n'), (helper, 'OFFSET = 0\n# changed helper\n')):
        original = drifted.read_text()
        drifted.write_text(replacement)
        out = f'{drifted.stem}-drift.jsonl'
        drift = run('--leela-plan=plan.json', '--leela-shard=0/2', f'--leela-jsonl={out}')
        assert drift.ret == 1
        drift.stdout.fnmatch_lines(['*local full mutation plan does not match*'])
        assert not (pytester.path / (out + '.receipt.json')).exists()
        assert not rows(out)
        drifted.write_text(original)

    mismatch = run('--leela-plan=plan.json', '--leela-shard=0/3', '--leela-jsonl=mismatch.jsonl')
    assert mismatch.ret == 1
    mismatch.stdout.fnmatch_lines(['*local full mutation plan does not match*'])
    assert not rows('mismatch.jsonl')

    (pytester.path / 'test_unrelated.py').write_text('def test_unrelated():\n    assert False\n')
    for i in range(2):
        out = f'failed{i}.jsonl'
        result = run('--leela-plan=plan.json', f'--leela-shard={i}/2', f'--leela-jsonl={out}')
        assert result.ret == 1
        result.stdout.fnmatch_lines(['*FAILED*test_unrelated.py::test_unrelated*'])
        assert not (pytester.path / out).exists()
