from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import discord.ext.flow.controller as controller_module
import discord.ext.flow.util as util_module
import pytest
from discord import Client, Interaction
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
from discord.ext.flow.controller import Controller, _ResultWaiter
from discord.ext.flow.modal import _InnerModal
from discord.ext.flow.util import exec_result, force_cancel_tasks
from discord.ext.flow.view import create_view

if TYPE_CHECKING:
    from discord.abc import Messageable
    from discord.ext.flow import ExternalResultTask
    from discord.ext.flow.util import _Editable
    from discord.ext.flow.view import _ViewType


def _finish(_: Interaction[Client]) -> Result:
    return Result.finish_flow()


class _InteractiveModel(ModelBase):
    def message(self) -> LegacyMessage:
        return LegacyMessage(items=(Button(callback=_finish),))


class _StaticModel(ModelBase):
    after_invoked = False

    def message(self) -> ComponentV2Message:
        return ComponentV2Message(items=(TextDisplay('Finished'),))

    def after_invoke(self) -> None:
        self.after_invoked = True


@pytest.mark.parametrize(
    'message',
    [
        LegacyMessage(items=(Button(callback=_finish, disabled=True),)),
        ComponentV2Message(items=(ActionRow(items=(Button(callback=_finish, disabled=True),)),)),
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

    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(return_value=object()))
    model = Model()

    await asyncio.wait_for(Controller(model).invoke(cast('Messageable', object())), timeout=0.1)

    assert model.after_invoked


@pytest.mark.asyncio
async def test_disabled_only_replacement_stops_active_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing active controls with disabled controls completes the view."""
    controller = Controller(_InteractiveModel())
    view = create_view({}, (Button(callback=_finish),), controller)
    interaction = cast('Interaction[Client]', object())
    monkeypatch.setattr(util_module, 'send_helper', AsyncMock(return_value=object()))

    await exec_result(
        view,
        Result.send_message(
            LegacyMessage(items=(Button(callback=_finish, disabled=True),)),
            interaction=interaction,
        ),
        cast('_Editable', object()),
    )

    assert view.is_finished()


@pytest.mark.asyncio
async def test_reused_controller_clears_stale_external_task_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal invocation cannot leave a completed task-added waiter for the next invocation."""
    send = AsyncMock(return_value=object())
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
    await controller.invoke(cast('Messageable', object()))
    assert controller._external_task_event.is_set()

    controller.model = _InteractiveModel()
    invocation = asyncio.create_task(controller.invoke(cast('Messageable', object())))
    for _ in range(3):
        await asyncio.sleep(0)

    assert not invocation.done()
    assert not controller._external_task_event.is_set()
    view = send.await_args_list[-1].args[2]
    await view._set_result(Result.finish_flow(), cast('Interaction[Client]', object()))
    await asyncio.wait_for(invocation, timeout=0.1)


@pytest.mark.asyncio
async def test_unprocessed_persistent_results_continue_into_next_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model transition preserves other completed persistent results in the same batch."""
    interaction = cast('Interaction[Client]', object())
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

    send = AsyncMock(return_value=object())
    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(_StaticModel())
    controller.model = InitialModel(controller)

    await asyncio.wait_for(controller.invoke(cast('Messageable', object())), timeout=0.1)

    assert send.await_count == 3
    assert first_model.after_invoked
    assert second_model.after_invoked


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
async def test_view_timeout_completes_controller_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """A discord.py view timeout also completes the flow's result wait."""
    sent = asyncio.Event()
    captured_view: _ViewType | None = None

    async def send(_: object, __: object, view: _ViewType, ___: object) -> object:
        nonlocal captured_view
        captured_view = view
        sent.set()
        return object()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    invoke = asyncio.create_task(Controller(_InteractiveModel()).invoke(cast('Messageable', object())))
    await asyncio.wait_for(sent.wait(), timeout=0.1)

    assert captured_view is not None
    captured_view._dispatch_timeout()  # type: ignore[no-untyped-call]
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
                (TextInput(label='Value'),),
                lambda _interaction, _values: Result.finish_flow(),
            )
            self.controller.create_external_result(self.modal._wait, name='modal-wait')
            asyncio.get_running_loop().call_soon(self.modal._dispatch_timeout)  # type: ignore[no-untyped-call]

    monkeypatch.setattr(controller_module, 'send_helper', AsyncMock(return_value=object()))
    controller = Controller(_StaticModel())
    model = ModalModel(controller)
    controller.model = model

    await asyncio.wait_for(controller.invoke(cast('Messageable', object())), timeout=0.1)

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

    async def send(_: object, __: object, view: _ViewType, ___: object) -> object:
        nonlocal captured_view
        captured_view = view
        sent.set()
        return object()

    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(_InteractiveModel())
    model = Model(controller)
    controller.model = model
    invoke = asyncio.create_task(controller.invoke(cast('Messageable', object())))
    await asyncio.wait_for(sent.wait(), timeout=0.1)

    model.task.cancel()
    await asyncio.sleep(0)
    assert captured_view is not None
    await captured_view._set_result(Result.finish_flow(), cast('Interaction[Client]', object()))
    await asyncio.wait_for(invoke, timeout=0.1)

    assert model.task.task.cancelled()


@pytest.mark.asyncio
async def test_cancelled_final_external_result_releases_static_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale view waiter cannot keep a static replacement alive after cancellation."""
    replacement_sent = asyncio.Event()
    never_finishes = asyncio.Event()
    interaction = cast('Interaction[Client]', object())

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

    async def send(*_: object) -> object:
        if send_mock.await_count == 2:
            replacement_sent.set()
        return object()

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)
    monkeypatch.setattr(util_module, 'send_helper', send_mock)
    controller = Controller(_InteractiveModel())
    model = Model(controller)
    controller.model = model

    invoke = asyncio.create_task(controller.invoke(cast('Messageable', object())))
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
    interaction = cast('Interaction[Client]', object())

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

    async def send(*_: object) -> object:
        if send_mock.await_count == 2:
            replacement_sent.set()
        return object()

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)
    monkeypatch.setattr(util_module, 'send_helper', send_mock)
    controller = Controller(_StaticModel())
    terminal_model = _StaticModel()
    controller.model = CollisionModel(controller, terminal_model)

    invoke = asyncio.create_task(controller.invoke(cast('Messageable', object())))
    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)
    assert not invoke.done()

    allow_transition.set()
    await asyncio.wait_for(invoke, timeout=0.1)

    assert send_mock.await_count == 3
    assert terminal_model.after_invoked
