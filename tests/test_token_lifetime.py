from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from discord import (
    Client,
    Intents,
    Interaction,
    InteractionType,
    Message as DiscordMessage,
    NotFound,
    PartialMessage,
    ui,
)
from discord.ext.flow import (
    ActionRow,
    Button,
    ComponentV2Message,
    Controller,
    Message,
    ModalConfig,
    ModelBase,
    Result,
    run_flow,
)
from discord.ext.flow.display import FlowDisplay
from discord.ext.flow.modal import _InnerModal
from discord.utils import time_snowflake, utcnow
from discord.webhook.async_ import async_context

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from discord.http import MultipartParameters
    from discord.types.interactions import Interaction as InteractionPayload
    from discord.types.message import Message as MessagePayload


class _API:
    """Emulate HTTP responses while exercising the actual discord.py message and interaction classes."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.channel = client.get_partial_messageable(100)
        self.messages: dict[int, dict[str, Any]] = {}
        self.interactions: dict[str, Interaction] = {}
        self.originals: dict[str, int] = {}
        self.calls: list[tuple[str, int]] = []
        self.fail: set[str] = set()

    def message(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Store a new Discord message payload."""
        message_id = 1000 + len(self.messages)
        data: dict[str, Any] = {
            'id': str(message_id),
            'channel_id': '100',
            'type': 0,
            'content': '',
            'author': {'id': '123', 'username': 'bot', 'discriminator': '0', 'avatar': None, 'bot': True},
            'attachments': [],
            'embeds': [],
            'components': [],
            'flags': 0,
            'edited_timestamp': None,
            'pinned': False,
            'mention_everyone': False,
            'tts': False,
            **payload,
        }
        self.messages[message_id] = data
        return deepcopy(data)

    def interaction(self, message_id: int | None = None) -> Interaction:
        """Create an interaction whose expiration can be advanced independently of the messages."""
        token = f'test-token-{len(self.interactions)}'
        data = cast(
            'InteractionPayload',
            {
                'id': str(time_snowflake(utcnow())),
                'application_id': '123',
                'type': 3 if message_id else 2,
                'token': token,
                'version': 1,
                'attachment_size_limit': 1000000,
            },
        )
        interaction = Interaction(data=data, state=self.client._connection)
        interaction.channel = self.channel  # type: ignore[assignment]
        if message_id is not None:
            interaction.message = DiscordMessage(
                state=self.client._connection,
                channel=self.channel,
                data=cast('MessagePayload', deepcopy(self.messages[message_id])),
            )
            self.originals[token] = message_id
        self.interactions[token] = interaction
        return interaction

    async def bot_send(self, channel_id: int, *, params: MultipartParameters) -> dict[str, Any]:
        """Record bot-authenticated sends."""
        self.calls.append(('bot_send', channel_id))
        assert params.payload is not None
        return self.message(params.payload)

    async def bot_edit(self, _channel_id: int, message_id: int, *, params: MultipartParameters) -> dict[str, Any]:
        """Record bot-authenticated edits and reject ephemeral channel edits."""
        self.calls.append(('bot_edit', message_id))
        if 'bot_edit' in self.fail or self.messages[message_id]['flags'] & 64:
            raise NotFound(MagicMock(status=404, reason='Not Found'), 'Unavailable channel message')
        assert params.payload is not None
        self.messages[message_id].update(params.payload)
        return deepcopy(self.messages[message_id])

    async def webhook(
        self, operation: str, application_id: int, token: str, *args: int, **kwargs: object
    ) -> dict[str, Any]:
        """Reject expired tokens on all interaction endpoints."""
        interaction = self.interactions[token]
        assert application_id == (interaction.id if operation == 'response' else 123)
        self.calls.append((operation, interaction.id))
        if operation in self.fail or interaction.is_expired():
            raise NotFound(MagicMock(status=404, reason='Not Found'), 'Expired interaction token')
        if operation == 'response':
            payload = cast('MultipartParameters', kwargs['params']).payload
            assert payload is not None
            response_type = payload['type']
            if response_type == 9:
                return {'interaction': {'id': str(interaction.id)}}
            if response_type == 4:
                data = self.message(payload['data'])
                self.originals[token] = int(data['id'])
            elif response_type == 5:
                data = self.message(payload.get('data', {}))
                self.originals[token] = int(data['id'])
            else:
                data = self.messages[self.originals[token]]
                ephemeral = data['flags'] & 64
                data.update(payload.get('data', {}))
                data['flags'] |= ephemeral
            return {
                'interaction': {'id': str(interaction.id), 'response_message_id': data['id']},
                'resource': {'type': response_type, 'message': deepcopy(data)},
            }
        if operation == 'followup':
            return self.message(cast('dict[str, Any]', kwargs['payload']))
        message_id = args[0] if operation == 'webhook_edit' else self.originals[token]
        data = self.messages[message_id]
        if operation != 'get_original':
            ephemeral = data['flags'] & 64
            data.update(cast('dict[str, Any]', kwargs['payload']))
            data['flags'] |= ephemeral
        return deepcopy(data)


@pytest_asyncio.fixture
async def api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_API]:
    """Install an in-memory HTTP transport without mocking flow or discord.py delivery decisions."""
    async with Client(intents=Intents.none()) as client:
        backend = _API(client)
        monkeypatch.setattr(client.http, 'send_message', AsyncMock(side_effect=backend.bot_send))
        monkeypatch.setattr(client.http, 'edit_message', AsyncMock(side_effect=backend.bot_edit))
        adapter = MagicMock()
        for method, operation in (
            ('create_interaction_response', 'response'),
            ('get_original_interaction_response', 'get_original'),
            ('edit_original_interaction_response', 'original_edit'),
            ('execute_webhook', 'followup'),
            ('edit_webhook_message', 'webhook_edit'),
        ):
            setattr(adapter, method, AsyncMock(side_effect=partial(backend.webhook, operation)))
        token = async_context.set(adapter)
        try:
            yield backend
        finally:
            async_context.reset(token)


class _Screen(ModelBase):
    def __init__(
        self,
        content: str,
        callback: Callable[[Interaction], Result],
        *,
        v2: bool = False,
        edit: bool = False,
        ephemeral: bool = False,
    ) -> None:
        self.content = content
        self.callback = callback
        self.v2 = v2
        self.edit = edit
        self.ephemeral = ephemeral

    def message(self) -> Message | ComponentV2Message:
        """Produce an indefinitely displayed screen with controls to finalize."""
        button = Button(label=self.content).on(callback=self.callback)
        if self.v2:
            return ComponentV2Message(
                items=(ActionRow(items=(button,)),),
                edit_original=self.edit,
                ephemeral=self.ephemeral,
                disable_items=True,
            )
        return Message(
            content=self.content, items=(button,), edit_original=self.edit, ephemeral=self.ephemeral, disable_items=True
        )


async def _wait_for_screen(controller: Controller, label: str, task: asyncio.Task[None]) -> ui.Button[Any]:
    async with asyncio.timeout(2):
        while True:
            if task.done():
                await task
                pytest.fail(f'Flow ended before displaying {label}')
            view = controller._display.view
            if view is not None:
                for item in view.walk_children():
                    if isinstance(item, ui.Button) and item.label == label:
                        return item
            await asyncio.sleep(0)


def _buttons_disabled(data: dict[str, Any]) -> bool:
    def buttons(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            button
            for item in items
            for button in ([item] if item['type'] == 2 else buttons(item.get('components', [])))
        ]

    found = buttons(data['components'])
    return bool(found) and all(button['disabled'] for button in found)


@pytest.mark.parametrize('v2', [False, True])
@pytest.mark.parametrize('edit', [False, True])
@pytest.mark.parametrize('transition', ['model', 'message'])
@pytest.mark.parametrize('ending', ['send', 'finish', 'error'])
@pytest.mark.asyncio
async def test_public_flow_outlives_ui_token(api: _API, v2: bool, edit: bool, transition: str, ending: str) -> None:
    """External completion keeps bot delivery and cleanup after a UI transition's token expires."""
    release = asyncio.Event()
    next_model = _Screen('next', lambda _: Result.finish_flow(), v2=v2, edit=edit)

    def advance(_: Interaction) -> Result:
        return Result.next_model(next_model) if transition == 'model' else Result.send_message(next_model.message())

    errors: list[ExceptionGroup[Exception]] = []

    def on_error(error: ExceptionGroup[Exception]) -> Result:
        errors.append(error)
        return Result.send_message(Message(content='error notification'))

    controller = Controller(_Screen('first', advance, v2=v2), on_error=on_error)

    async def external() -> Result:
        await release.wait()
        if ending == 'error':
            raise RuntimeError('external failure')
        return Result.finish_flow() if ending == 'finish' else Result.send_message(Message(content='finished'))

    controller.create_external_result(external)
    task = asyncio.create_task(controller.invoke(api.channel))
    async with asyncio.timeout(3):
        button = await _wait_for_screen(controller, 'first', task)
        assert controller._display.message is not None
        interaction = api.interaction(controller._display.message.id)
        await button.callback(interaction)
        await _wait_for_screen(controller, 'next', task)
        assert isinstance(controller._display.message, PartialMessage)
        message_id = controller._display.message.id
        interaction.id = time_snowflake(utcnow() - timedelta(minutes=16))
        assert interaction.is_expired()
        api.calls.clear()
        release.set()
        await task
    assert _buttons_disabled(api.messages[message_id])
    assert all(operation.startswith('bot_') for operation, _ in api.calls)
    assert any(operation == 'bot_send' for operation, _ in api.calls) is (ending != 'finish')
    assert len(errors) == int(ending == 'error')
    assert not controller._tasks


@pytest.mark.parametrize('ephemeral', [False, True])
@pytest.mark.parametrize('followup', [False, True])
@pytest.mark.asyncio
async def test_message_editor_matches_actual_visibility(api: _API, ephemeral: bool, followup: bool) -> None:
    """Initial and followup messages retain their proper edit endpoint."""
    interaction = api.interaction()
    if followup:
        await interaction.response.send_message('acknowledged')
    display = FlowDisplay()
    display.start(interaction, None)
    sent = await display._send(Message(content='screen', ephemeral=ephemeral), None)
    api.calls.clear()
    display = FlowDisplay()
    display.start(api.channel, sent)
    await display._send(Message(content='changed', edit_original=True), None)
    assert api.messages[sent.id]['content'] == 'changed'
    assert (
        api.calls[0][0] == ('webhook_edit' if followup else 'original_edit')
        if ephemeral
        else api.calls[0][0] == 'bot_edit'
    )
    assert isinstance(sent, DiscordMessage)
    assert sent.flags.ephemeral is ephemeral


@pytest.mark.asyncio
async def test_failed_edits_and_interaction_send_reach_saved_destination(api: _API) -> None:
    """Every authorized fallback is attempted in order before the saved Messageable delivers the result."""
    display = FlowDisplay()
    display.start(api.channel, None)
    sent = await display._send(Message(content='first'), None)
    interaction = api.interaction(sent.id)
    api.fail.update({'response', 'bot_edit', 'followup'})
    api.calls.clear()
    display = FlowDisplay()
    display.start(api.channel, sent)
    display.begin_response(interaction)
    result = await display._send(Message(content='replacement', edit_original=True), None)
    assert [operation for operation, _ in api.calls] == ['response', 'bot_edit', 'response', 'response', 'bot_send']
    assert result.id != sent.id
    assert api.messages[result.id]['content'] == 'replacement'


@pytest.mark.asyncio
async def test_expired_ephemeral_edit_never_falls_back_to_public_send(api: _API) -> None:
    """An edit's omitted ephemeral flag cannot make failure recovery expose an ephemeral message."""
    interaction = api.interaction()
    display = FlowDisplay()
    display.start(interaction, None)
    sent = await display._send(Message(content='private', ephemeral=True), None)
    interaction.id = time_snowflake(utcnow() - timedelta(minutes=16))
    api.calls.clear()
    display = FlowDisplay()
    display.start(api.channel, sent)
    display.begin_response(interaction)
    with pytest.raises(NotFound):
        await display._send(Message(content='still private', edit_original=True), None)
    assert [operation for operation, _ in api.calls] == ['original_edit', 'followup']


@pytest.mark.asyncio
async def test_fresh_component_token_can_finalize_old_ephemeral_screen(api: _API) -> None:
    """A newly acknowledged component provides an edit route after the original response token expires."""
    initial = api.interaction()
    controller = Controller(_Screen('private', lambda _: Result.finish_flow(), ephemeral=True))
    task = asyncio.create_task(controller.invoke(initial))
    async with asyncio.timeout(3):
        button = await _wait_for_screen(controller, 'private', task)
        assert controller._display.message is not None
        message_id = controller._display.message.id
        initial.id = time_snowflake(utcnow() - timedelta(minutes=16))
        fresh = api.interaction(message_id)
        await fresh.response.defer()
        api.calls.clear()
        await button.callback(fresh)
        await task
    assert [operation for operation, _ in api.calls] == ['original_edit', 'original_edit']
    assert _buttons_disabled(api.messages[message_id])


@pytest.mark.asyncio
async def test_run_flow_creates_independent_invocations(api: _API) -> None:
    """The recommended entry point can run the same model repeatedly with fresh state."""

    class Terminal(ModelBase):
        def message(self) -> Message:
            return Message(content='done')

    await run_flow(Terminal(), api.channel)
    await run_flow(Terminal(), api.channel)
    assert len(api.messages) == 2
    assert all(message['content'] == 'done' for message in api.messages.values())


@pytest.mark.parametrize('v2', [False, True])
@pytest.mark.parametrize('edit', [False, True])
@pytest.mark.parametrize('expired', [False, True])
@pytest.mark.parametrize('ending', ['send', 'finish', 'error'])
@pytest.mark.asyncio
async def test_ephemeral_flow_respects_available_tokens(
    api: _API,
    v2: bool,
    edit: bool,
    expired: bool,
    ending: str,
) -> None:
    """Fresh UI tokens support ephemeral transitions and external completion only during their validity."""
    release = asyncio.Event()
    next_model = _Screen('next', lambda _: Result.finish_flow(), v2=v2, edit=edit, ephemeral=True)
    initial = api.interaction()
    controller = Controller(
        _Screen('first', lambda _: Result.next_model(next_model), v2=v2, ephemeral=True),
        on_error=lambda _: Result.send_message(Message(content='error', ephemeral=True)),
    )

    async def external() -> Result:
        await release.wait()
        if ending == 'error':
            raise RuntimeError('external failure')
        return (
            Result.finish_flow() if ending == 'finish' else Result.send_message(Message(content='done', ephemeral=True))
        )

    controller.create_external_result(external)
    task = asyncio.create_task(controller.invoke(initial))
    async with asyncio.timeout(3):
        button = await _wait_for_screen(controller, 'first', task)
        assert controller._display.message is not None
        old_id = controller._display.message.id
        initial.id = time_snowflake(utcnow() - timedelta(minutes=16))
        fresh = api.interaction(old_id)
        await button.callback(fresh)
        await _wait_for_screen(controller, 'next', task)
        assert controller._display.message is not None
        next_id = controller._display.message.id
        assert api.messages[next_id]['flags'] & 64
        if not edit:
            assert _buttons_disabled(api.messages[old_id])
        if expired:
            fresh.id = time_snowflake(utcnow() - timedelta(minutes=16))
        api.calls.clear()
        release.set()
        if expired:
            with pytest.raises((NotFound, ExceptionGroup)):
                await task
        else:
            await task
            assert _buttons_disabled(api.messages[next_id])
    assert all(not operation.startswith('bot_') for operation, _ in api.calls)
    assert controller._display.view is None
    assert not controller._tasks


@pytest.mark.asyncio
async def test_external_ephemeral_send_uses_latest_ui_interaction(api: _API) -> None:
    """A Messageable-started flow retains the interaction needed by an explicitly ephemeral external send."""
    release = asyncio.Event()
    next_model = _Screen('next', lambda _: Result.finish_flow())
    controller = Controller(_Screen('first', lambda _: Result.next_model(next_model)))

    async def external() -> Result:
        await release.wait()
        return Result.send_message(Message(content='private notification', ephemeral=True))

    controller.create_external_result(external)
    task = asyncio.create_task(controller.invoke(api.channel))
    async with asyncio.timeout(3):
        button = await _wait_for_screen(controller, 'first', task)
        assert controller._display.message is not None
        interaction = api.interaction(controller._display.message.id)
        await button.callback(interaction)
        await _wait_for_screen(controller, 'next', task)
        api.calls.clear()
        release.set()
        await task
    notification = next(message for message in api.messages.values() if message['content'] == 'private notification')
    assert notification['flags'] & 64
    assert [operation for operation, _ in api.calls] == ['followup', 'bot_edit']


@pytest.mark.asyncio
async def test_ephemeral_send_without_interaction_fails_before_sending(api: _API) -> None:
    """A normal Messageable cannot silently turn an ephemeral request into a public message."""
    with pytest.raises(AssertionError, match='require an interaction'):
        await run_flow(_Screen('private', lambda _: Result.finish_flow(), ephemeral=True), api.channel)
    assert api.calls == []


@pytest.mark.asyncio
async def test_programming_error_does_not_trigger_delivery_fallback(api: _API, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback is limited to Discord failures, not exceptions caused by invalid code."""
    interaction = api.interaction()
    monkeypatch.setattr(
        async_context.get(), 'create_interaction_response', AsyncMock(side_effect=TypeError('bad payload'))
    )
    display = FlowDisplay()
    display.start(api.channel, None)
    display.begin_response(interaction)
    with pytest.raises(TypeError, match='bad payload'):
        await display._send(Message(content='notice'), None)
    assert api.calls == []


@pytest.mark.asyncio
async def test_interaction_source_is_available_to_edit_fallback(api: _API) -> None:
    """Without an explicit target, a failed interaction edit can edit its public source using bot authentication."""
    display = FlowDisplay()
    display.start(api.channel, None)
    sent = await display._send(Message(content='first'), None)
    interaction = api.interaction(sent.id)
    api.fail.add('response')
    api.calls.clear()
    display = FlowDisplay()
    display.start(interaction, None)
    edited = await display._send(Message(content='changed', edit_original=True), None)
    assert edited.id == sent.id
    assert [operation for operation, _ in api.calls] == ['response', 'bot_edit']
    assert api.messages[sent.id]['content'] == 'changed'


@pytest.mark.parametrize('v2', [False, True])
@pytest.mark.parametrize('ephemeral', [False, True])
@pytest.mark.asyncio
async def test_finalization_changes_only_components(api: _API, v2: bool, ephemeral: bool) -> None:
    """Disabling controls must not clear content, embeds or attachments on either message kind."""
    release = asyncio.Event()
    controller = Controller(_Screen('screen', lambda _: Result.finish_flow(), v2=v2, ephemeral=ephemeral))

    async def finish() -> Result:
        await release.wait()
        return Result.finish_flow()

    controller.create_external_result(finish)
    task = asyncio.create_task(controller.invoke(api.interaction() if ephemeral else api.channel))
    async with asyncio.timeout(3):
        await _wait_for_screen(controller, 'screen', task)
        assert controller._display.message is not None
        message_id = controller._display.message.id
        previous = deepcopy(api.messages[message_id])
        release.set()
        await task
    assert _buttons_disabled(api.messages[message_id])
    for field in ('content', 'embeds', 'attachments'):
        assert api.messages[message_id][field] == previous[field]
    if ephemeral:
        call = cast('AsyncMock', async_context.get().edit_original_interaction_response).await_args
        assert call is not None
        payload = call.kwargs['payload']
    else:
        call = cast('AsyncMock', api.client.http.edit_message).await_args
        assert call is not None
        payload = call.kwargs['params'].payload
    assert payload is not None
    assert {'content', 'embeds', 'attachments'}.isdisjoint(payload)


@pytest.mark.parametrize('transition', ['message', 'model'])
@pytest.mark.parametrize('ephemeral', [False, True])
@pytest.mark.asyncio
async def test_modal_response_does_not_replace_external_delivery_context(
    api: _API, transition: str, ephemeral: bool
) -> None:
    """A modal result uses its submit response, while later external results retain the component context."""
    release = asyncio.Event()
    controller = Controller(_Screen('first', lambda _: Result.continue_flow(), ephemeral=ephemeral))
    next_model = _Screen('next', lambda _: Result.finish_flow(), ephemeral=ephemeral)
    modal = _InnerModal(
        ModalConfig(title='Input'),
        (),
        lambda _: Result.next_model(next_model) if transition == 'model' else Result.send_message(next_model.message()),
        controller=controller,
    )

    async def finish() -> Result:
        await release.wait()
        return Result.send_message(Message(content='private notification', ephemeral=True))

    controller.create_external_result(finish)
    initial = api.interaction()
    task = asyncio.create_task(controller.invoke(initial if ephemeral else api.channel))
    async with asyncio.timeout(3):
        button = await _wait_for_screen(controller, 'first', task)
        assert controller._display.message is not None
        source_id = controller._display.message.id
        initial.id = time_snowflake(utcnow() - timedelta(minutes=16))
        component = api.interaction(source_id)
        await component.response.send_modal(modal)
        await button.callback(component)
        controller.create_external_result(modal._wait)
        submitted = api.interaction(source_id)
        submitted.type = InteractionType.modal_submit
        await modal.on_submit(submitted)
        await _wait_for_screen(controller, 'next', task)
        assert submitted.response.is_done()
        assert controller._display._interaction is component
        assert controller._display.message is not None
        assert _buttons_disabled(api.messages[source_id])
        if not ephemeral:
            submitted.id = time_snowflake(utcnow() - timedelta(minutes=16))
        api.calls.clear()
        release.set()
        await task
    assert [operation for operation, _ in api.calls] == ['followup', 'webhook_edit' if ephemeral else 'bot_edit']
    call = cast('AsyncMock', async_context.get().execute_webhook).await_args
    assert call is not None
    assert call.args[1] == component.token
    assert any(
        message['content'] == 'private notification' and message['flags'] & 64 for message in api.messages.values()
    )


@pytest.mark.asyncio
async def test_modal_finish_can_disable_an_old_ephemeral_message(api: _API) -> None:
    """The submit response remains available through finalization without becoming retained flow context."""
    initial = api.interaction()
    controller = Controller(_Screen('private', lambda _: Result.continue_flow(), ephemeral=True))
    modal = _InnerModal(ModalConfig(title='Input'), (), lambda _: Result.finish_flow(), controller=controller)
    task = asyncio.create_task(controller.invoke(initial))
    async with asyncio.timeout(3):
        button = await _wait_for_screen(controller, 'private', task)
        assert controller._display.message is not None
        message_id = controller._display.message.id
        initial.id = time_snowflake(utcnow() - timedelta(minutes=16))
        component = api.interaction(message_id)
        await component.response.send_modal(modal)
        await button.callback(component)
        controller.create_external_result(modal._wait)
        submitted = api.interaction(message_id)
        submitted.type = InteractionType.modal_submit
        await submitted.response.defer()
        await modal.on_submit(submitted)
        await task
    assert controller._display._interaction is component
    assert _buttons_disabled(api.messages[message_id])
    assert controller._display.view is None
    assert not controller._tasks


@pytest.mark.parametrize('transition', ['message', 'model'])
@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.asyncio
async def test_old_screen_cleanup_failure_still_finalizes_replacement(
    api: _API, monkeypatch: pytest.MonkeyPatch, transition: str, cancel: bool
) -> None:
    """Once a new screen is sent, failure disabling the old one must not orphan the replacement or its tasks."""
    release = asyncio.Event()
    controller = Controller(_Screen('first', lambda _: Result.finish_flow()))
    replacement = _Screen('next', lambda _: Result.finish_flow())

    async def replace_screen() -> Result:
        await release.wait()
        return Result.next_model(replacement) if transition == 'model' else Result.send_message(replacement.message())

    controller.create_external_result(replace_screen)
    task = asyncio.create_task(controller.invoke(api.channel))
    failure = (
        asyncio.CancelledError('old screen cleanup cancelled')
        if cancel
        else NotFound(MagicMock(status=404, reason='Not Found'), 'old screen unavailable')
    )
    async with asyncio.timeout(3):
        await _wait_for_screen(controller, 'first', task)
        previous_view = controller._display.view
        assert previous_view is not None
        assert controller._display.message is not None
        old_id = controller._display.message.id

        async def edit(channel_id: int, message_id: int, *, params: MultipartParameters) -> dict[str, Any]:
            if message_id == old_id:
                raise failure
            return await api.bot_edit(channel_id, message_id, params=params)

        monkeypatch.setattr(api.client.http, 'edit_message', AsyncMock(side_effect=edit))
        release.set()
        with pytest.raises(type(failure)) as raised:
            await task
    assert raised.value is failure
    assert not _buttons_disabled(api.messages[old_id])
    replacement_data = next(message for message in api.messages.values() if message['content'] == 'next')
    assert _buttons_disabled(replacement_data)
    assert previous_view.is_finished()
    assert controller._display.message is None
    assert controller._display.view is None
    assert not controller._tasks
