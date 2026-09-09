from __future__ import annotations

import sys
from asyncio import FIRST_COMPLETED, Event, create_task, gather, wait
from collections.abc import Callable
from contextlib import AbstractContextManager, AsyncExitStack
from contextvars import ContextVar
from dataclasses import replace
from enum import Enum, auto
from logging import getLogger
from typing import TYPE_CHECKING, Any, Concatenate, NamedTuple, ParamSpec, cast

from discord import ui
from discord.utils import MaybeAwaitable, maybe_coroutine

from .external_task import ExternalResultTask, ExternalTaskLifeTime
from .model import ComponentV2Message, Message, ModelBase
from .result import Result, _ResultTypeEnum
from .util import send_helper, view_can_produce_result
from .view import _ViewType, create_view

if TYPE_CHECKING:
    from asyncio import Task
    from collections.abc import Sequence
    from contextvars import Token
    from types import TracebackType
    from typing import Self

    from discord import Interaction
    from discord.abc import Messageable
    from discord.utils import MaybeAwaitableFunc

    from .util import _Editable

    type Sendable = Interaction | Messageable

__all__ = (
    'Controller',
    'ErrorCallback',
    'FlowTimeoutError',
    'create_external_result',
)


logger = getLogger(__name__)
controller_var = ContextVar['Controller | None'](f'{__name__}.controller_var', default=None)
_CallbackParams = ParamSpec('_CallbackParams')

type ErrorCallback = Callable[[ExceptionGroup[Exception]], MaybeAwaitable[None]]


class FlowTimeoutError(TimeoutError):
    """Raised internally when a flow view or modal reaches its configured timeout."""


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


class _ActiveMessage(NamedTuple):
    message: ComponentV2Message | Message
    view: _ViewType | None
    editable: _Editable
    messageable: Sendable


class _ResultAction(Enum):
    CONTINUE_BATCH = auto()
    REPLACE_VIEW = auto()
    TRANSITION_MODEL = auto()
    FINISH_FLOW = auto()


class _ResultOutcome(NamedTuple):
    action: _ResultAction
    transition: tuple[ModelBase, Sendable] | None = None


class _ResultTaskRecord(NamedTuple):
    task: Task[Result]
    model: ModelBase
    source: ExternalResultTask | None
    view: _ViewType | None


class _ViewTaskRecord(NamedTuple):
    task: Task[bool]
    model: ModelBase
    view: _ViewType


type _TaskRecord = _ResultTaskRecord | _ViewTaskRecord


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

    Note:
        Registering an external result while flow cleanup is in progress is not guaranteed. The task may be
        cancelled without its result being applied.
    """
    return _get_controller().create_external_result(coro, name=name, life_time=life_time)


class Controller:
    """Flow controller.

    This class is responsible for sending messages and waiting for interactions.

    Args:
        initial_model (ModelBase): Initial model. This model will be used first.
        on_error (ErrorCallback | None): Final fallback for errors not handled by a model.
    """

    model: ModelBase
    _active_message: _ActiveMessage | None
    _external_task_event: Event
    _tasks: dict[Task[Any], _TaskRecord]

    def __init__(
        self,
        initial_model: ModelBase,
        *,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self.model = initial_model
        self._active_message = None
        self._external_task_event = Event()
        self._tasks = {}
        self._current_model: ModelBase = initial_model
        self._error_callback = on_error

    def copy(self) -> Self:
        """Returns a copy of this controller.

        Returns:
            Controller: Copied controller.
        """
        copied = self.__class__(self.model)
        copied._error_callback = self._error_callback
        return copied

    def _raise_failures(self, message: str, failures: tuple[BaseException, ...]) -> None:
        if not failures:
            return
        if len(failures) == 1:
            raise failures[0]
        if all(isinstance(failure, Exception) for failure in failures):
            raise ExceptionGroup(message, cast('tuple[Exception, ...]', failures))
        raise BaseExceptionGroup(message, failures)

    async def invoke(self, messageable: Sendable, message: _Editable | None = None) -> None:
        """Invoke flow.

        Args:
            messageable (Sendable): Messageable or interaction to send first message.
            message (discord.Message | None): The first target for editing if edit_original is True. Defaults to None.

        Note:
            If ``messageable`` is an unacknowledged interaction without a triggering message and ``message`` is
            provided for a direct edit, the caller must acknowledge the interaction before invoking. Editing the
            explicit message target does not acknowledge the interaction response.
        """
        self._external_task_event.clear()
        self._current_model = self.model
        async with AsyncExitStack() as stack:
            stack.enter_context(self._set_to_context())
            stack.push_async_exit(self._cleanup_after_invoke)
            model = self.model
            while True:
                result = await self._run_model(model, messageable, message)
                if result is None:
                    break
                next_model, messageable, message = result
                if next_model != model:
                    await self._cancel_model_tasks()
                    self._current_model = next_model
                model = next_model

    async def _cleanup_after_invoke(
        self,
        _exception_type: type[BaseException] | None,
        exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        cleanup_failures: list[BaseException] = []
        try:
            await self._finalize_active_message()
        except BaseException as cleanup_exception:  # noqa: BLE001
            cleanup_failures.append(cleanup_exception)
        try:
            await self._cancel_and_drain_tasks()
        except BaseException as cleanup_exception:  # noqa: BLE001
            cleanup_failures.append(cleanup_exception)
        self._tasks.clear()
        failures = tuple(cleanup_failures)
        if exception is not None:
            self._raise_failures('Errors occurred during flow cleanup.', (exception, *failures))
        self._raise_failures('Errors occurred during flow cleanup.', failures)

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

        Note:
            Registering an external result while flow cleanup is in progress is not guaranteed. The task may be
            cancelled without its result being applied.
        """
        task = ExternalResultTask(coro, name=name, lifetime=life_time)
        self._tasks[task.task] = _ResultTaskRecord(task.task, self._current_model, task, None)
        self._external_task_event.set()
        return task

    def _set_to_context(self) -> AbstractContextManager[Token[Controller | None], None]:
        return _AutoResetControllerContext.from_token(controller_var.set(self))

    def _create_ui_task(
        self,
        view: _ViewType,
        callback: Callable[Concatenate[Interaction, _CallbackParams], MaybeAwaitable[Result]],
        interaction: Interaction,
        *values: _CallbackParams.args,
        **kwargs: _CallbackParams.kwargs,
    ) -> Task[Result] | None:
        if view.is_finished():
            return None

        async def invoke_callback() -> Result:
            with self._set_to_context():
                result = await maybe_coroutine(callback, interaction, *values, **kwargs)
            if result._interaction is None:
                result = replace(result, _interaction=interaction)
            return result

        task = create_task(invoke_callback(), name='flow-ui-callback')
        self._tasks[task] = _ResultTaskRecord(task, self._current_model, None, view)
        self._external_task_event.set()
        return task

    async def _run_model(
        self,
        model: ModelBase,
        messageable: Sendable,
        edit: _Editable | None,
    ) -> tuple[ModelBase, Sendable, _Editable] | None:
        self._current_model = model
        await maybe_coroutine(model.before_invoke)
        msg = await maybe_coroutine(model.message)
        if not isinstance(msg, (ComponentV2Message, Message)):
            raise TypeError('ModelBase.message must return ComponentV2Message or LegacyMessage.')
        if msg.items is None:
            await self._send_and_activate_message(messageable, msg, None, edit)
            await maybe_coroutine(model.after_invoke)
            return None

        view_config = await maybe_coroutine(model.view_config)
        view = create_view(config=view_config, items=msg.items, controller=self)
        await self._send_and_activate_message(messageable, msg, view, edit)

        if any(
            isinstance(record, _ResultTaskRecord) and isinstance(record.source, ExternalResultTask)
            for record in self._tasks.values()
        ) or view_can_produce_result(view):
            result = await self._wait_result(view)
        else:
            view.stop()
            result = None
        await self._retire_view(view)
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

    async def _finalize_active_message(self) -> None:
        active, self._active_message = self._active_message, None
        if active is None:
            return
        failures: list[BaseException] = []
        try:
            await self._disable_items(active)
        except BaseException as exception:  # noqa: BLE001
            failures.append(exception)
        if active.view is not None:
            try:
                await self._retire_view(active.view)
            except BaseException as exception:  # noqa: BLE001
                failures.append(exception)
        self._raise_failures('Errors occurred while finalizing the flow message.', tuple(failures))

    async def _send_and_activate_message(
        self,
        messageable: Sendable,
        message: ComponentV2Message | Message,
        view: _ViewType | None,
        edit: _Editable | None,
    ) -> _Editable:
        if view is not None:
            task = create_task(view.wait(), name='flow-view-finished')
            self._tasks[task] = _ViewTaskRecord(task, self._current_model, view)
            self._external_task_event.set()
        previous = self._active_message
        try:
            sent = await send_helper(messageable, message, view, edit)
        except BaseException as send_exception:
            if view is not None:
                try:
                    await self._retire_view(view)
                except BaseException as close_exception:  # noqa: BLE001
                    self._raise_failures(
                        'Errors occurred while closing a flow view after send failure.',
                        (send_exception, close_exception),
                    )
            raise
        self._active_message = _ActiveMessage(message, view, sent, messageable)
        if previous is not None and previous.view is not view and previous.editable.id != sent.id:
            failures: list[BaseException] = []
            try:
                await self._disable_items(previous)
            except BaseException as exception:  # noqa: BLE001
                failures.append(exception)
            if previous.view is not None:
                try:
                    await self._retire_view(previous.view)
                except BaseException as exception:  # noqa: BLE001
                    failures.append(exception)
            self._raise_failures('Errors occurred while replacing the previous flow message.', tuple(failures))
        elif previous is not None and previous.view is not view and previous.view is not None:
            await self._retire_view(previous.view)
        return sent

    async def _apply_result(
        self,
        view: _ViewType,
        result: Result,
    ) -> _ResultOutcome:
        if result._interaction is None:
            raise ValueError('result._interaction is None.')
        messageable = result._interaction

        match result._type:
            case _ResultTypeEnum.MESSAGE:
                assert result._message is not None
                assert messageable is not None
                replacement = create_view(
                    config=view.config,
                    items=result._message.items or (),
                    controller=self,
                )
                await self._send_and_activate_message(
                    messageable,
                    result._message,
                    replacement,
                    None if self._active_message is None else self._active_message.editable,
                )
                return _ResultOutcome(_ResultAction.REPLACE_VIEW)

            case _ResultTypeEnum.MODEL:
                assert result._model is not None
                assert messageable is not None
                view.stop()
                return _ResultOutcome(_ResultAction.TRANSITION_MODEL, (result._model, messageable))

            case _ResultTypeEnum.CONTINUE | _ResultTypeEnum.FINISH:
                if not messageable.response.is_done():
                    raise RuntimeError('Callback MUST consume interaction.')
                if result._is_end:
                    view.stop()
                    return _ResultOutcome(_ResultAction.FINISH_FLOW)
                active_view = None if self._active_message is None else self._active_message.view
                if active_view is view and view.is_finished():
                    return _ResultOutcome(_ResultAction.FINISH_FLOW)
                return _ResultOutcome(_ResultAction.CONTINUE_BATCH)

        raise TypeError('Flow handler must return Result or None.')

    async def _notify_errors(
        self,
        model_errors: list[tuple[ModelBase, list[Exception]]],
        controller_errors: list[Exception],
    ) -> tuple[BaseException, ...]:
        failures: list[BaseException] = []
        for model, errors in model_errors:
            if not errors:
                continue
            error_group = ExceptionGroup('Errors occurred in a flow model.', tuple(errors))
            handler = getattr(model, 'on_error', None)
            if handler is None:
                controller_errors.extend(errors)
                continue
            try:
                await maybe_coroutine(handler, error_group)
            except BaseException as exception:  # noqa: BLE001
                failures.extend((error_group, exception))
        if controller_errors:
            error_group = ExceptionGroup('Errors occurred in a flow.', tuple(controller_errors))
            handler = self._error_callback if self._error_callback is not None else self.on_error
            try:
                await maybe_coroutine(handler, error_group)
            except BaseException as exception:  # noqa: BLE001
                failures.extend((error_group, exception))
        return tuple(failures)

    def _add_model_error(
        self,
        model_errors: list[tuple[ModelBase, list[Exception]]],
        model: ModelBase,
        error: Exception,
    ) -> None:
        for registered_model, errors in model_errors:
            if registered_model is model:
                errors.append(error)
                return
        model_errors.append((model, [error]))

    async def _apply_completed_result(
        self,
        record: _ResultTaskRecord,
        result: Result,
        outcome: _ResultOutcome,
    ) -> tuple[_ResultOutcome, tuple[BaseException, ...]]:
        if record.task not in self._tasks:
            return outcome, ()
        persistent = record.source is not None and record.source._lifetime is ExternalTaskLifeTime.PERSISTENT
        active = self._active_message
        target = None if active is None else record.view if record.view is not None else active.view
        if (
            outcome.action in (_ResultAction.TRANSITION_MODEL, _ResultAction.FINISH_FLOW)
            or active is None
            or (record.view is not None and record.view is not active.view)
            or target is None
        ):
            if not persistent or record.view is not None:
                self._tasks.pop(record.task, None)
            return outcome, ()
        self._tasks.pop(record.task, None)
        try:
            result_outcome = await self._apply_result(target, result)
        except BaseException as error:  # noqa: BLE001
            return _ResultOutcome(_ResultAction.FINISH_FLOW), (error,)
        if result_outcome.action is _ResultAction.CONTINUE_BATCH and outcome.action is _ResultAction.REPLACE_VIEW:
            return outcome, ()
        return result_outcome, ()

    def _collect_result_records(
        self,
        records: Sequence[_TaskRecord],
        *,
        apply: bool,
    ) -> tuple[
        list[tuple[_ResultTaskRecord, Result]],
        list[tuple[ModelBase, list[Exception]]],
        list[Exception],
        list[BaseException],
    ]:
        result_records = [record for record in records if isinstance(record, _ResultTaskRecord)]
        result_records.sort(key=lambda record: 0 if record.source is None else 1)
        completed: list[tuple[_ResultTaskRecord, Result]] = []
        model_errors: list[tuple[ModelBase, list[Exception]]] = []
        controller_errors: list[Exception] = []
        failures: list[BaseException] = []
        for record in result_records:
            if record.task not in self._tasks:
                continue
            if record.task.cancelled():
                self._tasks.pop(record.task, None)
                continue
            try:
                result = record.task.result()
            except Exception as error:  # noqa: BLE001
                self._tasks.pop(record.task, None)
                if record.source is None and getattr(record.model, 'on_error', None) is not None and apply:
                    self._add_model_error(model_errors, record.model, error)
                else:
                    controller_errors.append(error)
            except BaseException as error:  # noqa: BLE001
                self._tasks.pop(record.task, None)
                failures.append(error)
            else:
                completed.append((record, result))
        return completed, model_errors, controller_errors, failures

    def _collect_view_records(
        self,
        records: Sequence[_TaskRecord],
        model_errors: list[tuple[ModelBase, list[Exception]]],
        failures: list[BaseException],
    ) -> tuple[list[_ViewType], list[_ViewType]]:
        timed_out_views: list[_ViewType] = []
        stopped_views: list[_ViewType] = []
        for record in records:
            if not isinstance(record, _ViewTaskRecord) or record.task not in self._tasks:
                continue
            self._tasks.pop(record.task, None)
            if record.task.cancelled():
                continue
            try:
                timed_out = record.task.result()
            except BaseException as error:  # noqa: BLE001
                failures.append(error)
                continue
            if timed_out:
                timed_out_views.append(record.view)
                self._add_model_error(model_errors, record.model, FlowTimeoutError('The flow view timed out.'))
            else:
                stopped_views.append(record.view)
        return timed_out_views, stopped_views

    async def _process_records(
        self,
        records: Sequence[_TaskRecord],
        *,
        apply: bool,
    ) -> tuple[_ResultOutcome, tuple[BaseException, ...]]:
        completed, model_errors, controller_errors, failures = self._collect_result_records(records, apply=apply)
        timed_out_views, stopped_views = self._collect_view_records(records, model_errors, failures)

        if not apply:
            model_errors = [
                (model, errors)
                for model, errors in model_errors
                if any(isinstance(error, FlowTimeoutError) for error in errors)
            ]
            failures.extend(controller_errors)
            controller_errors = []
        handler_failures = await self._notify_errors(model_errors, controller_errors)
        failures.extend(handler_failures)
        outcome = _ResultOutcome(_ResultAction.CONTINUE_BATCH)
        if not apply:
            for record, _result in completed:
                self._tasks.pop(record.task, None)
        if not failures and apply:
            for record, result in completed:
                outcome, result_failures = await self._apply_completed_result(record, result, outcome)
                failures.extend(result_failures)
                if result_failures:
                    break
        if failures:
            return _ResultOutcome(_ResultAction.FINISH_FLOW), tuple(failures)
        if apply and outcome.action is _ResultAction.CONTINUE_BATCH:
            active = self._active_message
            if active is not None and any(active.view is view for view in (*timed_out_views, *stopped_views)):
                outcome = _ResultOutcome(_ResultAction.FINISH_FLOW)
        return outcome, ()

    async def _wait_on_view(self, view: _ViewType) -> _ResultOutcome:
        while True:
            has_external_result = any(
                isinstance(record, _ResultTaskRecord) and isinstance(record.source, ExternalResultTask)
                for record in self._tasks.values()
            )
            has_view_result = any(
                isinstance(record, _ResultTaskRecord) and record.view is view for record in self._tasks.values()
            )
            if not view_can_produce_result(view) and not has_external_result and not has_view_result:
                view.stop()
                return _ResultOutcome(_ResultAction.FINISH_FLOW)
            ready = [record for task, record in self._tasks.items() if task.done()]
            if ready:
                outcome, failures = await self._process_records(ready, apply=True)
                if failures:
                    self._raise_failures('Errors occurred while processing flow tasks.', failures)
                if outcome.action is not _ResultAction.CONTINUE_BATCH:
                    return outcome
                if view.is_finished() and self._active_message is not None and self._active_message.view is view:
                    return _ResultOutcome(_ResultAction.FINISH_FLOW)
                continue

            pending = [task for task in self._tasks if not task.done()]
            if not pending:
                view.stop()
                return _ResultOutcome(_ResultAction.FINISH_FLOW)
            self._external_task_event.clear()
            if any(task.done() for task in self._tasks):
                continue
            added = create_task(self._external_task_event.wait(), name='flow-task-added')
            pending.append(added)
            try:
                await wait(pending, return_when=FIRST_COMPLETED)
            finally:
                if added.done():
                    self._external_task_event.clear()
                else:
                    added.cancel()
                    await gather(added, return_exceptions=True)

    async def _wait_result(self, initial_view: _ViewType) -> tuple[ModelBase, Sendable] | None:
        view: _ViewType | None = initial_view
        while view is not None:
            outcome = await self._wait_on_view(view)
            if outcome.action is _ResultAction.TRANSITION_MODEL:
                assert outcome.transition is not None
                return outcome.transition
            if outcome.action is _ResultAction.FINISH_FLOW:
                return None
            if outcome.action is _ResultAction.REPLACE_VIEW:
                view = None if self._active_message is None else self._active_message.view
                continue
            raise RuntimeError('View wait returned without a terminal action.')
        return None

    async def _retire_view(self, view: _ViewType) -> None:
        view.stop()
        failures: list[BaseException] = []
        while True:
            records = tuple(
                record
                for record in self._tasks.values()
                if (isinstance(record, _ResultTaskRecord) and record.view is view)
                or (isinstance(record, _ViewTaskRecord) and record.view is view)
            )
            if not records:
                break
            failures.extend(await self._reclaim_tasks(records))
        self._raise_failures('Errors occurred while retiring a flow view.', tuple(failures))

    async def _reclaim_tasks(self, records: Sequence[_TaskRecord]) -> tuple[BaseException, ...]:
        for record in records:
            if isinstance(record, _ViewTaskRecord):
                record.view.stop()
            elif not record.task.done():
                record.task.cancel()
        await gather(*(cast('Task[Any]', record.task) for record in records), return_exceptions=True)
        _, process_failures = await self._process_records(records, apply=False)
        return process_failures

    async def _cancel_model_tasks(self) -> None:
        model = self._current_model
        failures: list[BaseException] = []
        while True:
            records = [
                record
                for record in self._tasks.values()
                if isinstance(record, _ResultTaskRecord)
                and record.model is model
                and isinstance(record.source, ExternalResultTask)
                and record.source._lifetime.is_model()
            ]
            if not records:
                break
            failures.extend(await self._reclaim_tasks(records))
        self._raise_failures('Errors occurred while cancelling model tasks.', tuple(failures))

    async def _cancel_and_drain_tasks(self) -> None:
        failures: list[BaseException] = []
        while self._tasks:
            records = tuple(self._tasks.values())
            failures.extend(await self._reclaim_tasks(records))
        self._raise_failures('Errors occurred while draining flow tasks.', tuple(failures))

    def on_error(self, error: ExceptionGroup[Exception]) -> MaybeAwaitable[None]:
        """Handle errors not handled by a model.

        Applications should normally catch expected exceptions inside callbacks and external result
        coroutines and return a :class:`Result`.  This hook is a final fallback for errors that escape
        those operations and for view timeouts.
        """
        logger.error('Ignoring Exceptions:', exc_info=error)
        return None
