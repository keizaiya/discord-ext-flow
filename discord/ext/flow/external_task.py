from __future__ import annotations

from asyncio import Task, get_running_loop
from typing import TYPE_CHECKING

from discord.utils import maybe_coroutine

if TYPE_CHECKING:
    from discord.utils import MaybeAwaitableFunc

    from .result import Result


__all__ = ('ExternalResultTask',)


class ExternalResultTask:
    """asyncio.Task wrapper."""

    task: Task[Result]

    def __init__(self, coro: MaybeAwaitableFunc[[], Result], name: str | None = None) -> None:
        if name is None:
            name = f'ExternalResultTask-{coro.__qualname__}'
        self.task = get_running_loop().create_task(maybe_coroutine(coro), name=name)

    def done(self) -> bool:
        """If already call set_result, return True, otherwise return False.

        Returns:
            bool: is already call set_result.
        """
        return self.task.done()

    def cancel(self) -> None:
        """Cancel task."""
        self.task.cancel()

    def result(self) -> Result:
        """Get task result.

        Returns:
            Result: task result.
        """
        return self.task.result()
