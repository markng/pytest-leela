"""Experimental shared mutation plans and completion proofs for serial shards."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
from dataclasses import asdict, dataclass, field
from importlib.metadata import distributions
from pathlib import Path, PurePosixPath
from uuid import uuid4

import pytest

from pytest_leela import __version__
from pytest_leela.config import load_config
from pytest_leela.models import Mutant


class ShardError(Exception):
    """The shared mutation plan or shard evidence cannot be verified."""


class PlanWritten(Exception):
    """Planning completed without running any mutations."""


def _digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def _read_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ShardError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ShardError(f"invalid object in {path.name}")
    return dict(value)


def _write_object(path: Path, value: dict[str, object]) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, allow_nan=False)
            stream.write("\n")
    except (OSError, ValueError) as exc:
        raise ShardError(f"cannot write {path.name}: {exc}") from exc


def _finite_time(value: object) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


@dataclass(frozen=True)
class Plan:
    seal: str
    count: int
    occurrences: list[list[object]]
    members: list[list[int]]
    times: dict[str, float]
    fingerprint: str
    fail_on_error: bool


def _load_plan(path: Path) -> Plan:
    data = _read_object(path)
    seal = data.pop("seal", None)
    if set(data) != {"schema", "nonce", "fingerprint", "count", "runnable", "occurrences", "members", "times", "fail_on_error"}:
        raise ShardError("invalid shared mutation plan keys")
    try:
        valid_seal = seal == _digest(data)
    except (ValueError, OverflowError) as exc:
        raise ShardError("invalid shared mutation plan values") from exc
    if not isinstance(seal, str) or not valid_seal:
        raise ShardError("shared mutation plan checksum mismatch")
    count, runnable, occurrences, members = (data[k] for k in ("count", "runnable", "occurrences", "members"))
    fingerprint, times, fail_on_error = (data[k] for k in ("fingerprint", "times", "fail_on_error"))
    if (type(data["schema"]) is not int or data["schema"] != 2 or not isinstance(data["nonce"], str)
        or type(count) is not int or type(runnable) is not int or not isinstance(occurrences, list)
        or not isinstance(members, list) or not 0 < count <= runnable == len(occurrences)
        or not isinstance(fingerprint, str) or not isinstance(fail_on_error, bool)
        or not isinstance(times, dict) or not all(_finite_time(t) for t in times.values())):
        raise ShardError("invalid shared mutation plan shape")
    for i, row in enumerate(occurrences):
        if (not isinstance(row, list) or len(row) != 7 or type(row[0]) is not int or row[0] != i
            or not all(isinstance(row[j], str) for j in (1, 2, 5, 6))
            or not all(type(row[j]) is int for j in (3, 4))):
            raise ShardError("invalid shared mutation occurrence")
    if (any(not isinstance(group, list) or any(type(i) is not int for i in group) for group in members)
        or members != [list(range(i, runnable, count)) for i in range(count)]):
        raise ShardError("invalid shared mutation shard membership")
    return Plan(seal, count, occurrences, members, times, fingerprint, fail_on_error)


def receipt_path(path: Path) -> Path:
    return path.with_name(path.name + ".receipt.json")


def _result_evidence(plan: Plan, index: int, path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        if not raw.endswith(b"\n"):
            raise ShardError("truncated shard JSONL")
        expected = plan.members[index]
        lines = raw[:-1].split(b"\n")
        if len(lines) != len(expected):
            raise ShardError("shard JSONL completion count mismatch")
        outcomes = {"killed": 0, "survived": 0, "error": 0}
        for i, line in zip(expected, lines, strict=True):
            row = json.loads(line)
            if (not isinstance(row, dict) or set(row) != {"schema_version", "mutant_id", "file_path", "module_name", "lineno",
                "col_offset", "original_op", "replacement_op", "verdict", "wall_time_seconds"}
                or type(row["schema_version"]) is not int or row["schema_version"] != 1
                or any(type(row[k]) is not int for k in ("mutant_id", "lineno", "col_offset"))
                or not isinstance(row["file_path"], str) or not isinstance(row["verdict"], str)
                or row["verdict"] not in outcomes or not _finite_time(row["wall_time_seconds"])):
                raise ShardError("invalid shard JSONL v1 row")
            file = Path(row["file_path"])
            identity = [row[k] for k in ("mutant_id", "module_name", "lineno", "col_offset", "original_op", "replacement_op")]
            expected_path = PurePosixPath(str(plan.occurrences[i][1]))
            if not file.is_absolute() or file.parts[-len(expected_path.parts):] != expected_path.parts or identity != [plan.occurrences[i][j] for j in (0, 2, 3, 4, 5, 6)]:
                raise ShardError(f"unexpected shard JSONL identity for occurrence {i}")
            outcomes[row["verdict"]] += 1
    except (OSError, ValueError) as exc:
        raise ShardError(f"cannot read shard JSONL {path.name}: {exc}") from exc
    return {"schema": 1, "plan": plan.seal, "index": index, "count": plan.count,
            "completed": len(expected), "jsonl_sha256": hashlib.sha256(raw).hexdigest(),
            "outcomes": outcomes}


def verify_results(plan: Plan, outputs: list[Path]) -> dict[str, int]:
    seen = set()
    totals = {"killed": 0, "survived": 0, "error": 0}
    for path in outputs:
        receipt = _read_object(receipt_path(path))
        index = receipt.get("index")
        if type(index) is not int or not 0 <= index < plan.count or index in seen:
            raise ShardError("unexpected or duplicate shard index")
        expected = _result_evidence(plan, index, path)
        try:
            matches = _digest(receipt) == _digest(expected)
        except (ValueError, OverflowError) as exc:
            raise ShardError("invalid shard completion receipt values") from exc
        if not matches:
            raise ShardError("shard completion receipt does not match plan and JSONL bytes")
        seen.add(index)
        results = expected["outcomes"]
        assert isinstance(results, dict)
        for name in totals:
            totals[name] += results[name]
    if len(seen) != plan.count:
        raise ShardError("missing shard completion receipt")
    return totals


@dataclass
class Shard:
    path: Path
    root: Path
    index: int = 0
    count: int = 1
    write: bool = False
    config_path: Path | None = None
    _plan: Plan | None = field(default=None, init=False, repr=False)

    def complete(self, path: Path) -> None:
        if self._plan is None:
            raise ShardError("cannot complete an unverified shard")
        _write_object(receipt_path(path), _result_evidence(self._plan, self.index, path))

    def resolve(
        self,
        files: list[str],
        modules: dict[str, str],
        mutants: list[Mutant],
        test_ids: list[str] | None,
        selectors: list[list[str] | None],
        times: dict[str, float] | None,
    ) -> dict[str, float]:
        if not mutants or self.count > len(mutants):
            raise ShardError("shards require at least one runnable occurrence per shard")
        if not test_ids or len(set(test_ids)) != len(test_ids) or times is None:
            raise ShardError("shards require exact collected tests and coverage timings")
        if len(modules) != len(files):
            raise ShardError("shards do not support duplicate target module names")
        all_ids = set(test_ids)
        if any(not ids or not all_ids.issuperset(ids) for ids in selectors):
            raise ShardError("shards require explicit per-mutant selectors")

        def file_identity(path: str) -> tuple[str, str]:
            full = (self.root / path).resolve(strict=True)
            return full.relative_to(self.root).as_posix(), hashlib.sha256(full.read_bytes()).hexdigest()

        try:
            # The initial supported scope is a regular Python checkout with an
            # external environment. Include helpers even when not mutation targets.
            files_in_root: set[str] = set()
            for parent, dirs, names in os.walk(self.root, onerror=_walk_error):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}]
                if any((Path(parent) / d).is_symlink() for d in dirs) or "pyvenv.cfg" in names:
                    raise ShardError("shards require an external environment and no symlinked project directories")
                sources = [Path(parent) / name for name in names if name.endswith(".py")]
                if any(p.is_symlink() or not p.is_file() for p in sources):
                    raise ShardError("shards require regular project Python files")
                files_in_root.update(map(str, sources))
            if any(str(self.root / node.split("::", 1)[0]) not in files_in_root for node in test_ids):
                raise ShardError("shards require collected Python test files inside the checkout")
            if self.config_path is not None:
                files_in_root.add(str(self.config_path))
            config = load_config(self.root)
            file_ids = {os.path.abspath(f): file_identity(f) for f in files}
            occurrences = [[m.mutant_id, file_ids[m.point.file_path][0], m.point.module_name,
                            m.point.lineno, m.point.col_offset, m.point.original_op,
                            m.replacement_op] for m in mutants]
            fingerprint = _digest([
                self.count, sys.implementation.name, sys.version, platform.system(), platform.machine(),
                pytest.__version__, __version__, sorted((d.metadata["Name"], d.version) for d in distributions()),
                asdict(config), list(file_ids.values()), [(m, file_identity(f)[0]) for m, f in modules.items()],
                test_ids, sorted(file_identity(p) for p in files_in_root), selectors,
                [(row, m.point.node_type) for row, m in zip(occurrences, mutants, strict=True)],
            ])
            if self.write:
                selected_times = {node: elapsed for node, elapsed in times.items() if node in all_ids}
                if not all(_finite_time(t) for t in selected_times.values()):
                    raise ShardError("invalid shared mutation plan timings")
                data: dict[str, object] = {"schema": 2, "nonce": uuid4().hex, "fingerprint": fingerprint,
                    "count": self.count, "runnable": len(mutants), "occurrences": occurrences,
                    "members": [list(range(i, len(mutants), self.count)) for i in range(self.count)],
                    "times": selected_times, "fail_on_error": config.fail_on_error}
                _write_object(self.path, {**data, "seal": _digest(data)})
                return selected_times
            plan = _load_plan(self.path)
            if (plan.fingerprint != fingerprint or plan.count != self.count or plan.occurrences != occurrences
                or plan.fail_on_error != config.fail_on_error or not all_ids.issuperset(plan.times)):
                raise ShardError("local full mutation plan does not match the shared plan")
            self._plan = plan
            return plan.times
        except (OSError, ValueError, OverflowError) as exc:
            raise ShardError(f"cannot use shared mutation plan: {exc}") from exc


def _walk_error(exc: OSError) -> None:
    raise exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify all JSONL shards and their completion receipts")
    parser.add_argument("plan", type=Path)
    parser.add_argument("jsonl", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        plan = _load_plan(args.plan)
        totals = verify_results(plan, args.jsonl)
    except ShardError as exc:
        print(f"shard verification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(totals, sort_keys=True))
    return int(totals["survived"] > 0 or (totals["error"] > 0 and plan.fail_on_error))


if __name__ == "__main__":
    raise SystemExit(main())
