from __future__ import annotations

from contextlib import AsyncExitStack
from contextvars import ContextVar, Token
from enum import Enum
from logging import getLogger
from typing import TYPE_CHECKING

from discord.utils import maybe_coroutine

from .external_task import ExternalResultTask
from .result import Result
from .util import exec_result, force_cancel_tasks, send_helper, wait_first_result
from .view import _View

if TYPE_CHECKING:
    from asyncio import Task
    from types import TracebackType
    from typing import Self

    from discord import Client, Interaction
    from discord.abc import Messageable
    from discord.utils import MaybeAwaitableFunc

    from .model import ModelBase
    from .result import Result
    from .util import _Editable

__all__ = ('Controller', 'ExternalTaskLifeTime', 'create_external_result')

logger = getLogger(__name__)


class ExternalTaskLifeTime(Enum):
    """External task life time.

    PERSISTENT: Persistent task. This task will be kept until the flow is finished.
    MODEL: Model task. This task will be removed when the model is changed.
    """

    PERSISTENT = 0
    MODEL = 1


controller_var = ContextVar['Controller | None'](f'{__name__}.controller_var', default=None)


def _get_controller() -> Controller:
    controller = controller_var.get()
    if controller is None:
        raise RuntimeError('This function should be called inside flow.')
    return controller


class _AutoRestControllerContext:
    def __init__(self, token: Token[Controller | None]) -> None:
        self.token = token

    def __enter__(self) -> Token[Controller | None]:
        return self.token

    def __exit__(self, *args: object) -> None:
        controller_var.reset(self.token)


def create_external_result(
    coro: MaybeAwaitableFunc[[], Result],
    name: str | None = None,
    life_time: ExternalTaskLifeTime = ExternalTaskLifeTime.PERSISTENT,
) -> ExternalResultTask:
    """Create external result.

    Args:
        coro (MaybeAwaitableFunc[[], Result]): Coro function to get result.
        name (str | None): Name of the task.
        life_time (ExternalTaskLifeTime): Life time of the task. Defaults to ExternalTaskLifeTime.PERSISTENT.

    Raises:
        RuntimeError:
            This function should be called inside flow.

    Returns:
        ExternalResult: task of External result.
    """
    controller = _get_controller()
    return controller.create_external_result(coro, name=name, life_time=life_time)


class Controller:
    """Flow controller.

    This class is responsible for sending messages and waiting for interactions.

    Args:
        initial_model (ModelBase): Initial model. This model will be used first.
    """

    model: ModelBase
    persistent_tasks: list[ExternalResultTask]
    model_tasks: list[ExternalResultTask]

    def __init__(self, initial_model: ModelBase) -> None:
        self.model = initial_model
        self.persistent_tasks = []
        self.model_tasks = []

    def copy(self) -> Self:
        """Returns a copy of this controller.

        Returns:
            Controller: Copied controller.
        """
        return self.__class__(self.model)

    async def invoke(self, messageable: Messageable | Interaction[Client], message: _Editable | None = None) -> None:
        """Invoke flow.

        Args:
            messageable (Messageable | Interaction): Messageable or interaction to send first message.
            message (discord.Message | None): The first target for editing if edit_original is True. Defaults to None.
        """

        async def cleanup(_c: type[BaseException] | None, _e: BaseException | None, _t: TracebackType | None) -> None:
            await force_cancel_tasks(t.task for t in self.model_tasks)
            await force_cancel_tasks(t.task for t in self.persistent_tasks)

        async with AsyncExitStack() as st:
            st.enter_context(self._set_to_context())
            st.push_async_exit(cleanup)
            model: ModelBase = self.model
            while True:
                if (ret := await self._send(model, messageable, message)) is None:
                    break
                _model, messageable, message = ret
                if model != _model:
                    model = _model
                    await force_cancel_tasks(t.task for t in self.model_tasks)

    def create_external_result(
        self,
        coro: MaybeAwaitableFunc[[], Result],
        name: str | None = None,
        life_time: ExternalTaskLifeTime = ExternalTaskLifeTime.PERSISTENT,
    ) -> ExternalResultTask:
        """Create external result.

        Args:
            coro (MaybeAwaitableFunc[[], Result]): Coro function to get result.
            name (str | None): Name of the task.
            life_time (ExternalTaskLifeTime): Life time of the task. Defaults to ExternalTaskLifeTime.PERSISTENT.

        Returns:
            ExternalResultTask: task of External result.
        """
        task = ExternalResultTask(coro, name=name)
        if life_time == ExternalTaskLifeTime.PERSISTENT:
            self.persistent_tasks.append(task)
        else:
            self.model_tasks.append(task)
        return task

    def _set_to_context(self) -> _AutoRestControllerContext:
        return _AutoRestControllerContext(controller_var.set(self))

    async def _send(
        self,
        model: ModelBase,
        messageable: Messageable | Interaction[Client],
        edit_target: _Editable | None,
    ) -> tuple[ModelBase, Interaction[Client] | Messageable, _Editable] | None:
        await maybe_coroutine(model.before_invoke)
        msg = await maybe_coroutine(model.message)

        if msg.items is None:
            await send_helper(messageable, msg, None, edit_target)
            await maybe_coroutine(model.after_invoke)
            return None

        view = _View(config=await maybe_coroutine(model.view_config), items=msg.items, controller=self)
        message = await send_helper(messageable, msg, view, edit_target)

        self._get_view_wait_task(view)
        result = await self._wait_result(view)
        assert view.is_finished()
        view.fut.cancel()

        if msg.disable_items:
            for child in view.children:
                child.disabled = True  # type: ignore[reportGeneralTypeIssues, attr-defined]
            await message.edit(view=view)

        await maybe_coroutine(model.after_invoke)

        return None if result is None else (*result, message)

    def _get_view_wait_task(self, view: _View) -> ExternalResultTask:
        # Creates a task that waits for the view to finish or receive an interaction.
        # Re-registers itself upon completion if not cancelled to continuously wait for the next interaction.
        task = self.create_external_result(view._wait, name='inner-view-wait', life_time=ExternalTaskLifeTime.MODEL)
        # Re-create the wait task if the view is still active (not cancelled) after the previous wait completed.
        task.task.add_done_callback(lambda t: self._get_view_wait_task(view) if not t.cancelled() else None)
        return task

    async def _wait_result(self, view: _View) -> tuple[ModelBase, Interaction[Client] | Messageable] | None:
        pending_tasks: set[Task[Result]] = set()
        while not view.is_finished():
            pending_tasks |= {
                *(t.task for t in self.persistent_tasks if not t.done()),
                *(t.task for t in self.model_tasks if not t.done()),
            }
            done_tasks, raised_tasks, pending_tasks = await wait_first_result(pending_tasks)

            # check exceptions
            exceptions: list[Exception] = []
            base_exceptions: list[BaseException] = []
            for t in raised_tasks:
                if t.cancelled() or not t.done() or (e := t.exception()) is None:
                    continue
                if isinstance(e, Exception):
                    exceptions.append(e)
                else:
                    base_exceptions.append(e)

            if base_exceptions:
                raise BaseExceptionGroup('Errors occurred in external tasks', base_exceptions)
            if exceptions:
                await self.on_error(ExceptionGroup('Errors occurred in external tasks', exceptions))

            # check done tasks
            for t in done_tasks:
                ret = await exec_result(view, t.result())
                if ret is not None or view.is_finished():
                    return ret

        return None

    async def on_error(self, exception_group: BaseExceptionGroup) -> None:
        """A Callback that is called when external tasks raised exceptions.

        The default implementation will log the exception.

        Args:
            exception_group (BaseExceptionGroup): The exception group raised by external tasks.
        """
        logger.error('Ignoring Exceptions:', exc_info=exception_group)
