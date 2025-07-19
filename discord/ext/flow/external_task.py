from __future__ import annotations

from asyncio import get_running_loop
from enum import Enum
from typing import TYPE_CHECKING

from discord.utils import maybe_coroutine

if TYPE_CHECKING:
    from asyncio import Task

    from discord.utils import MaybeAwaitableFunc

    from .result import Result


__all__ = ('ExternalResultTask', 'ExternalTaskLifeTime')


class ExternalTaskLifeTime(Enum):
    """External task life time.

    PERSISTENT: Persistent task. This task will be kept until the flow is finished.
    MODEL: Model task. This task will be removed when the model is changed.
    """

    PERSISTENT = 0
    MODEL = 1

    def is_model(self) -> bool:
        """Check if this task is a model task.

        Returns:
            bool: True if this task is a model task, False otherwise.
        """
        return self == ExternalTaskLifeTime.MODEL


class ExternalResultTask:
    """asyncio.Task wrapper."""

    task: Task[Result]
    _lifetime: ExternalTaskLifeTime

    def __init__(
        self,
        coro: MaybeAwaitableFunc[[], Result],
        name: str | None = None,
        lifetime: ExternalTaskLifeTime = ExternalTaskLifeTime.PERSISTENT,
    ) -> None:
        if name is None:
            name = f'ExternalResultTask-{coro.__qualname__}'
        self.task = get_running_loop().create_task(maybe_coroutine(coro), name=name)
        self._lifetime = lifetime

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
