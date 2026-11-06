"""Plain pytest tests for ``pytest_leela.operators``.

Mirrors the ``describe_operators.py`` block-style tests so the
daemon's coverage collector (running under system Python
without ``pytest-describe``) can discover them. Targets every
mutated path in ``operators.py``.
"""

from __future__ import annotations

import os
import sys
import typing

import pytest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "src"),
)

from pytest_leela.models import MutationPoint  # noqa: E402
from pytest_leela.operators import (  # noqa: E402
    build_allowed_keys,
    count_pruned,
    mutations_for,
)


def _make_point(
    *,
    node_type: str = "BinOp",
    original_op: str = "Add",
    inferred_type: str | None = "int",
) -> MutationPoint:
    """Build a MutationPoint with sensible defaults for tests."""
    return MutationPoint(
        file_path="dummy.py",
        module_name="dummy",
        lineno=1,
        col_offset=0,
        node_type=node_type,
        original_op=original_op,
        inferred_type=inferred_type,
    )


# ---------------------------------------------------------------------------
# Type annotation mutations are killable via get_type_hints
# ---------------------------------------------------------------------------


def test_build_allowed_keys_annotation_is_union() -> None:
    """``enabled_categories`` annotation is a union.

    Kills ``BitOr -> BitAnd`` on line 265: ``tuple[str, ...] | list[str, ...] | None``
    mutated to ``&`` raises ``TypeError`` when ``get_type_hints`` evaluates.
    """
    hints = typing.get_type_hints(build_allowed_keys)
    assert "enabled_categories" in hints


def test_mutations_for_annotation_is_union() -> None:
    """``allowed_keys`` annotation is a union.

    Kills ``BitOr -> BitAnd`` on line 293: ``frozenset[...] | None`` mutated
    to ``&`` raises ``TypeError`` when ``get_type_hints`` evaluates.
    """
    hints = typing.get_type_hints(mutations_for)
    assert "allowed_keys" in hints


def test_count_pruned_annotation_is_union() -> None:
    """``allowed_keys`` annotation is a union.

    Kills ``BitOr -> BitAnd`` on line 320.
    """
    hints = typing.get_type_hints(count_pruned)
    assert "allowed_keys" in hints


# ---------------------------------------------------------------------------
# build_allowed_keys
# ---------------------------------------------------------------------------


def test_build_allowed_keys_returns_none_when_disabled() -> None:
    """``build_allowed_keys(None)`` returns ``None`` (all ops enabled)."""
    assert build_allowed_keys(None) is None


def test_build_allowed_keys_returns_frozenset_when_enabled() -> None:
    """``build_allowed_keys(cat)`` returns ``frozenset``."""
    result = build_allowed_keys(("arithmetic",))
    assert isinstance(result, frozenset)


def test_build_allowed_keys_raises_on_unknown_category() -> None:
    """Unknown category -> ``ValueError``."""
    with pytest.raises(ValueError, match="Unknown operator categories"):
        build_allowed_keys(("nonexistent_category",))


def test_build_allowed_keys_resolves_arithmetic() -> None:
    """``"arithmetic"`` category contains arithmetic mutations."""
    result = build_allowed_keys(("arithmetic",))
    assert result is not None
    assert ("BinOp", "Add") in result


def test_build_allowed_keys_resolves_boolean() -> None:
    """``"boolean"`` category contains boolean mutations."""
    result = build_allowed_keys(("boolean",))
    assert result is not None
    assert ("BoolOp", "And") in result


# ---------------------------------------------------------------------------
# mutations_for
# ---------------------------------------------------------------------------


def test_mutations_for_returns_empty_for_filtered_key() -> None:
    """If ``allowed_keys`` filters out the key, returns ``[]``."""
    point = _make_point()
    result = mutations_for(point, allowed_keys=frozenset())
    assert result == []


def test_mutations_for_returns_all_when_allowed_keys_is_none() -> None:
    """``allowed_keys=None`` returns all mutations for the key."""
    point = _make_point()
    result = mutations_for(point, use_types=True, allowed_keys=None)
    assert isinstance(result, list)
    assert len(result) > 0


def test_mutations_for_returns_typed_when_type_matches() -> None:
    """Typed mutation set is used when (node_type, original_op, inferred_type) matches.

    Kills ``In -> NotIn`` on line 310 (``if key in TYPED_MUTATIONS:``):
    with the mutation, the typed branch is skipped for keys that ARE
    in TYPED_MUTATIONS, and the untyped result is returned instead.
    For ``BinOp/Add/int``, typed is ``["Sub", "Mult", "FloorDiv"]``
    (3 entries) but untyped is ``["Sub", "Mult"]`` (2 entries).
    """
    point = _make_point(inferred_type="int")
    result = mutations_for(point, use_types=True)
    # Typed set has 3 entries for int arithmetic
    assert result == ["Sub", "Mult", "FloorDiv"]


def test_mutations_for_distinguishes_typed_from_untyped() -> None:
    """Typed result must differ from untyped when typed rules exist.

    Counter-test for ``In -> NotIn``: asserts that ``use_types=True``
    with an int-typed point produces a *different* (and richer) list
    than ``use_types=False``. With the mutation, both branches return
    the untyped result, so the lists would be equal.
    """
    point = _make_point(inferred_type="int")
    typed = mutations_for(point, use_types=True)
    untyped = mutations_for(point, use_types=False)
    assert typed != untyped


def test_mutations_for_falls_back_to_untyped_when_no_typed_match() -> None:
    """No typed match -> falls back to UNTYPED_MUTATIONS."""
    point = _make_point(
        node_type="Compare",
        original_op="Eq",
        inferred_type=None,
    )
    result = mutations_for(point, use_types=False)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# count_pruned
# ---------------------------------------------------------------------------


def test_count_pruned_returns_zero_when_use_types_false() -> None:
    """``count_pruned(..., use_types=False)`` returns 0."""
    result = count_pruned([], use_types=False)
    assert result == 0


def test_count_pruned_returns_zero_for_empty_list() -> None:
    """``count_pruned([])`` returns 0."""
    result = count_pruned([])
    assert result == 0


def test_count_pruned_handles_real_points() -> None:
    """``count_pruned`` doesn't raise on real MutationPoints."""
    point = _make_point()
    result = count_pruned([point])
    assert isinstance(result, int)


def test_count_pruned_does_not_remove_untyped() -> None:
    """``count_pruned`` subtracts typed from untyped; doesn't remove.

    Kills ``Not -> _remove`` on line 323 (``if not untyped:``):
    with the mutation, ``_remove`` is called, raising ``NameError``.
    """
    point = _make_point(inferred_type=None)
    # Should not raise NameError
    result = count_pruned([point])
    assert isinstance(result, int)
