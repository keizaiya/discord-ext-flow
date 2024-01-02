from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeVar

    T = TypeVar('T')
    U = TypeVar('U')


def unwrap_or(value: T | None, default: U) -> T | U:
    """Return value if value is not None, otherwise return default."""
    if value is None:
        return default
    return value
