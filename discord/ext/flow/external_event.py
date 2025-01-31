from __future__ import annotations

from asyncio import Task, get_running_loop
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncio import Future

    from discord.abc import Messageable

    from .result import Result
    from .view import _View


class ExternalEvent:
    """asyncio.Future wrapper."""

    fut: Future[Result]

    def __init__(self) -> None:
        self.fut = get_running_loop().create_future()

    def set_result(self, result: Result) -> None:
        """Set result.

        Args:
            result (Result): result for update lib.
        """
        if self.fut.done():
            return
        self.fut.set_result(result)

    @property
    def is_done(self) -> bool:
        """If already call set_result, return True, otherwise return False.

        Returns:
            bool: is already call set_result.
        """
        return self.fut.done()

    def _wait_result(self, view: _View, msg: Messageable) -> tuple[Task[None], Task[None]]:
        id_ = id(self)
        loop = get_running_loop()
        task_wait = loop.create_task(self._wait_set(view, msg), name=f'wait-external-event-{id_}')
        task_cancel = loop.create_task(self._wait_cancel(view, task_wait), name=f'cancel-external-event-{id_}')
        return task_wait, task_cancel

    async def _wait_set(self, view: _View, msg: Messageable) -> None:
        ret = await self.fut
        await view.set_result(ret, msg)

    async def _wait_cancel(self, view: _View, task: Task[None]) -> None:
        await view.wait()
        task.cancel()
