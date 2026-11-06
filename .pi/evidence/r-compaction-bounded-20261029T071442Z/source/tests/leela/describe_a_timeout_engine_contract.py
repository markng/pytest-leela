"""Early CALL contract before self-mutation can enter recursive test discovery."""

from pathlib import Path

import pytest

from pytest_leela.engine import Engine


def test_engine_without_diff_does_not_request_changed_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "empty_target.py"
    # Parsed input only: no supported mutation points, no user test execution.
    target.write_text("class Marker:\n    pass\n")

    def unexpected_diff(reference: str) -> dict[str, set[int]]:
        raise AssertionError(f"diff discovery requested without --diff: {reference!r}")

    monkeypatch.setattr("pytest_leela.engine.changed_lines", unexpected_diff)
    result = Engine(use_coverage=False).run([str(target)])
    assert result.diff_base is None
    assert result.total_mutants == 0
    assert result.results == []


def test_engine_diff_contract_negative_control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def requests_diff(self: Engine, *args: object, **kwargs: object) -> None:
        from pytest_leela.engine import changed_lines
        changed_lines(None)

    monkeypatch.setattr(Engine, "run", requests_diff)
    with pytest.raises(AssertionError, match="diff discovery requested without --diff"):
        test_engine_without_diff_does_not_request_changed_lines(tmp_path, monkeypatch)
