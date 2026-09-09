from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import discord.ext.flow.controller as controller_module
import pytest
from discord import Client, Interaction, ui
from discord.abc import Messageable
from discord.ext.flow import Button, Controller, FlowTimeoutError, Message, ModelBase, Result
from discord.ext.flow.controller import _ViewTaskRecord
from discord.ext.flow.util import _Editable
from discord.ext.flow.view import create_view

if TYPE_CHECKING:
    from discord.ext.flow.model import ViewConfig
    from discord.ext.flow.view import _ViewType


def _interaction() -> Interaction[Client]:
    interaction = MagicMock(spec=Interaction)
    interaction.response.is_done.return_value = True
    return interaction


def _messageable() -> Messageable:
    return MagicMock(spec=Messageable)


def _editable() -> _Editable:
    editable = MagicMock(spec=_Editable)
    editable.id = 1
    return editable


def _first_button(view: _ViewType) -> ui.Button[_ViewType]:
    return cast('ui.Button[_ViewType]', next(item for item in view.walk_children() if isinstance(item, ui.Button)))


@pytest.mark.asyncio
async def test_model_error_group_contains_ui_error_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model receives one group for its callback failures and timeout."""
    callback_error = RuntimeError('callback failed')
    received: list[ExceptionGroup[Exception]] = []
    controller_calls: list[ExceptionGroup[Exception]] = []
    sent = asyncio.Event()
    allow_send = asyncio.Event()
    callback_release = asyncio.Event()
    captured_view: _ViewType | None = None

    class Model(ModelBase):
        def view_config(self) -> ViewConfig:
            return {'timeout': None}

        def message(self) -> Message:
            return Message(
                items=(Button(label='First').on(callback=self.fail), Button(label='Second').on(callback=self.fail))
            )

        async def fail(self, _: Interaction) -> Result:
            await callback_release.wait()
            raise callback_error

        def on_error(self, error: ExceptionGroup[Exception]) -> None:
            received.append(error)

    def on_error(error: ExceptionGroup[Exception]) -> None:
        controller_calls.append(error)

    async def send(_: object, __: Message, view: _ViewType, ___: object) -> _Editable:
        nonlocal captured_view
        captured_view = view
        sent.set()
        await allow_send.wait()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    model = Model()
    controller = Controller(model, on_error=on_error)
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    assert captured_view is not None
    view_wait_task = next(record.task for record in controller._tasks.values() if isinstance(record, _ViewTaskRecord))
    registered_tasks = set(controller._tasks)
    buttons = [item for item in captured_view.walk_children() if isinstance(item, ui.Button)]
    await cast('ui.Button[_ViewType]', buttons[0]).callback(_interaction())
    await cast('ui.Button[_ViewType]', buttons[1]).callback(_interaction())
    callback_tasks = [task for task in controller._tasks if task not in registered_tasks]
    callback_release.set()
    await asyncio.gather(*callback_tasks, return_exceptions=True)
    captured_view._dispatch_timeout()  # type: ignore[no-untyped-call]
    assert await view_wait_task is True
    allow_send.set()
    await asyncio.wait_for(invocation, timeout=0.1)

    assert len(received) == 1
    assert received[0].exceptions[:2] == (callback_error, callback_error)
    assert isinstance(received[0].exceptions[2], FlowTimeoutError)
    assert controller_calls == []


@pytest.mark.asyncio
async def test_controller_fallback_groups_external_error_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The controller fallback groups external errors and timeout together."""
    external_error = RuntimeError('external failed')
    received: list[ExceptionGroup[Exception]] = []
    sent = asyncio.Event()
    allow_send = asyncio.Event()
    external_release = asyncio.Event()
    captured_view: _ViewType | None = None

    class Model(ModelBase):
        def __init__(self, controller: Controller | None = None) -> None:
            self.controller = controller

        def view_config(self) -> ViewConfig:
            return {'timeout': None}

        def before_invoke(self) -> None:
            async def fail() -> Result:
                await external_release.wait()
                raise external_error

            assert self.controller is not None
            self.controller.create_external_result(fail)

        def message(self) -> Message:
            return Message(items=(Button().on(callback=lambda _: Result.finish_flow()),))

    def on_error(error: ExceptionGroup[Exception]) -> None:
        received.append(error)

    async def send(_: object, __: Message, view: _ViewType, ___: object) -> _Editable:
        nonlocal captured_view
        captured_view = view
        sent.set()
        await allow_send.wait()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    model = Model()
    controller = Controller(model, on_error=on_error)
    model.controller = controller
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    assert captured_view is not None
    view_wait_task = next(record.task for record in controller._tasks.values() if isinstance(record, _ViewTaskRecord))
    external_release.set()
    external_tasks = [record.task for record in controller._tasks.values() if not isinstance(record, _ViewTaskRecord)]
    await asyncio.gather(*external_tasks, return_exceptions=True)
    captured_view._dispatch_timeout()  # type: ignore[no-untyped-call]
    assert await view_wait_task is True
    allow_send.set()
    await asyncio.wait_for(invocation, timeout=0.1)

    assert len(received) == 1
    assert received[0].exceptions[0] is external_error
    assert isinstance(received[0].exceptions[1], FlowTimeoutError)


@pytest.mark.asyncio
async def test_normal_transition_is_applied_after_timeout_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed transition still wins after the same view reports timeout."""
    received: list[ExceptionGroup[Exception]] = []
    sent_messages: list[Message] = []
    sent = asyncio.Event()

    class NextModel(ModelBase):
        def message(self) -> Message:
            return Message(content='Next')

    next_model = NextModel()
    initial_message = Message(items=(Button().on(callback=lambda _: Result.next_model(next_model)),))

    class Model(ModelBase):
        def view_config(self) -> ViewConfig:
            return {'timeout': None}

        def message(self) -> Message:
            return initial_message

        def on_error(self, error: ExceptionGroup[Exception]) -> None:
            received.append(error)

    async def send(_: object, message: Message, __: object, ___: object) -> _Editable:
        sent_messages.append(message)
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(Model(), on_error=lambda error: pytest.fail(f'unexpected fallback: {error}'))
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    await _first_button(active.view).callback(_interaction())
    active.view._dispatch_timeout()  # type: ignore[no-untyped-call]
    await asyncio.wait_for(invocation, timeout=0.1)

    assert sent_messages == [initial_message, next_model.message()]
    assert len(received) == 1
    assert len(received[0].exceptions) == 1
    assert isinstance(received[0].exceptions[0], FlowTimeoutError)


@pytest.mark.asyncio
async def test_model_handler_failure_does_not_fallback_but_other_group_is_notified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model handler failure does not trigger controller fallback."""
    callback_error = RuntimeError('callback failed')
    handler_error = ValueError('model handler failed')
    external_error = OSError('external failed')
    model_group: list[ExceptionGroup[Exception]] = []
    controller_group: list[ExceptionGroup[Exception]] = []
    sent = asyncio.Event()

    class Model(ModelBase):
        def __init__(self, controller: Controller | None = None) -> None:
            self.controller = controller

        def before_invoke(self) -> None:
            async def fail() -> Result:
                raise external_error

            assert self.controller is not None
            self.controller.create_external_result(fail)

        def message(self) -> Message:
            return Message(items=(Button().on(callback=self.fail_callback),))

        def fail_callback(self, _: Interaction) -> Result:
            raise callback_error

        def on_error(self, error: ExceptionGroup[Exception]) -> None:
            model_group.append(error)
            raise handler_error

    def on_error(error: ExceptionGroup[Exception]) -> None:
        controller_group.append(error)

    async def send(*_: object) -> _Editable:
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    model = Model()
    controller = Controller(model, on_error=on_error)
    model.controller = controller
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    await _first_button(active.view).callback(_interaction())
    with pytest.raises(ExceptionGroup) as raised:
        await asyncio.wait_for(invocation, timeout=0.1)

    assert len(model_group) == 1
    assert model_group[0].exceptions == (callback_error,)
    assert len(controller_group) == 1
    assert controller_group[0].exceptions == (external_error,)
    assert handler_error in raised.value.exceptions
    assert model_group[0] in raised.value.exceptions


@pytest.mark.asyncio
async def test_timeout_hook_cannot_restart_expired_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timeout handler cannot keep an expired view waiting."""
    sent = asyncio.Event()
    timeout_calls = 0

    class Model(ModelBase):
        def __init__(self, controller: Controller | None = None) -> None:
            self.controller = controller

        def view_config(self) -> ViewConfig:
            return {'timeout': None}

        def message(self) -> Message:
            return Message(items=(Button().on(callback=lambda _: Result.finish_flow()),))

        def on_error(self, error: ExceptionGroup[Exception]) -> None:
            nonlocal timeout_calls
            if any(isinstance(item, FlowTimeoutError) for item in error.exceptions):
                timeout_calls += 1

                async def pending() -> Result:
                    await asyncio.Event().wait()
                    return Result.finish_flow()

                assert self.controller is not None
                self.controller.create_external_result(pending)

    async def send(*_: object) -> _Editable:
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    model = Model()
    controller = Controller(model)
    model.controller = controller
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    active.view._dispatch_timeout()  # type: ignore[no-untyped-call]
    await asyncio.wait_for(invocation, timeout=0.1)

    assert timeout_calls == 1
    assert controller._active_message is None


@pytest.mark.asyncio
async def test_explicit_view_stop_does_not_notify_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopping a view explicitly does not create a timeout error."""
    sent = asyncio.Event()
    received: list[ExceptionGroup[Exception]] = []

    class Model(ModelBase):
        def message(self) -> Message:
            return Message(items=(Button().on(callback=lambda _: Result.finish_flow()),))

        def on_error(self, error: ExceptionGroup[Exception]) -> None:
            received.append(error)

    async def send(*_: object) -> _Editable:
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(Model())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    active.view.stop()
    await asyncio.wait_for(invocation, timeout=0.1)

    assert received == []


@pytest.mark.asyncio
async def test_retiring_view_reclaims_timeout_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timeout discovered while retiring a view is still notified once."""
    received: list[ExceptionGroup[Exception]] = []
    sent_messages: list[Message] = []

    class Model(ModelBase):
        def message(self) -> Message:
            return Message(items=(Button().on(callback=lambda _: Result.finish_flow()),))

        def on_error(self, error: ExceptionGroup[Exception]) -> Result:
            received.append(error)
            return Result.send_message(Message(content='should not be sent'))

    async def send(_: object, message: Message, __: object, ___: object) -> _Editable:
        sent_messages.append(message)
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    model = Model()
    controller = Controller(model)
    message = model.message()
    view = create_view({}, message.items or (), controller)
    await controller._send_and_activate_message(_messageable(), message, view, None)
    view._dispatch_timeout()  # type: ignore[no-untyped-call]
    await controller._retire_view(view)

    assert len(received) == 1
    assert len(received[0].exceptions) == 1
    assert isinstance(received[0].exceptions[0], FlowTimeoutError)
    assert len(sent_messages) == 1
    assert not controller._tasks


@pytest.mark.asyncio
async def test_model_error_handler_result_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """A synchronous model error handler can replace a failed callback's view."""
    callback_error = RuntimeError('callback failed')
    received: list[ExceptionGroup[Exception]] = []
    sent_messages: list[Message] = []
    sent = asyncio.Event()

    class Model(ModelBase):
        def message(self) -> Message:
            return Message(content='initial', items=(Button(label='Fail').on(callback=self.fail),))

        def fail(self, _: Interaction) -> Result:
            raise callback_error

        def on_error(self, error: ExceptionGroup[Exception]) -> Result:
            received.append(error)
            return Result.send_message(
                Message(
                    content='recovered', items=(Button(label='Finish').on(callback=lambda _: Result.finish_flow()),)
                )
            )

    async def send(_: object, message: Message, __: object, ___: object) -> _Editable:
        sent_messages.append(message)
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(Model(), on_error=lambda error: pytest.fail(f'unexpected fallback: {error}'))
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    sent.clear()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    await _first_button(active.view).callback(_interaction())
    await asyncio.wait_for(sent.wait(), timeout=0.1)
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    await _first_button(active.view).callback(_interaction())
    await asyncio.wait_for(invocation, timeout=0.1)

    assert [message.content for message in sent_messages] == ['initial', 'recovered']
    assert len(received) == 1
    assert received[0].exceptions == (callback_error,)


@pytest.mark.asyncio
async def test_async_model_error_handler_result_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """An asynchronous model error handler can replace a failed callback's view."""
    callback_error = RuntimeError('callback failed')
    sent_messages: list[Message] = []
    sent = asyncio.Event()

    class Model(ModelBase):
        def message(self) -> Message:
            return Message(content='initial', items=(Button(label='Fail').on(callback=self.fail),))

        def fail(self, _: Interaction) -> Result:
            raise callback_error

        async def on_error(self, error: ExceptionGroup[Exception]) -> Result:
            assert error.exceptions == (callback_error,)
            return Result.send_message(
                Message(
                    content='recovered', items=(Button(label='Finish').on(callback=lambda _: Result.finish_flow()),)
                )
            )

    async def send(_: object, message: Message, __: object, ___: object) -> _Editable:
        sent_messages.append(message)
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(Model(), on_error=lambda error: pytest.fail(f'unexpected fallback: {error}'))
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    sent.clear()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    await _first_button(active.view).callback(_interaction())
    await asyncio.wait_for(sent.wait(), timeout=0.1)
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    await _first_button(active.view).callback(_interaction())
    await asyncio.wait_for(invocation, timeout=0.1)

    assert [message.content for message in sent_messages] == ['initial', 'recovered']


@pytest.mark.asyncio
async def test_controller_error_handler_result_transitions_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A synchronous controller error handler can transition after an external error."""
    external_error = RuntimeError('external failed')
    sent_messages: list[Message] = []

    class NextModel(ModelBase):
        def message(self) -> Message:
            return Message(content='next')

    next_model = NextModel()

    class Model(ModelBase):
        def __init__(self, controller: Controller | None = None) -> None:
            self.controller = controller

        def before_invoke(self) -> None:
            async def fail() -> Result:
                raise external_error

            assert self.controller is not None
            self.controller.create_external_result(fail)

        def message(self) -> Message:
            return Message(content='initial', items=(Button().on(callback=lambda _: Result.finish_flow()),))

    def on_error(error: ExceptionGroup[Exception]) -> Result:
        assert error.exceptions == (external_error,)
        return Result.next_model(next_model)

    async def send(_: object, message: Message, __: object, ___: object) -> _Editable:
        sent_messages.append(message)
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    model = Model()
    controller = Controller(model, on_error=on_error)
    model.controller = controller
    await asyncio.wait_for(controller.invoke(_messageable()), timeout=0.1)

    assert [message.content for message in sent_messages] == ['initial', 'next']


@pytest.mark.asyncio
async def test_async_controller_error_handler_result_transitions_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """An asynchronous controller error handler can transition after an external error."""
    external_error = RuntimeError('external failed')
    sent_messages: list[Message] = []

    class NextModel(ModelBase):
        def message(self) -> Message:
            return Message(content='next')

    next_model = NextModel()

    class Model(ModelBase):
        def __init__(self, controller: Controller | None = None) -> None:
            self.controller = controller

        def before_invoke(self) -> None:
            async def fail() -> Result:
                raise external_error

            assert self.controller is not None
            self.controller.create_external_result(fail)

        def message(self) -> Message:
            return Message(content='initial', items=(Button().on(callback=lambda _: Result.finish_flow()),))

    async def on_error(error: ExceptionGroup[Exception]) -> Result:
        assert error.exceptions == (external_error,)
        return Result.next_model(next_model)

    async def send(_: object, message: Message, __: object, ___: object) -> _Editable:
        sent_messages.append(message)
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    model = Model()
    controller = Controller(model, on_error=on_error)
    model.controller = controller
    await asyncio.wait_for(controller.invoke(_messageable()), timeout=0.1)

    assert [message.content for message in sent_messages] == ['initial', 'next']


@pytest.mark.asyncio
async def test_timeout_error_handler_result_replaces_expired_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timeout handler can recover by sending a replacement view."""
    sent_messages: list[Message] = []
    sent = asyncio.Event()

    class Model(ModelBase):
        def view_config(self) -> ViewConfig:
            return {'timeout': None}

        def message(self) -> Message:
            return Message(content='initial', items=(Button().on(callback=lambda _: Result.finish_flow()),))

        def on_error(self, error: ExceptionGroup[Exception]) -> Result:
            assert any(isinstance(item, FlowTimeoutError) for item in error.exceptions)
            return Result.send_message(
                Message(content='recovered', items=(Button().on(callback=lambda _: Result.finish_flow()),))
            )

    async def send(_: object, message: Message, __: object, ___: object) -> _Editable:
        sent_messages.append(message)
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(Model())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    sent.clear()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    active.view._dispatch_timeout()  # type: ignore[no-untyped-call]
    await asyncio.wait_for(sent.wait(), timeout=0.1)
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    await _first_button(active.view).callback(_interaction())
    await asyncio.wait_for(invocation, timeout=0.1)

    assert [message.content for message in sent_messages] == ['initial', 'recovered']


@pytest.mark.asyncio
async def test_timeout_error_handler_result_transitions_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timeout handler can recover by transitioning to another model."""
    sent_messages: list[Message] = []
    sent = asyncio.Event()

    class NextModel(ModelBase):
        def message(self) -> Message:
            return Message(content='next')

    next_model = NextModel()

    class Model(ModelBase):
        def view_config(self) -> ViewConfig:
            return {'timeout': None}

        def message(self) -> Message:
            return Message(content='initial', items=(Button().on(callback=lambda _: Result.finish_flow()),))

        def on_error(self, error: ExceptionGroup[Exception]) -> Result:
            assert any(isinstance(item, FlowTimeoutError) for item in error.exceptions)
            return Result.next_model(next_model)

    async def send(_: object, message: Message, __: object, ___: object) -> _Editable:
        sent_messages.append(message)
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(Model())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    active.view._dispatch_timeout()  # type: ignore[no-untyped-call]
    await asyncio.wait_for(invocation, timeout=0.1)

    assert [message.content for message in sent_messages] == ['initial', 'next']


@pytest.mark.asyncio
async def test_timeout_error_handler_continue_finishes_expired_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """Continuing from a timeout does not revive the expired view."""
    sent = asyncio.Event()

    class Model(ModelBase):
        def view_config(self) -> ViewConfig:
            return {'timeout': None}

        def message(self) -> Message:
            return Message(items=(Button().on(callback=lambda _: Result.finish_flow()),))

        def on_error(self, error: ExceptionGroup[Exception]) -> Result:
            assert any(isinstance(item, FlowTimeoutError) for item in error.exceptions)
            return Result.continue_flow()

    async def send(*_: object) -> _Editable:
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(Model())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    active.view._dispatch_timeout()  # type: ignore[no-untyped-call]
    await asyncio.wait_for(invocation, timeout=0.1)

    assert controller._active_message is None


@pytest.mark.asyncio
async def test_normal_replacement_is_followed_by_error_handler_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A handler result is applied to the view created by an earlier normal result."""
    callback_error = RuntimeError('callback failed')
    sent_messages: list[Message] = []
    sent = asyncio.Event()

    first_replacement = Message(content='first', items=(Button().on(callback=lambda _: Result.finish_flow()),))
    second_replacement = Message(content='second', items=(Button().on(callback=lambda _: Result.finish_flow()),))

    class Model(ModelBase):
        def message(self) -> Message:
            return Message(
                content='initial',
                items=(
                    Button(label='Replace').on(callback=self.replace),
                    Button(label='Fail').on(callback=self.fail),
                ),
            )

        def replace(self, _: Interaction) -> Result:
            return Result.send_message(first_replacement)

        def fail(self, _: Interaction) -> Result:
            raise callback_error

        def on_error(self, error: ExceptionGroup[Exception]) -> Result:
            assert error.exceptions == (callback_error,)
            return Result.send_message(second_replacement)

    async def send(_: object, message: Message, __: object, ___: object) -> _Editable:
        sent_messages.append(message)
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(Model())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    buttons = [item for item in active.view.walk_children() if isinstance(item, ui.Button)]
    await cast('ui.Button[_ViewType]', buttons[0]).callback(_interaction())
    await cast('ui.Button[_ViewType]', buttons[1]).callback(_interaction())
    await asyncio.wait_for(_wait_for_message_count(sent, sent_messages, 3), timeout=0.1)
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    await _first_button(active.view).callback(_interaction())
    await asyncio.wait_for(invocation, timeout=0.1)

    assert [message.content for message in sent_messages] == ['initial', 'first', 'second']


@pytest.mark.asyncio
async def test_normal_transition_discards_error_handler_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal transition prevents a later error handler result from overriding it."""
    callback_error = RuntimeError('callback failed')
    sent_messages: list[Message] = []
    sent = asyncio.Event()

    class NextModel(ModelBase):
        def message(self) -> Message:
            return Message(content='next')

    next_model = NextModel()

    class Model(ModelBase):
        def message(self) -> Message:
            return Message(
                content='initial',
                items=(
                    Button(label='Transition').on(callback=lambda _: Result.next_model(next_model)),
                    Button(label='Fail').on(callback=self.fail),
                ),
            )

        def fail(self, _: Interaction) -> Result:
            raise callback_error

        def on_error(self, error: ExceptionGroup[Exception]) -> Result:
            assert error.exceptions == (callback_error,)
            return Result.send_message(Message(content='unexpected'))

    async def send(_: object, message: Message, __: object, ___: object) -> _Editable:
        sent_messages.append(message)
        sent.set()
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(Model())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await sent.wait()
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    buttons = [item for item in active.view.walk_children() if isinstance(item, ui.Button)]
    await cast('ui.Button[_ViewType]', buttons[0]).callback(_interaction())
    await cast('ui.Button[_ViewType]', buttons[1]).callback(_interaction())
    await asyncio.wait_for(invocation, timeout=0.1)

    assert [message.content for message in sent_messages] == ['initial', 'next']


async def _wait_for_message_count(sent: asyncio.Event, messages: list[Message], count: int) -> None:
    while len(messages) < count:
        sent.clear()
        await sent.wait()


@pytest.mark.asyncio
async def test_error_handler_invalid_result_is_reported_as_type_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-Result error handler return is treated as a handler failure."""
    callback_error = RuntimeError('callback failed')

    class Model(ModelBase):
        def message(self) -> Message:
            return Message(items=(Button().on(callback=self.fail),))

        def fail(self, _: Interaction) -> Result:
            raise callback_error

        def on_error(self, _error: ExceptionGroup[Exception]) -> object:
            return object()

    async def send(*_: object) -> _Editable:
        return _editable()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(Model())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.sleep(0)
    active = controller._active_message
    assert active is not None
    assert active.view is not None
    await _first_button(active.view).callback(_interaction())
    with pytest.raises(ExceptionGroup) as raised:
        await asyncio.wait_for(invocation, timeout=0.1)

    assert any(isinstance(error, TypeError) for error in raised.value.exceptions)
