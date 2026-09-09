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
from discord.ext.flow.controller import (
    Controller,
    _ResultAction,
)
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
    replacement_message = LegacyMessage(items=(Button(disabled=True).on(callback=_finish),))

    class Model(ModelBase):
        def message(self) -> LegacyMessage:
            return LegacyMessage(items=(Button().on(callback=lambda _: Result.send_message(replacement_message)),))

    controller = Controller(Model())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(sent.wait(), timeout=0.1)
    view = send_mock.await_args_list[0].args[2]
    interaction = _interaction()

    await _first_button(view).callback(interaction)
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
            def replace(_: Interaction) -> Result:
                return Result.send_message(replacement)

            callback = replace if source == 'callback' else _finish
            return ComponentV2Message(
                items=(ActionRow(items=(Button(label='Initial').on(callback=callback),)),),
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
        await _first_button(initial_view).callback(interaction)
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

    await _first_button(replacement_view).callback(interaction)
    await asyncio.wait_for(invocation, timeout=0.1)

    assert events == expected_events
    assert replacement_button.disabled is replacement_disable_items
    assert replacement_edit_mock.await_count == int(replacement_disable_items)


@pytest.mark.asyncio
async def test_edit_original_replacement_overwrites_without_disabling_previous_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Editing the active message replaces its controls instead of finalizing the old representation first."""
    replacement_sent = asyncio.Event()
    initial_sent = asyncio.Event()
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
        def replace(self, _: Interaction) -> Result:
            return Result.send_message(replacement)

        def message(self) -> LegacyMessage:
            return LegacyMessage(items=(Button(label='Initial').on(callback=self.replace),), disable_items=True)

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
    await _first_button(initial_view).callback(interaction)
    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)

    initial_edit_mock.assert_not_awaited()
    assert send_mock.await_args_list[1].args[3] is initial_edit
    replacement_view = send_mock.await_args_list[1].args[2]
    await _first_button(replacement_view).callback(interaction)
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
    initial_view = create_view(view_config, initial_config.items or (), controller, controller.model)
    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(return_value=initial_message))
    await controller._send_and_activate_message(_messageable(), initial_config, initial_view, None)

    monkeypatch.setattr(util_module, 'Interaction', FakeInteraction)
    monkeypatch.setattr(controller_module, 'send_helper', util_module.send_helper)
    interaction = cast('Interaction[Client]', FakeInteraction())
    replacement = LegacyMessage(
        items=(Button(label='Replacement').on(callback=_finish),),
        edit_original=True,
    )

    outcome = await controller._apply_result(
        initial_view,
        Result.send_message(replacement, interaction=interaction),
    )

    assert outcome.action is _ResultAction.REPLACE_VIEW
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
        def callback(self, _: Interaction) -> Result:
            return result

        def message(self) -> LegacyMessage:
            return LegacyMessage(items=(Button(label='Initial').on(callback=self.callback),), disable_items=True)

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
    await _first_button(initial_view).callback(interaction)

    with pytest.raises(RuntimeError, match='replacement failed'):
        await asyncio.wait_for(invocation, timeout=0.1)

    assert events == ['send-0', 'send-1', 'disable-initial']
    assert _first_button(initial_view).disabled
    initial_edit_mock.assert_awaited_once_with(view=initial_view)


@pytest.mark.asyncio
async def test_invoke_groups_flow_and_cleanup_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cleanup failure does not hide the exception that caused invocation to exit."""
    sent = asyncio.Event()
    flow_failure = RuntimeError('flow failed')
    cleanup_failure = RuntimeError('cleanup failed')
    initial_edit = _editable()
    initial_edit_mock = cast('AsyncMock', initial_edit.edit)
    initial_edit_mock.side_effect = cleanup_failure

    class FailingModel(ModelBase):
        def message(self) -> LegacyMessage:
            return LegacyMessage(items=(Button(label='Initial').on(callback=_finish),), disable_items=True)

        def after_invoke(self) -> None:
            raise flow_failure

    async def send(_: object, __: object, ___: _ViewType, ____: object) -> _Editable:
        sent.set()
        return initial_edit

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(FailingModel())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(sent.wait(), timeout=0.1)
    view = controller._active_message
    assert view is not None
    assert view.view is not None
    await _first_button(view.view).callback(_interaction())

    with pytest.raises(ExceptionGroup) as raised:
        await asyncio.wait_for(invocation, timeout=0.1)

    assert raised.value.exceptions == (flow_failure, cleanup_failure)
    initial_edit_mock.assert_awaited_once_with(view=view.view)


@pytest.mark.asyncio
async def test_invoke_preserves_cleanup_failure_without_flow_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cleanup failure remains the only exception when invocation finishes normally."""
    sent = asyncio.Event()
    cleanup_failure = RuntimeError('cleanup failed')
    initial_edit = _editable()
    initial_edit_mock = cast('AsyncMock', initial_edit.edit)
    initial_edit_mock.side_effect = cleanup_failure
    captured_view: _ViewType | None = None

    class Model(ModelBase):
        def message(self) -> LegacyMessage:
            return LegacyMessage(items=(Button(label='Initial').on(callback=_finish),), disable_items=True)

    async def send(_: object, __: object, view: _ViewType, ____: object) -> _Editable:
        nonlocal captured_view
        captured_view = view
        sent.set()
        return initial_edit

    monkeypatch.setattr(controller_module, 'send_helper', send)
    invocation = asyncio.create_task(Controller(Model()).invoke(_messageable()))
    await asyncio.wait_for(sent.wait(), timeout=0.1)
    assert captured_view is not None
    view = captured_view
    await _first_button(view).callback(_interaction())

    with pytest.raises(RuntimeError, match='cleanup failed'):
        await asyncio.wait_for(invocation, timeout=0.1)

    initial_edit_mock.assert_awaited_once_with(view=view)


@pytest.mark.asyncio
async def test_invoke_groups_cancellation_and_cleanup_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cleanup failure does not hide cancellation of the invocation."""
    sent = asyncio.Event()
    cleanup_failure = RuntimeError('cleanup failed')
    initial_edit = _editable()
    initial_edit_mock = cast('AsyncMock', initial_edit.edit)
    initial_edit_mock.side_effect = cleanup_failure
    captured_view: _ViewType | None = None

    class Model(ModelBase):
        def message(self) -> LegacyMessage:
            return LegacyMessage(items=(Button(label='Initial').on(callback=_finish),), disable_items=True)

    async def send(_: object, __: object, view: _ViewType, ____: object) -> _Editable:
        nonlocal captured_view
        captured_view = view
        sent.set()
        return initial_edit

    monkeypatch.setattr(controller_module, 'send_helper', send)
    invocation = asyncio.create_task(Controller(Model()).invoke(_messageable()))
    await asyncio.wait_for(sent.wait(), timeout=0.1)
    invocation.cancel()

    with pytest.raises(BaseExceptionGroup) as raised:
        await asyncio.wait_for(invocation, timeout=0.1)

    assert captured_view is not None
    assert isinstance(raised.value, BaseExceptionGroup)
    assert isinstance(raised.value.exceptions[0], asyncio.CancelledError)
    assert raised.value.exceptions[1] is cleanup_failure
    initial_edit_mock.assert_awaited_once_with(view=captured_view)


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
    await _first_button(view).callback(_interaction())
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
    next_message = LegacyMessage(content='Next model', items=())
    external_message = LegacyMessage(content='External replacement', items=())
    ui_ready = asyncio.Event()
    ui_release = asyncio.Event()
    ui_completed = asyncio.Event()
    external_completed = asyncio.Event()
    external_release = asyncio.Event()

    class NextModel(ModelBase):
        def message(self) -> LegacyMessage:
            return next_message

    next_model = NextModel()

    async def transition(_: Interaction) -> Result:
        ui_ready.set()
        await ui_release.wait()
        try:
            return Result.next_model(next_model, interaction=interaction)
        finally:
            ui_completed.set()

    initial_message = LegacyMessage(items=(Button(label='Initial').on(callback=transition),))

    class InitialModel(ModelBase):
        def __init__(self, controller: Controller) -> None:
            self.controller = controller
            self.external_task: ExternalResultTask | None = None

        def before_invoke(self) -> None:
            async def replace_message() -> Result:
                await external_release.wait()
                try:
                    return Result.send_message(external_message, interaction=interaction)
                finally:
                    external_completed.set()

            self.external_task = self.controller.create_external_result(replace_message)

        def message(self) -> LegacyMessage:
            return initial_message

    sent_messages: list[LegacyMessage] = []

    async def send(_: object, message: LegacyMessage, view: _ViewType, __: object) -> _Editable:
        sent_messages.append(message)
        if message is initial_message:
            await _first_button(view).callback(interaction)
            await ui_ready.wait()
            external_release.set()
            ui_release.set()
            await external_completed.wait()
            await ui_completed.wait()
        return _sent()

    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(side_effect=send))
    controller = Controller(_StaticModel())
    controller.model = InitialModel(controller)
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(invocation, timeout=0.1)

    assert sent_messages == [initial_message, next_message, external_message]


@pytest.mark.asyncio
async def test_view_continue_precedes_simultaneous_external_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement remains interactive after a simultaneous component continue result."""
    interaction = _interaction()
    ui_ready = asyncio.Event()
    ui_release = asyncio.Event()
    ui_completed = asyncio.Event()
    external_release = asyncio.Event()
    external_completed = asyncio.Event()
    replacement_sent = asyncio.Event()

    async def continue_flow(_: Interaction) -> Result:
        ui_ready.set()
        await ui_release.wait()
        try:
            return Result.continue_flow()
        finally:
            ui_completed.set()

    initial_message = LegacyMessage(items=(Button(label='Initial').on(callback=continue_flow),))
    replacement_message = LegacyMessage(items=(Button(label='Replacement').on(callback=_finish),))

    class InitialModel(ModelBase):
        def __init__(self, controller: Controller) -> None:
            self.controller = controller
            self.external_task: ExternalResultTask | None = None

        def before_invoke(self) -> None:
            async def replace_message() -> Result:
                await external_release.wait()
                try:
                    return Result.send_message(replacement_message, interaction=interaction)
                finally:
                    external_completed.set()

            self.external_task = self.controller.create_external_result(replace_message)

        def message(self) -> LegacyMessage:
            return initial_message

    sent_views: list[_ViewType] = []

    async def send(_: object, message: LegacyMessage, view: _ViewType, __: object) -> _Editable:
        sent_views.append(view)
        if message is initial_message:
            await _first_button(view).callback(interaction)
            await ui_ready.wait()
            external_release.set()
            ui_release.set()
            await external_completed.wait()
            await ui_completed.wait()
        else:
            replacement_sent.set()
        return _sent()

    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(side_effect=send))
    controller = Controller(_StaticModel())
    controller.model = InitialModel(controller)
    invocation = asyncio.create_task(controller.invoke(_messageable()))

    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)

    replacement_view = sent_views[-1]
    assert not invocation.done()
    assert not replacement_view.is_finished()

    await _first_button(replacement_view).callback(interaction)
    await asyncio.wait_for(invocation, timeout=0.1)


@pytest.mark.asyncio
async def test_completed_external_results_continue_after_view_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement does not discard another completed persistent result from the same batch."""
    initial_sent = asyncio.Event()
    second_replacement_sent = asyncio.Event()
    interaction = _interaction()
    initial = LegacyMessage(items=(Button(label='Initial').on(callback=_finish),))
    first = LegacyMessage(items=(Button(label='First').on(callback=_finish),))
    second = LegacyMessage(items=(Button(label='Second').on(callback=_finish),))
    send_messages: list[LegacyMessage] = []

    async def send(_: object, message: LegacyMessage, __: _ViewType | None, ___: object) -> _Editable:
        send_messages.append(message)
        if len(send_messages) == 1:
            initial_sent.set()
        elif len(send_messages) == 3:
            second_replacement_sent.set()
        return _sent()

    class Model(ModelBase):
        def __init__(self) -> None:
            self.controller: Controller | None = None

        def before_invoke(self) -> None:
            assert self.controller is not None

            async def first_result() -> Result:
                return Result.send_message(first, interaction=interaction)

            async def second_result() -> Result:
                return Result.send_message(second, interaction=interaction)

            self.controller.create_external_result(first_result)
            self.controller.create_external_result(second_result)

        def message(self) -> LegacyMessage:
            return initial

    monkeypatch.setattr(controller_module, 'send_helper', send)
    model = Model()
    controller = Controller(model)
    model.controller = controller
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(initial_sent.wait(), timeout=0.1)
    await asyncio.wait_for(second_replacement_sent.wait(), timeout=0.1)

    assert send_messages == [initial, first, second]
    assert not invocation.done()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    await _first_button(active.view).callback(interaction)
    await asyncio.wait_for(invocation, timeout=0.1)


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
    await _first_button(captured_view).callback(_interaction())
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
