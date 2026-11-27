import json
from collections import Counter


def test_shared_plan_keeps_exact_occurrences_and_verdicts(pytester):
    pytester.makepyfile(
        calc='def value(x):\n    return x + 2 + 0\n\ndef other(x):\n    return x * 2',
        test_calc='from calc import value, other\n\ndef test_value():\n    assert value(2) == 4\n\ndef test_other():\n    assert other(0) == 0',
        test_unrelated='def test_unrelated():\n    assert True',
    )
    args = ('--target', 'calc.py', '-q')

    def rows(name):
        records = [json.loads(line) for line in (pytester.path / name).read_text().splitlines()]
        for row in records:
            row.pop('wall_time_seconds')
        return records

    assert pytester.runpytest_subprocess(*args, '--leela-write-plan=plan.json', '--leela-shard-count=2').ret == 0
    assert pytester.runpytest_subprocess(*args, '--leela-jsonl=full.jsonl').ret == 1
    full = rows('full.jsonl')
    shards = []
    for i in range(2):
        out = f'shard{i}.jsonl'
        result = pytester.runpytest_subprocess(*args, '--leela-plan=plan.json', f'--leela-shard={i}/2', f'--leela-jsonl={out}')
        subset = rows(out)
        assert subset and result.ret == int(any(m['verdict'] != 'killed' for m in subset))
        assert all(m['mutant_id'] % 2 == i for m in subset)
        shards.extend(subset)
    assert sorted(shards, key=lambda m: m['mutant_id']) == full
    assert {m['verdict'] for m in full} == {'killed', 'survived'}
    assert max(Counter(tuple((k, v) for k, v in m.items() if k != 'mutant_id') for m in full).values()) > 1

    mismatch = pytester.runpytest_subprocess(*args, '--leela-plan=plan.json', '--leela-shard=0/3', '--leela-jsonl=mismatch.jsonl')
    assert mismatch.ret == 1
    mismatch.stdout.fnmatch_lines(['*local full mutation plan does not match*'])
    assert not rows('mismatch.jsonl')

    (pytester.path / 'test_unrelated.py').write_text('def test_unrelated():\n    assert False\n')
    for i in range(2):
        out = f'failed{i}.jsonl'
        result = pytester.runpytest_subprocess(*args, '--leela-plan=plan.json', f'--leela-shard={i}/2', f'--leela-jsonl={out}')
        assert result.ret == 1
        result.stdout.fnmatch_lines(['*FAILED*test_unrelated.py::test_unrelated*'])
        assert not (pytester.path / out).exists()
