"""Internal helpers for optional values."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def unwrap_or[T, U](value: T | None, default: U) -> T | U:
    """Return value if value is not None, otherwise return default."""
    if value is None:
        return default
    return value


def map_or[T, U, V](value: T | None, default: U, func: Callable[[T], V]) -> V | U:
    """Return func(value) if value is not None, otherwise return default."""
    if value is None:
        return default
    return func(value)
