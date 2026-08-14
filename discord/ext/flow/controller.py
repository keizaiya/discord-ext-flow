from __future__ import annotations

import sys
from asyncio import FIRST_COMPLETED, Event, create_task, wait
from contextlib import AbstractContextManager, AsyncExitStack
from contextvars import ContextVar
from itertools import chain
from logging import getLogger
from typing import TYPE_CHECKING, NamedTuple

from discord import ui
from discord.utils import maybe_coroutine

from .external_task import ExternalResultTask, ExternalTaskLifeTime
from .model import ComponentV2Message, Message
from .result import Result, _ResultTypeEnum
from .util import force_cancel_tasks, send_helper, view_can_produce_result
from .view import _ViewType, create_view

if TYPE_CHECKING:
    from asyncio import Task
    from collections.abc import Iterator
    from contextvars import Token
    from types import TracebackType
    from typing import Self

    from discord import Interaction
    from discord.abc import Messageable
    from discord.utils import MaybeAwaitableFunc

    from .model import ModelBase
    from .result import Result
    from .util import _Editable

    type Sendable = Interaction | Messageable

__all__ = ('Controller', 'create_external_result')


logger = getLogger(__name__)
controller_var = ContextVar['Controller | None'](f'{__name__}.controller_var', default=None)


def _get_controller() -> Controller:
    controller = controller_var.get()
    if controller is None:
        raise RuntimeError('This function should be called inside flow.')
    return controller


class _AutoResetControllerContext:
    @classmethod
    def from_token(cls, token: Token[Controller | None]) -> AbstractContextManager[Token[Controller | None], None]:
        if sys.version_info >= (3, 14):
            return token
        return cls(token)

    def __init__(self, token: Token[Controller | None]) -> None:
        self.token = token

    def __enter__(self) -> Token[Controller | None]:
        return self.token

    def __exit__(self, *args: object) -> None:
        controller_var.reset(self.token)


class _CompletedResult(NamedTuple):
    result: Result
    source: ExternalResultTask | None


class _ResultBatch(NamedTuple):
    results: Iterator[_CompletedResult]
    exceptions: tuple[Exception, ...] = ()
    base_exceptions: tuple[BaseException, ...] = ()
    view_finished: bool = False
    exhausted: bool = False


class _ActiveMessage(NamedTuple):
    message: ComponentV2Message | Message
    view: _ViewType | None
    editable: _Editable


class _ResultOutcome(NamedTuple):
    transition: tuple[ModelBase, Sendable] | None = None
    switched_view: bool = False
    terminal: bool = False


class _ResultWaiter:
    def __init__(self, controller: Controller, view: _ViewType) -> None:
        self.controller = controller
        self.view = view
        self.external_tasks: set[ExternalResultTask] = set()
        self.view_result_task: Task[Result] | None = None
        self.view_finished_task = create_task(view.wait(), name='flow-view-finished')
        self.task_added = self._create_task_added_waiter()

    def _create_task_added_waiter(self) -> Task[bool]:
        return create_task(self.controller._external_task_event.wait(), name='flow-external-task-added')

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.controller.external_tasks |= self.external_tasks
        await force_cancel_tasks(
            task
            for task in (self.view_result_task, self.view_finished_task, self.task_added)
            if task is not None and not task.done()
        )

    async def _take_registered_tasks(self) -> None:
        if self.controller.external_tasks:
            self.external_tasks |= self.controller.external_tasks
            self.controller.external_tasks = set()
        if self.controller._external_task_event.is_set() or self.task_added.done():
            self.controller._external_task_event.clear()
            if not self.task_added.done():
                await force_cancel_tasks((self.task_added,))
            self.task_added = self._create_task_added_waiter()

    async def _sync_view_result_task(self) -> None:
        if view_can_produce_result(self.view):
            if self.view_result_task is None:
                self.view_result_task = create_task(self.view._wait(), name='flow-view-result')
            return
        if self.view_result_task is not None and not self.view_result_task.done():
            await force_cancel_tasks((self.view_result_task,))
            self.view_result_task = None

    def has_pending_external_result(self) -> bool:
        tasks = self.external_tasks | self.controller.external_tasks
        return any(not task.task.cancelled() and not task.task.cancelling() for task in tasks)

    def _collect_external_results(self) -> _ResultBatch:
        results: list[_CompletedResult] = []
        exceptions: list[Exception] = []
        base_exceptions: list[BaseException] = []
        for task in tuple(self.external_tasks):
            if not task.done():
                continue
            if task.task.cancelled():
                self.external_tasks.remove(task)
                continue
            try:
                result = task.result()
            except Exception as exception:  # noqa: BLE001
                self.external_tasks.remove(task)
                exceptions.append(exception)
            except BaseException as exception:  # noqa: BLE001
                self.external_tasks.remove(task)
                base_exceptions.append(exception)
            else:
                results.append(_CompletedResult(result, source=task))
        return _ResultBatch(self._consume_results(tuple(results)), tuple(exceptions), tuple(base_exceptions))

    def _consume_results(self, results: tuple[_CompletedResult, ...]) -> Iterator[_CompletedResult]:
        for completed in results:
            if completed.source is not None:
                self.external_tasks.remove(completed.source)
            yield completed

    async def wait(self) -> _ResultBatch:
        await self._take_registered_tasks()
        await self._sync_view_result_task()
        if self.view_result_task is None and not self.has_pending_external_result():
            return _ResultBatch(iter(()), exhausted=True)

        wait_for: set[Task[object]] = set(self._external_task_handles())
        wait_for.update((self.view_finished_task, self.task_added))
        if self.view_result_task is not None:
            wait_for.add(self.view_result_task)
        await wait(wait_for, return_when=FIRST_COMPLETED)

        view_finished = self.view_finished_task.done()
        await self._take_registered_tasks()
        batch = self._collect_external_results()
        if self.view_result_task is not None and self.view_result_task.done():
            results = chain((_CompletedResult(self.view_result_task.result(), source=None),), batch.results)
            self.view_result_task = None
            return _ResultBatch(results, batch.exceptions, batch.base_exceptions, view_finished=view_finished)
        return _ResultBatch(batch.results, batch.exceptions, batch.base_exceptions, view_finished=view_finished)

    def _external_task_handles(self) -> set[Task[Result]]:
        return {task.task for task in self.external_tasks}


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
    external_tasks: set[ExternalResultTask]
    _external_task_event: Event
    _active_message: _ActiveMessage | None

    def __init__(self, initial_model: ModelBase) -> None:
        self.model = initial_model
        self.external_tasks = set()
        self._external_task_event = Event()
        self._active_message = None

    def copy(self) -> Self:
        """Returns a copy of this controller.

        Returns:
            Controller: Copied controller.
        """
        return self.__class__(self.model)

    async def invoke(self, messageable: Sendable, message: _Editable | None = None) -> None:
        """Invoke flow.

        Args:
            messageable (Sendable): Messageable or interaction to send first message.
            message (discord.Message | None): The first target for editing if edit_original is True. Defaults to None.
        """

        async def cleanup(_c: type[BaseException] | None, _e: BaseException | None, _t: TracebackType | None) -> None:
            await force_cancel_tasks(t.task for t in self.external_tasks)
            self.external_tasks.clear()
            await self._finalize_active_message()

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
                    to_cancel = {t for t in self.external_tasks if t._lifetime.is_model()}
                    await force_cancel_tasks(t.task for t in to_cancel)
                    self.external_tasks -= to_cancel

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
        task = ExternalResultTask(coro, name=name, lifetime=life_time)
        self.external_tasks.add(task)
        self._external_task_event.set()
        return task

    def _set_to_context(self) -> AbstractContextManager[Token[Controller | None], None]:
        return _AutoResetControllerContext.from_token(controller_var.set(self))

    async def _send(
        self,
        model: ModelBase,
        messageable: Sendable,
        edit: _Editable | None,
    ) -> tuple[ModelBase, Sendable, _Editable] | None:
        await maybe_coroutine(model.before_invoke)
        msg = await maybe_coroutine(model.message)
        if not isinstance(msg, (ComponentV2Message, Message)):
            raise TypeError('ModelBase.message must return ComponentV2Message or LegacyMessage.')

        if msg.items is None:
            await self._send_message(messageable, msg, None, edit)
            await maybe_coroutine(model.after_invoke)
            return None

        view_config = await maybe_coroutine(model.view_config)
        view = create_view(config=view_config, items=msg.items, controller=self)
        await self._send_message(messageable, msg, view, edit)

        if self.external_tasks or view_can_produce_result(view):
            result = await self._wait_result(view)
        else:
            view.stop()
            result = None
        assert view.is_finished()
        view.fut.cancel()

        await maybe_coroutine(model.after_invoke)

        if result is None:
            return None
        assert self._active_message is not None
        return (*result, self._active_message.editable)

    async def _disable_items(self, active: _ActiveMessage | None) -> None:
        if active is None or not active.message.disable_items or active.view is None:
            return
        for child in active.view.walk_children():
            if isinstance(
                child,
                (ui.Button, ui.ChannelSelect, ui.MentionableSelect, ui.RoleSelect, ui.Select, ui.UserSelect),
            ):
                child.disabled = True
        await active.editable.edit(view=active.view)

    @staticmethod
    def _stop_view(active: _ActiveMessage | None) -> None:
        if active is None or active.view is None:
            return
        active.view.stop()
        active.view.fut.cancel()

    async def _finalize_active_message(self) -> None:
        active, self._active_message = self._active_message, None
        try:
            await self._disable_items(active)
        finally:
            self._stop_view(active)

    async def _send_message(
        self,
        messageable: Sendable,
        message: ComponentV2Message | Message,
        view: _ViewType | None,
        edit: _Editable | None,
    ) -> _Editable:
        previous = self._active_message
        sent = await send_helper(messageable, message, view, edit)
        self._active_message = _ActiveMessage(message=message, view=view, editable=sent)
        if previous is not None and previous.editable.id != sent.id:
            try:
                await self._disable_items(previous)
            except Exception:
                logger.exception('Failed to disable the previous flow message.')
            finally:
                self._stop_view(previous)
        elif previous is not None and previous.view is not view:
            self._stop_view(previous)
        return sent

    async def _exec_result(self, view: _ViewType, result: Result) -> _ResultOutcome:
        if result._interaction is None:
            raise ValueError('result._interaction is None.')
        messageable = result._interaction

        match result._type:
            case _ResultTypeEnum.MESSAGE:
                assert result._message is not None
                message = result._message
                replacement = create_view(config=view.config, items=message.items or (), controller=self)
                edit = None if self._active_message is None else self._active_message.editable
                await self._send_message(messageable, message, replacement, edit)
                return _ResultOutcome(switched_view=True)

            case _ResultTypeEnum.MODEL:
                assert result._model is not None
                view.stop()
                return _ResultOutcome(transition=(result._model, messageable), terminal=True)

            case _ResultTypeEnum.CONTINUE | _ResultTypeEnum.FINISH:
                if not messageable.response.is_done():
                    raise RuntimeError('Callback MUST consume interaction.')
                if result._is_end:
                    view.stop()
                    return _ResultOutcome(terminal=True)
                active_view = None if self._active_message is None else self._active_message.view
                return _ResultOutcome(terminal=active_view is view and view.is_finished())

    async def _handle_batch_errors(self, batch: _ResultBatch) -> None:
        if batch.base_exceptions:
            raise BaseExceptionGroup('Errors occurred in external tasks', batch.base_exceptions)
        if batch.exceptions:
            await self.on_error(ExceptionGroup('Errors occurred in external tasks', batch.exceptions))

    async def _wait_result(
        self,
        initial_view: _ViewType,
    ) -> tuple[ModelBase, Sendable] | None:
        view: _ViewType | None = initial_view
        while view is not None:
            if not self.external_tasks and not view_can_produce_result(view):
                view.stop()
                return None

            switched_view = False
            async with _ResultWaiter(self, view) as waiter:
                while not view.is_finished():
                    batch = await waiter.wait()
                    if batch.exhausted:  # if anyone cannot provide any results
                        view.stop()
                        return None

                    await self._handle_batch_errors(batch)

                    for completed in batch.results:
                        outcome = await self._exec_result(view, completed.result)
                        switched_view |= outcome.switched_view
                        if outcome.terminal:
                            return outcome.transition

                    if switched_view:
                        view = None if self._active_message is None else self._active_message.view
                        break

                    if batch.view_finished:
                        return None
            if view is not None and view.is_finished():
                return None
        return None

    async def on_error(self, exception_group: BaseExceptionGroup) -> None:
        """A Callback that is called when external tasks raised exceptions.

        The default implementation will log the exception.

        Args:
            exception_group (BaseExceptionGroup): The exception group raised by external tasks.
        """
        logger.error('Ignoring Exceptions:', exc_info=exception_group)
