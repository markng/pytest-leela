"""Opt-in JSON Lines stream of completed mutant timings."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import TextIO

from pytest_leela.models import MutantTiming


class MutantEventsError(Exception):
    """The requested mutant events file could not be opened, written or closed."""


@contextmanager
def open_mutant_events(path: str | None) -> Generator[TextIO | None]:
    if path is None:
        yield None
        return

    try:
        stream = open(path, "x", encoding="utf-8", newline="\n")
    except (OSError, ValueError) as exc:
        raise MutantEventsError(
            f"could not open mutant events file {path!r}: {exc}"
        ) from exc

    try:
        yield stream
    finally:
        try:
            stream.close()
        except OSError as exc:
            raise MutantEventsError(
                f"could not close mutant events file {path!r}: {exc}"
            ) from exc


def write_mutant_event(stream: TextIO, timing: MutantTiming) -> None:
    mutant = timing.mutant
    point = mutant.point
    record = {
        "schema_version": 1,
        "mutant_id": mutant.mutant_id,
        "file_path": point.file_path,
        "module_name": point.module_name,
        "lineno": point.lineno,
        "col_offset": point.col_offset,
        "original_op": point.original_op,
        "replacement_op": mutant.replacement_op,
        "verdict": timing.status,
        "wall_time_seconds": timing.wall_time_seconds,
    }
    try:
        stream.write(json.dumps(record) + "\n")
        stream.flush()
    except OSError as exc:
        raise MutantEventsError(
            f"could not write mutant events file {stream.name!r}: {exc}"
        ) from exc
