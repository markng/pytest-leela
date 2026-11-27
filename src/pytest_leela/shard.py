"""Experimental shared plan for serial workers on separate checkouts."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from pytest_leela import __version__
from pytest_leela.models import Mutant


class ShardError(Exception):
    """The full local run cannot be matched to the shared plan."""


class PlanWritten(Exception):
    """Planning completed without running any mutations."""


def digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


@dataclass(frozen=True)
class Shard:
    path: Path
    root: Path
    index: int = 0
    count: int = 1
    write: bool = False
    config_path: Path | None = None

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
            tests = sorted({node.split("::", 1)[0] for node in test_ids})
            parents = {self.root}
            for test in tests:
                parents.update(p for p in (self.root / test).parents if p.is_relative_to(self.root))
            inputs = {str(p / "conftest.py") for p in parents if (p / "conftest.py").is_file()}
            if self.config_path is not None:
                inputs.add(str(self.config_path))
            fingerprint = digest([
                self.count, sys.implementation.name, list(sys.version_info), pytest.__version__, __version__,
                [file_identity(f) for f in files],
                [(m, file_identity(f)[0]) for m, f in modules.items()],
                test_ids, [file_identity(t) for t in tests], sorted(file_identity(p) for p in inputs), selectors,
                [(m.mutant_id, m.point.module_name, m.point.lineno, m.point.col_offset,
                  m.point.node_type, m.point.original_op, m.replacement_op) for m in mutants],
            ])
            if self.write:
                selected_times = {node: elapsed for node, elapsed in times.items() if node in all_ids}
            else:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if (not isinstance(value, dict) or set(value) != {"schema", "fingerprint", "times", "seal"}
                    or type(value["schema"]) is not int or value["schema"] != 1
                    or value["fingerprint"] != fingerprint):
                    raise ShardError("local full mutation plan does not match the shared plan")
                selected_times = value["times"]
                if value["seal"] != digest([fingerprint, selected_times]):
                    raise ShardError("shared mutation plan checksum mismatch")
            if (not isinstance(selected_times, dict) or not all_ids.issuperset(selected_times)
                or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0
                       for t in selected_times.values())):
                raise ShardError("invalid shared mutation plan timings")
            if self.write:
                with self.path.open("x", encoding="utf-8") as stream:
                    json.dump({"schema": 1, "fingerprint": fingerprint, "times": selected_times,
                               "seal": digest([fingerprint, selected_times])}, stream, allow_nan=False)
                    stream.write("\n")
        except (OSError, ValueError, OverflowError) as exc:
            raise ShardError(f"cannot use shared mutation plan: {exc}") from exc
        return selected_times
