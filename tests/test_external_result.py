from __future__ import annotations

import asyncio
from itertools import count
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import discord.ext.flow.controller as controller_module
import discord.ext.flow.util as util_module
import pytest
from discord import Client, Interaction, ui
from discord.abc import Messageable
from discord.ext.flow import (
    ActionRow,
    Button,
    ComponentV2Message,
    LegacyMessage,
    ModalConfig,
    ModelBase,
    Result,
    TextDisplay,
    TextInput,
)
from discord.ext.flow.controller import Controller, _CompletedResult, _ResultBatch, _ResultWaiter
from discord.ext.flow.modal import _InnerModal
from discord.ext.flow.util import _Editable, force_cancel_tasks
from discord.ext.flow.view import create_view

if TYPE_CHECKING:
    from discord.ext.flow import ExternalResultTask
    from discord.ext.flow.model import ViewConfig
    from discord.ext.flow.view import _ViewType


def _interaction() -> Interaction[Client]:
    """Create a typed interaction double when only identity is relevant."""
    return MagicMock(spec=Interaction)


def _messageable() -> Messageable:
    """Create a typed messageable double when sending is mocked at the flow boundary."""
    return MagicMock(spec=Messageable)


_message_ids = count(1)


def _editable(*, message_id: int | None = None) -> _Editable:
    """Create a typed editable-message double when it is not exercised by the test."""
    editable = MagicMock(spec=_Editable)
    editable.id = next(_message_ids) if message_id is None else message_id
    return editable


def _sent(message: _Editable | None = None) -> _Editable:
    return _editable() if message is None else message


def _tracked_editable(name: str, events: list[str]) -> tuple[_Editable, AsyncMock]:
    target = _editable()
    edit_mock = cast('AsyncMock', target.edit)

    async def edit(**_: object) -> _Editable:
        events.append(f'disable-{name}')
        return target

    edit_mock.side_effect = edit
    return target, edit_mock


def _first_button(view: _ViewType) -> ui.Button[_ViewType]:
    return cast('ui.Button[_ViewType]', next(child for child in view.walk_children() if isinstance(child, ui.Button)))


def _finish(_: Interaction[Client]) -> Result:
    return Result.finish_flow()


class _InteractiveModel(ModelBase):
    def message(self) -> LegacyMessage:
        return LegacyMessage(items=(Button().on(callback=_finish),))


class _StaticModel(ModelBase):
    after_invoked = False

    def message(self) -> ComponentV2Message:
        return ComponentV2Message(items=(TextDisplay('Finished'),))

    def after_invoke(self) -> None:
        self.after_invoked = True


@pytest.mark.parametrize(
    'message',
    [
        LegacyMessage(items=(Button(disabled=True).on(callback=_finish),)),
        ComponentV2Message(items=(ActionRow(items=(Button(disabled=True).on(callback=_finish),)),)),
    ],
)
@pytest.mark.asyncio
async def test_disabled_only_layout_completes_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
    message: ComponentV2Message | LegacyMessage,
) -> None:
    """Disabled controls are not treated as active flow result sources."""

    class Model(ModelBase):
        after_invoked = False

        def message(self) -> ComponentV2Message | LegacyMessage:
            return message

        def after_invoke(self) -> None:
            self.after_invoked = True

    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(return_value=_sent()))
    model = Model()

    await asyncio.wait_for(Controller(model).invoke(_messageable()), timeout=0.1)

    assert model.after_invoked


@pytest.mark.asyncio
async def test_disabled_only_replacement_stops_active_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing active controls with disabled controls completes the view."""
    sent = asyncio.Event()
    editables = (_editable(), _editable())

    async def send(*_: object) -> _Editable:
        sent.set()
        return _sent(editables[send_mock.await_count - 1])

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)
    controller = Controller(_InteractiveModel())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(sent.wait(), timeout=0.1)
    view = send_mock.await_args_list[0].args[2]
    interaction = _interaction()

    await view._set_result(
        Result.send_message(
            LegacyMessage(items=(Button(disabled=True).on(callback=_finish),)),
            interaction=interaction,
        ),
        interaction,
    )
    await asyncio.wait_for(invocation, timeout=0.1)

    assert view.is_finished()
    replacement = send_mock.await_args_list[1].args[2]
    assert replacement.is_finished()


@pytest.mark.parametrize('source', ['callback', 'external'])
@pytest.mark.parametrize(
    ('replacement_disable_items', 'expected_events'),
    [
        (False, ['send-0', 'send-1', 'disable-initial']),
        (True, ['send-0', 'send-1', 'disable-initial', 'disable-replacement']),
    ],
)
@pytest.mark.asyncio
async def test_new_message_replacement_finalizes_each_message_with_its_own_flag(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    replacement_disable_items: bool,
    expected_events: list[str],
) -> None:
    """A new message disables the previous controls and becomes the next finalization target."""
    release_external = asyncio.Event()
    initial_sent = asyncio.Event()
    replacement_sent = asyncio.Event()
    events: list[str] = []
    interaction = _interaction()
    cast('AsyncMock', interaction.response.is_done).return_value = True
    replacement = ComponentV2Message(
        items=(ActionRow(items=(Button(label='Replacement').on(callback=_finish),)),),
        disable_items=replacement_disable_items,
    )
    initial_edit, initial_edit_mock = _tracked_editable('initial', events)
    replacement_edit, replacement_edit_mock = _tracked_editable('replacement', events)
    editables = (initial_edit, replacement_edit)
    sent_events = (initial_sent, replacement_sent)

    async def send(*_: object) -> _Editable:
        index = send_mock.await_count - 1
        events.append(f'send-{index}')
        sent_events[index].set()
        return _sent(editables[index])

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)

    class Model(ModelBase):
        def message(self) -> ComponentV2Message:
            return ComponentV2Message(
                items=(ActionRow(items=(Button(label='Initial').on(callback=_finish),)),),
                disable_items=True,
            )

    controller = Controller(_InteractiveModel())
    controller.model = Model()
    if source == 'external':

        async def replace_message() -> Result:
            await release_external.wait()
            return Result.send_message(replacement, interaction=interaction)

        controller.create_external_result(replace_message)
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(initial_sent.wait(), timeout=0.1)

    initial_view = send_mock.await_args_list[0].args[2]
    if source == 'callback':
        await initial_view._set_result(Result.send_message(replacement), interaction)
    else:
        release_external.set()
    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)

    assert events[:3] == ['send-0', 'send-1', 'disable-initial']
    initial_button = _first_button(initial_view)
    assert initial_button.disabled
    initial_edit_mock.assert_awaited_once_with(view=initial_view)

    replacement_view = send_mock.await_args_list[1].args[2]
    replacement_button = _first_button(replacement_view)
    assert not replacement_button.disabled
    assert not invocation.done()

    await replacement_view._set_result(Result.finish_flow(), interaction)
    await asyncio.wait_for(invocation, timeout=0.1)

    assert events == expected_events
    assert replacement_button.disabled is replacement_disable_items
    assert replacement_edit_mock.await_count == int(replacement_disable_items)


@pytest.mark.asyncio
async def test_edit_original_replacement_overwrites_without_disabling_previous_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Editing the active message replaces its controls instead of finalizing the old representation first."""
    initial_sent = asyncio.Event()
    replacement_sent = asyncio.Event()
    initial_edit = _editable()
    replacement_edit = _editable(message_id=initial_edit.id)
    initial_edit_mock = cast('AsyncMock', initial_edit.edit)
    editables = (initial_edit, replacement_edit)
    sent_events = (initial_sent, replacement_sent)

    async def send(*_: object) -> _Editable:
        index = send_mock.await_count - 1
        sent_events[index].set()
        return _sent(editables[index])

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)

    class Model(ModelBase):
        def message(self) -> LegacyMessage:
            return LegacyMessage(items=(Button(label='Initial').on(callback=_finish),), disable_items=True)

    controller = Controller(Model())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(initial_sent.wait(), timeout=0.1)

    initial_view = send_mock.await_args_list[0].args[2]
    interaction = _interaction()
    cast('AsyncMock', interaction.response.is_done).return_value = True
    replacement = LegacyMessage(
        items=(Button(label='Replacement').on(callback=_finish),),
        edit_original=True,
    )
    await initial_view._set_result(Result.send_message(replacement), interaction)
    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)

    initial_edit_mock.assert_not_awaited()
    assert send_mock.await_args_list[1].args[3] is initial_edit
    replacement_view = send_mock.await_args_list[1].args[2]
    await replacement_view._set_result(Result.finish_flow(), interaction)
    await asyncio.wait_for(invocation, timeout=0.1)


@pytest.mark.asyncio
async def test_interaction_edit_of_different_message_finalizes_active_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interaction response editing message B finalizes the distinct active message A."""
    events: list[str] = []
    initial_message, initial_edit_mock = _tracked_editable('initial', events)
    interaction_message = _editable()
    assert initial_message.id != interaction_message.id
    response = SimpleNamespace(is_done=lambda: False, edit_message=AsyncMock())

    class FakeInteraction:
        def __init__(self) -> None:
            self.response = response
            self.message = interaction_message
            self.original_response = AsyncMock(return_value=interaction_message)

    controller = Controller(_InteractiveModel())
    initial_config = LegacyMessage(
        items=(Button(label='Initial').on(callback=_finish),),
        disable_items=True,
    )
    view_config: ViewConfig = {'timeout': 42.0}
    initial_view = create_view(view_config, initial_config.items or (), controller)
    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(return_value=initial_message))
    await controller._send_message(_messageable(), initial_config, initial_view, None)

    monkeypatch.setattr(util_module, 'Interaction', FakeInteraction)
    monkeypatch.setattr(controller_module, 'send_helper', util_module.send_helper)
    interaction = cast('Interaction[Client]', FakeInteraction())
    replacement = LegacyMessage(
        items=(Button(label='Replacement').on(callback=_finish),),
        edit_original=True,
    )

    outcome = await controller._exec_result(
        initial_view,
        Result.send_message(replacement, interaction=interaction),
    )

    assert outcome.switched_view
    response.edit_message.assert_awaited_once()
    assert events == ['disable-initial']
    assert _first_button(initial_view).disabled
    initial_edit_mock.assert_awaited_once_with(view=initial_view)
    assert initial_view.is_finished()
    assert controller._active_message is not None
    assert controller._active_message.editable is interaction_message
    assert controller._active_message.view is not None
    assert controller._active_message.view.config is view_config

    await controller._finalize_active_message()


@pytest.mark.parametrize('replacement_kind', ['message', 'model'])
@pytest.mark.asyncio
async def test_failed_replacement_send_finalizes_previous_message_in_invoke_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    """A failed replacement leaves the previous message active for the common invoke cleanup."""
    initial_sent = asyncio.Event()
    events: list[str] = []
    initial_edit, initial_edit_mock = _tracked_editable('initial', events)

    async def send(*_: object) -> _Editable:
        index = send_mock.await_count - 1
        events.append(f'send-{index}')
        if index == 0:
            initial_sent.set()
            return _sent(initial_edit)
        raise RuntimeError('replacement failed')

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)

    class InitialModel(ModelBase):
        def message(self) -> LegacyMessage:
            return LegacyMessage(items=(Button(label='Initial').on(callback=_finish),), disable_items=True)

    class ReplacementModel(ModelBase):
        def message(self) -> LegacyMessage:
            return LegacyMessage(content='Replacement')

    controller = Controller(InitialModel())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(initial_sent.wait(), timeout=0.1)

    initial_view = send_mock.await_args_list[0].args[2]
    interaction = _interaction()
    result = (
        Result.send_message(LegacyMessage(content='Replacement'))
        if replacement_kind == 'message'
        else Result.next_model(ReplacementModel())
    )
    await initial_view._set_result(result, interaction)

    with pytest.raises(RuntimeError, match='replacement failed'):
        await asyncio.wait_for(invocation, timeout=0.1)

    assert events == ['send-0', 'send-1', 'disable-initial']
    assert _first_button(initial_view).disabled
    initial_edit_mock.assert_awaited_once_with(view=initial_view)


@pytest.mark.asyncio
async def test_reused_controller_clears_stale_external_task_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal invocation cannot leave a completed task-added waiter for the next invocation."""
    send = AsyncMock(return_value=_sent())
    monkeypatch.setattr(controller_module, 'send_helper', send)

    class TerminalModel(ModelBase):
        def __init__(self, controller: Controller) -> None:
            self.controller = controller

        def before_invoke(self) -> None:
            async def pending() -> Result:
                await asyncio.Event().wait()
                return Result.finish_flow()

            self.controller.create_external_result(pending)

        def message(self) -> LegacyMessage:
            return LegacyMessage()

    controller = Controller(_InteractiveModel())
    controller.model = TerminalModel(controller)
    await controller.invoke(_messageable())
    assert controller._external_task_event.is_set()

    controller.model = _InteractiveModel()
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    for _ in range(3):
        await asyncio.sleep(0)

    assert not invocation.done()
    assert not controller._external_task_event.is_set()
    view = send.await_args_list[-1].args[2]
    await view._set_result(Result.finish_flow(), _interaction())
    await asyncio.wait_for(invocation, timeout=0.1)


@pytest.mark.asyncio
async def test_unprocessed_persistent_results_continue_into_next_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model transition preserves other completed persistent results in the same batch."""
    interaction = _interaction()
    first_model = _StaticModel()
    second_model = _StaticModel()

    class InitialModel(ModelBase):
        def __init__(self, controller: Controller) -> None:
            self.controller = controller
            self.tasks: tuple[ExternalResultTask, ExternalResultTask] | None = None

        def before_invoke(self) -> None:
            async def first_transition() -> Result:
                return Result.next_model(first_model, interaction=interaction)

            async def second_transition() -> Result:
                return Result.next_model(second_model, interaction=interaction)

            self.tasks = (
                self.controller.create_external_result(first_transition),
                self.controller.create_external_result(second_transition),
            )

        async def message(self) -> ComponentV2Message:
            assert self.tasks is not None
            await asyncio.gather(*(task.task for task in self.tasks))
            return ComponentV2Message(items=(TextDisplay('Initial'),))

    send = AsyncMock(return_value=_sent())
    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(_StaticModel())
    controller.model = InitialModel(controller)

    await asyncio.wait_for(controller.invoke(_messageable()), timeout=0.1)

    assert send.await_count == 3
    assert first_model.after_invoked
    assert second_model.after_invoked


@pytest.mark.asyncio
async def test_view_model_result_precedes_simultaneous_external_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A direct component transition wins before a completed external replacement."""
    interaction = _interaction()
    initial_message = LegacyMessage(items=(Button(label='Initial').on(callback=_finish),))
    next_message = LegacyMessage(content='Next model', items=())
    external_message = LegacyMessage(content='External replacement', items=())

    class NextModel(ModelBase):
        def message(self) -> LegacyMessage:
            return next_message

    next_model = NextModel()

    class InitialModel(ModelBase):
        def __init__(self, controller: Controller) -> None:
            self.controller = controller
            self.external_task: ExternalResultTask | None = None

        def before_invoke(self) -> None:
            async def replace_message() -> Result:
                return Result.send_message(external_message, interaction=interaction)

            self.external_task = self.controller.create_external_result(replace_message)

        async def message(self) -> LegacyMessage:
            assert self.external_task is not None
            await self.external_task.task
            return initial_message

    sent_messages: list[LegacyMessage] = []

    async def send(_: object, message: LegacyMessage, view: _ViewType, __: object) -> _Editable:
        sent_messages.append(message)
        if message is initial_message:
            await view._set_result(Result.next_model(next_model), interaction)
        return _sent()

    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(side_effect=send))
    controller = Controller(_StaticModel())
    controller.model = InitialModel(controller)

    await asyncio.wait_for(controller.invoke(_messageable()), timeout=0.1)

    assert sent_messages == [initial_message, next_message, external_message]


@pytest.mark.asyncio
async def test_view_continue_precedes_simultaneous_external_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement remains interactive after a simultaneous component continue result."""
    interaction = _interaction()
    initial_message = LegacyMessage(items=(Button(label='Initial').on(callback=_finish),))
    replacement_message = LegacyMessage(items=(Button(label='Replacement').on(callback=_finish),))
    replacement_sent = asyncio.Event()

    class InitialModel(ModelBase):
        def __init__(self, controller: Controller) -> None:
            self.controller = controller
            self.external_task: ExternalResultTask | None = None

        def before_invoke(self) -> None:
            async def replace_message() -> Result:
                return Result.send_message(replacement_message, interaction=interaction)

            self.external_task = self.controller.create_external_result(replace_message)

        async def message(self) -> LegacyMessage:
            assert self.external_task is not None
            await self.external_task.task
            return initial_message

    sent_views: list[_ViewType] = []

    async def send(_: object, message: LegacyMessage, view: _ViewType, __: object) -> _Editable:
        sent_views.append(view)
        if message is initial_message:
            await view._set_result(Result.continue_flow(), interaction)
        else:
            replacement_sent.set()
        return _sent()

    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(side_effect=send))
    controller = Controller(_StaticModel())
    controller.model = InitialModel(controller)
    invocation = asyncio.create_task(controller.invoke(_messageable()))

    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)
    await asyncio.sleep(0)

    replacement_view = sent_views[-1]
    assert not invocation.done()
    assert not replacement_view.is_finished()

    await replacement_view._set_result(Result.finish_flow(), interaction)
    await asyncio.wait_for(invocation, timeout=0.1)


@pytest.mark.asyncio
async def test_result_waiter_consumes_external_results_as_they_are_yielded() -> None:
    """A result batch preserves external tasks until their results are yielded."""
    controller = Controller(_StaticModel())

    async def finish() -> Result:
        return Result.finish_flow()

    tasks = {controller.create_external_result(finish), controller.create_external_result(finish)}
    await asyncio.gather(*(task.task for task in tasks))
    view = create_view({}, (TextDisplay('Waiting'),), controller)

    async with _ResultWaiter(controller, view) as waiter:
        batch = await waiter.wait()

        assert isinstance(batch, tuple)
        assert isinstance(batch.exceptions, tuple)
        assert isinstance(batch.base_exceptions, tuple)
        assert iter(batch.results) is batch.results
        assert waiter.external_tasks == tasks

        completed = next(batch.results)

        assert completed.source in tasks
        assert completed.source not in waiter.external_tasks
        assert len(waiter.external_tasks) == 1

    assert controller.external_tasks == tasks - {completed.source}
    view.stop()
    view.fut.cancel()


@pytest.mark.asyncio
async def test_result_waiter_appends_view_timeout_after_completed_results() -> None:
    """A view timeout shares its batch with completed view and external result sources."""
    controller = Controller(_StaticModel())
    interaction = _interaction()
    view_model = _StaticModel()
    view_result = Result.next_model(view_model, interaction=interaction)
    external_message = LegacyMessage(content='External result')
    external_result = Result.send_message(external_message, interaction=interaction)

    async def complete_external() -> Result:
        return external_result

    external_task = controller.create_external_result(complete_external)
    await external_task.task
    view = create_view({}, (Button(label='Finish').on(callback=_finish),), controller)
    await view._set_result(view_result, interaction)
    view.stop()

    async with _ResultWaiter(controller, view) as waiter:
        batch = await waiter.wait()
        completed = tuple(batch.results)

    assert batch.view_finished
    assert [item.result for item in completed] == [view_result, external_result]
    assert completed[0].source is None
    assert completed[1].source is external_task
    assert not controller.external_tasks
    view.fut.cancel()


@pytest.mark.asyncio
async def test_view_timeout_runs_completed_model_transition_before_finishing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed model transition precedes the terminal timeout at the end of its batch."""
    controller = Controller(_StaticModel())
    interaction = _interaction()
    initial_message = LegacyMessage(items=(Button(label='Initial').on(callback=_finish),))
    next_model = _StaticModel()
    result = Result.next_model(next_model, interaction=interaction)
    send_mock = AsyncMock(return_value=_sent())
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)
    initial_view = create_view({}, initial_message.items or (), controller)
    await controller._send_message(_messageable(), initial_message, initial_view, None)

    async def finish_wait(waiter: _ResultWaiter) -> _ResultBatch:
        waiter.view.stop()
        return _ResultBatch(iter((_CompletedResult(result, source=None),)), view_finished=True)

    monkeypatch.setattr(_ResultWaiter, 'wait', finish_wait)

    transition = await controller._wait_result(initial_view)

    assert transition == (next_model, interaction)
    assert send_mock.await_count == 1

    await controller._finalize_active_message()


@pytest.mark.asyncio
async def test_view_timeout_switches_to_completed_external_message_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An external replacement remains interactive when the prior view times out in the same batch."""
    controller = Controller(_StaticModel())
    interaction = _interaction()
    cast('AsyncMock', interaction.response.is_done).return_value = True
    initial_message = LegacyMessage(items=(Button(label='Initial').on(callback=_finish),))
    replacement_message = LegacyMessage(items=(Button(label='Replacement').on(callback=_finish),))
    replacement_result = Result.send_message(replacement_message, interaction=interaction)

    async def replace_message() -> Result:
        return replacement_result

    external_task = controller.create_external_result(replace_message)
    await external_task.task
    replacement_sent = asyncio.Event()
    sent_views: list[_ViewType] = []

    async def send(_: object, message: LegacyMessage, view: _ViewType, __: object) -> _Editable:
        sent_views.append(view)
        if message is replacement_message:
            replacement_sent.set()
        return _sent()

    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(side_effect=send))
    initial_view = create_view({}, initial_message.items or (), controller)
    await controller._send_message(_messageable(), initial_message, initial_view, None)
    original_wait = _ResultWaiter.wait

    async def finish_initial_wait(waiter: _ResultWaiter) -> _ResultBatch:
        if waiter.view is initial_view:
            await waiter._take_registered_tasks()
            waiter.view.stop()
            batch = waiter._collect_external_results()
            return _ResultBatch(
                batch.results,
                batch.exceptions,
                batch.base_exceptions,
                view_finished=True,
            )
        return await original_wait(waiter)

    monkeypatch.setattr(_ResultWaiter, 'wait', finish_initial_wait)
    waiting = asyncio.create_task(controller._wait_result(initial_view))
    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)
    await asyncio.sleep(0)

    replacement_view = sent_views[-1]
    assert not waiting.done()
    assert not replacement_view.is_finished()

    await replacement_view._set_result(Result.finish_flow(), interaction)
    assert await asyncio.wait_for(waiting, timeout=0.1) is None

    await controller._finalize_active_message()


@pytest.mark.asyncio
async def test_continue_result_ignores_a_replaced_view_terminal_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Continue does not terminate the active replacement because the prior view was stopped."""
    interaction = _interaction()
    controller = Controller(_StaticModel())
    initial_message = LegacyMessage(items=(Button(label='Initial').on(callback=_finish),))
    replacement_message = LegacyMessage(items=(Button(label='Replacement').on(callback=_finish),))
    initial_view = create_view({}, initial_message.items or (), controller)
    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(return_value=_sent()))
    await controller._send_message(_messageable(), initial_message, initial_view, None)
    await initial_view._set_result(Result.continue_flow(), interaction)
    continue_result = await initial_view._wait()

    outcome = await controller._exec_result(
        initial_view,
        Result.send_message(replacement_message, interaction=interaction),
    )
    continue_outcome = await controller._exec_result(initial_view, continue_result)

    assert outcome.switched_view
    assert initial_view.is_finished()
    assert controller._active_message is not None
    assert controller._active_message.view is not None
    assert not controller._active_message.view.is_finished()
    assert not continue_outcome.terminal

    await controller._finalize_active_message()


@pytest.mark.asyncio
async def test_view_timeout_completes_controller_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """A discord.py view timeout also completes the flow's result wait."""
    sent = asyncio.Event()
    captured_view: _ViewType | None = None

    async def send(_: object, __: object, view: _ViewType, ___: object) -> _Editable:
        nonlocal captured_view
        captured_view = view
        sent.set()
        return _sent()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    invoke = asyncio.create_task(Controller(_InteractiveModel()).invoke(_messageable()))
    await asyncio.wait_for(sent.wait(), timeout=0.1)

    assert captured_view is not None
    captured_view._dispatch_timeout()  # type: ignore[no-untyped-call]
    await asyncio.wait_for(invoke, timeout=0.1)

    assert captured_view.is_finished()


@pytest.mark.asyncio
async def test_view_timeout_during_external_error_handler_completes_controller_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeout observed while handling an external error still completes the flow."""
    sent = asyncio.Event()
    error_started = asyncio.Event()
    release_error = asyncio.Event()
    captured_view: _ViewType | None = None

    class WaitingErrorController(Controller):
        async def on_error(self, _exception_group: BaseExceptionGroup) -> None:
            error_started.set()
            await release_error.wait()

    class FailingModel(_InteractiveModel):
        def __init__(self, controller: Controller) -> None:
            self.controller = controller

        def before_invoke(self) -> None:
            async def fail() -> Result:
                raise RuntimeError('external task failed')

            self.controller.create_external_result(fail)

    async def send(_: object, __: object, view: _ViewType, ___: object) -> _Editable:
        nonlocal captured_view
        captured_view = view
        sent.set()
        return _sent()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = WaitingErrorController(_InteractiveModel())
    model = FailingModel(controller)
    controller.model = model
    invoke = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(sent.wait(), timeout=0.1)
    await asyncio.wait_for(error_started.wait(), timeout=0.1)

    assert captured_view is not None
    captured_view._dispatch_timeout()  # type: ignore[no-untyped-call]
    release_error.set()
    await asyncio.wait_for(invoke, timeout=0.1)

    assert captured_view.is_finished()


@pytest.mark.asyncio
async def test_modal_timeout_releases_its_external_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timed-out modal does not keep a static flow waiting indefinitely."""

    class ModalModel(_StaticModel):
        def __init__(self, controller: Controller) -> None:
            self.controller = controller
            self.modal: _InnerModal | None = None

        def before_invoke(self) -> None:
            self.modal = _InnerModal(
                ModalConfig(title='Timed out'),
                (TextInput(label='Value').field(),),
                lambda _interaction: Result.finish_flow(),
            )
            self.controller.create_external_result(self.modal._wait, name='modal-wait')
            # d.py exposes timeout delivery only through this private, untyped dispatcher.
            asyncio.get_running_loop().call_soon(self.modal._dispatch_timeout)  # type: ignore[no-untyped-call]

    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(return_value=_sent()))
    controller = Controller(_StaticModel())
    model = ModalModel(controller)
    controller.model = model

    messageable: Messageable = AsyncMock()
    await asyncio.wait_for(controller.invoke(messageable), timeout=0.1)

    assert model.modal is not None
    assert model.modal.is_finished()
    assert model.modal.fut.cancelled()
    assert model.after_invoked


@pytest.mark.asyncio
async def test_cancelled_external_result_does_not_preempt_view_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Intentional external task cancellation leaves the active view usable."""
    sent = asyncio.Event()
    never_finishes = asyncio.Event()
    captured_view: _ViewType | None = None

    class Model(_InteractiveModel):
        task: ExternalResultTask

        def __init__(self, controller: Controller) -> None:
            self.controller = controller

        def before_invoke(self) -> None:
            async def wait_forever() -> Result:
                await never_finishes.wait()
                return Result.finish_flow()

            self.task = self.controller.create_external_result(wait_forever, name='modal-wait')

    async def send(_: object, __: object, view: _ViewType, ___: object) -> _Editable:
        nonlocal captured_view
        captured_view = view
        sent.set()
        return _sent()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(_InteractiveModel())
    model = Model(controller)
    controller.model = model
    invoke = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(sent.wait(), timeout=0.1)

    model.task.cancel()
    await asyncio.sleep(0)
    assert captured_view is not None
    await captured_view._set_result(Result.finish_flow(), _interaction())
    await asyncio.wait_for(invoke, timeout=0.1)

    assert model.task.task.cancelled()


@pytest.mark.asyncio
async def test_cancelled_final_external_result_releases_static_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale view waiter cannot keep a static replacement alive after cancellation."""
    replacement_sent = asyncio.Event()
    never_finishes = asyncio.Event()
    interaction = _interaction()

    class Model(_InteractiveModel):
        pending_task: ExternalResultTask

        def __init__(self, controller: Controller) -> None:
            self.controller = controller

        def before_invoke(self) -> None:
            async def replace_message() -> Result:
                return Result.send_message(LegacyMessage(content='Finished', items=()), interaction=interaction)

            async def wait_forever() -> Result:
                await never_finishes.wait()
                return Result.finish_flow()

            self.controller.create_external_result(replace_message)
            self.pending_task = self.controller.create_external_result(wait_forever)

    async def send(*_: object) -> _Editable:
        if send_mock.await_count == 2:
            replacement_sent.set()
        return _sent()

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)
    monkeypatch.setattr(util_module, 'send_helper', send_mock)
    controller = Controller(_InteractiveModel())
    model = Model(controller)
    controller.model = model

    invoke = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)
    assert not invoke.done()

    model.pending_task.cancel()
    await asyncio.wait_for(invoke, timeout=0.1)

    assert model.pending_task.task.cancelled()
    assert send_mock.await_count == 2


@pytest.mark.asyncio
async def test_force_cancel_tasks_waits_for_generator_tasks_to_finish() -> None:
    """Cancellation waits for cleanup even when the caller supplies a generator."""
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            cleanup_finished.set()

    task = asyncio.create_task(worker())
    await started.wait()
    cancellation = asyncio.create_task(force_cancel_tasks(candidate for candidate in (task,)))
    await asyncio.wait_for(cleanup_started.wait(), timeout=0.1)

    assert not cancellation.done()
    assert not cleanup_finished.is_set()
    release_cleanup.set()
    await asyncio.wait_for(cancellation, timeout=0.1)

    assert cleanup_finished.is_set()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_external_task_name_cannot_collide_with_view_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """An arbitrary public task name does not affect result-source tracking."""
    replacement_sent = asyncio.Event()
    allow_transition = asyncio.Event()
    interaction = _interaction()

    class CollisionModel(_StaticModel):
        def __init__(self, controller: Controller, next_model: ModelBase) -> None:
            self.controller = controller
            self.next_model = next_model

        def before_invoke(self) -> None:
            async def replace_message() -> Result:
                return Result.send_message(
                    ComponentV2Message(items=(TextDisplay('Still waiting'),)),
                    interaction=interaction,
                )

            async def transition() -> Result:
                await allow_transition.wait()
                return Result.next_model(self.next_model, interaction=interaction)

            self.controller.create_external_result(replace_message, name='replacement')
            self.controller.create_external_result(transition, name='inner-view-wait')

    async def send(*_: object) -> _Editable:
        if send_mock.await_count == 2:
            replacement_sent.set()
        return _sent()

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)
    monkeypatch.setattr(util_module, 'send_helper', send_mock)
    controller = Controller(_StaticModel())
    terminal_model = _StaticModel()
    controller.model = CollisionModel(controller, terminal_model)

    invoke = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)
    assert not invoke.done()

    allow_transition.set()
    await asyncio.wait_for(invoke, timeout=0.1)

    assert send_mock.await_count == 3
    assert terminal_model.after_invoked
