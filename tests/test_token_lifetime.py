from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from functools import partial
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from discord import (
    Client,
    File,
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
    ExternalTaskLifeTime,
    Message,
    ModalConfig,
    ModelBase,
    Result,
    TextDisplay,
    run_flow,
    send_modal,
)
from discord.ext.flow.display import FlowDisplay
from discord.ext.flow.modal import _InnerModal
from discord.ext.flow.view import create_view
from discord.utils import time_snowflake, utcnow
from discord.webhook.async_ import async_context

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence
    from pathlib import Path

    from discord.ext.flow import ExternalResultTask
    from discord.ext.flow.model import ViewConfig
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
        self,
        operation: str,
        application_id: int,
        token: str,
        *args: int,
        **kwargs: object,
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
                data['flags'] |= 128
                self.messages[int(data['id'])]['flags'] |= 128
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
            payload = cast('dict[str, Any]', kwargs['payload'])
            original = self.messages[self.originals[token]]
            if original['flags'] & 128:
                ephemeral = original['flags'] & 64
                original.update(payload)
                original['flags'] = (original['flags'] & ~64) | ephemeral
                original['flags'] &= ~128
                return deepcopy(original)
            return self.message(payload)
        message_id = args[0] if operation == 'webhook_edit' else self.originals[token]
        data = self.messages[message_id]
        if operation != 'get_original':
            ephemeral = data['flags'] & 64
            data.update(cast('dict[str, Any]', kwargs['payload']))
            data['flags'] |= ephemeral
            if operation == 'original_edit':
                data['flags'] &= ~128
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


@pytest.mark.parametrize('edit_original', [False, True])
@pytest.mark.asyncio
async def test_deferred_v2_original_is_completed_once(api: _API, edit_original: bool) -> None:
    """After a deferred V2 original is completed, later messages use follow-ups and keep their visibility."""
    interaction = api.interaction()
    await interaction.response.defer()
    display = FlowDisplay()
    display.start(interaction, None)

    await display._send(ComponentV2Message(items=(TextDisplay('public'),), edit_original=edit_original), None)
    await display._send(ComponentV2Message(items=(TextDisplay('private'),), ephemeral=True), None)

    original_id = api.originals[interaction.token]
    followup_id = next(message_id for message_id in api.messages if message_id != original_id)
    assert [operation for operation, _ in api.calls] == [
        'response',
        'get_original',
        'original_edit',
        'followup',
    ]
    assert not api.messages[original_id]['flags'] & 64
    assert api.messages[followup_id]['flags'] & 64


@pytest.mark.asyncio
async def test_deferred_legacy_followup_completes_original_before_v2_send(api: _API) -> None:
    """A legacy follow-up completes a deferred original before a later V2 message is sent."""
    interaction = api.interaction()
    await interaction.response.defer()
    display = FlowDisplay()
    display.start(interaction, None)

    await display._send(Message(content='public'), None)
    await display._send(ComponentV2Message(items=(TextDisplay('private'),), ephemeral=True), None)

    original_id = api.originals[interaction.token]
    followup_id = next(message_id for message_id in api.messages if message_id != original_id)
    assert [operation for operation, _ in api.calls] == ['response', 'followup', 'followup']
    assert api.messages[original_id]['content'] == 'public'
    assert api.messages[followup_id]['flags'] & 64


@pytest.mark.parametrize('edit_original', [False, True])
@pytest.mark.asyncio
async def test_v2_send_after_caller_completed_defer_uses_followup(api: _API, edit_original: bool) -> None:
    """A V2 flow started after an external defer completion does not edit the old original."""
    interaction = api.interaction()
    await interaction.response.defer()
    await interaction.followup.send('already complete')
    display = FlowDisplay()
    display.start(interaction, None)

    await display._send(
        ComponentV2Message(items=(TextDisplay('private'),), ephemeral=True, edit_original=edit_original), None
    )

    original_id = api.originals[interaction.token]
    followup_id = next(message_id for message_id in api.messages if message_id != original_id)
    assert [operation for operation, _ in api.calls] == [
        'response',
        'followup',
        'get_original',
        'followup',
    ]
    assert api.messages[original_id]['content'] == 'already complete'
    assert api.messages[followup_id]['flags'] & 64


@pytest.mark.asyncio
async def test_v2_send_refreshes_cached_deferred_original_before_route_selection(api: _API) -> None:
    """A cached loading response is refreshed before deciding whether the deferred original is still pending."""
    interaction = api.interaction()
    await interaction.response.defer()
    await interaction.original_response()
    await interaction.edit_original_response(content='already complete')
    display = FlowDisplay()
    display.start(interaction, None)

    await display._send(ComponentV2Message(items=(TextDisplay('private'),), ephemeral=True), None)

    original_id = api.originals[interaction.token]
    followup_id = next(message_id for message_id in api.messages if message_id != original_id)
    assert [operation for operation, _ in api.calls] == [
        'response',
        'get_original',
        'original_edit',
        'get_original',
        'followup',
    ]
    assert api.messages[original_id]['content'] == 'already complete'
    assert api.messages[followup_id]['flags'] & 64


@pytest.mark.asyncio
async def test_ephemeral_v2_send_refreshes_deferred_original_through_interaction_webhook(api: _API) -> None:
    """The uncached interaction-webhook read handles an externally completed ephemeral defer."""
    interaction = api.interaction()
    await interaction.response.defer(ephemeral=True)
    await interaction.original_response()
    await interaction.followup.send('already complete')
    display = FlowDisplay()
    display.start(interaction, None)

    await display._send(ComponentV2Message(items=(TextDisplay('private'),), ephemeral=True), None)

    original_id = api.originals[interaction.token]
    followup_id = next(message_id for message_id in api.messages if message_id != original_id)
    assert [operation for operation, _ in api.calls] == [
        'response',
        'get_original',
        'followup',
        'get_original',
        'followup',
    ]
    assert api.messages[original_id]['flags'] & 64
    assert api.messages[followup_id]['flags'] & 64


@pytest.mark.asyncio
async def test_deferred_v2_send_does_not_edit_cached_original_when_refresh_fails(api: _API) -> None:
    """A failed fresh-state read never falls back to editing a cached loading response."""
    interaction = api.interaction()
    await interaction.response.defer(ephemeral=True)
    await interaction.original_response()
    api.calls.clear()
    api.fail.add('get_original')
    display = FlowDisplay()
    display.start(interaction, None)

    with pytest.raises(NotFound):
        await display._send(ComponentV2Message(items=(TextDisplay('private'),), ephemeral=True), None)

    original_id = api.originals[interaction.token]
    assert [operation for operation, _ in api.calls] == ['get_original']
    assert api.messages[original_id]['flags'] & 128
    assert api.messages[original_id]['content'] == ''


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
    api: _API,
    transition: str,
    ephemeral: bool,
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
    api: _API,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
    cancel: bool,
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


@pytest.mark.parametrize('destination', ['channel', 'initial', 'followup'])
@pytest.mark.asyncio
async def test_static_layout_finishes_and_cancels_external_task(api: _API, destination: str) -> None:
    """Static layouts finish without waiting for their configured timeout or a running external task."""
    pending: ExternalResultTask | None = None
    errors: list[ExceptionGroup[Exception]] = []
    views: list[ui.LayoutView] = []

    class StaticModel(ModelBase):
        async def before_invoke(self) -> None:
            nonlocal pending

            async def never_finishes() -> Result:
                await asyncio.Event().wait()
                return Result.finish_flow()

            pending = controller.create_external_result(never_finishes)
            await asyncio.sleep(0)

        def message(self) -> ComponentV2Message:
            return ComponentV2Message(items=(TextDisplay('Done'),))

        def view_config(self) -> ViewConfig:
            return {'timeout': 0.001}

        def after_invoke(self) -> None:
            view = controller._display.view
            assert isinstance(view, ui.LayoutView)
            views.append(view)

    controller = Controller(StaticModel(), on_error=errors.append)
    interaction = api.interaction()
    if destination == 'followup':
        await interaction.response.send_message('Acknowledged')
    await asyncio.wait_for(controller.invoke(api.channel if destination == 'channel' else interaction), timeout=0.1)

    assert pending is not None
    assert pending.task.cancelled()
    assert not errors
    assert not controller._tasks
    assert len(views) == 1
    assert views[0].is_finished()
    assert views[0].timeout == 0.001


@pytest.mark.parametrize('route', ['channel', 'initial', 'acknowledged'])
@pytest.mark.parametrize('send_fails', [False, True])
@pytest.mark.asyncio
async def test_uploads_survive_failed_delivery_routes(  # noqa: C901, PLR0915 - Exercise all delivery routes.
    api: _API,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
    send_fails: bool,
) -> None:
    """Real discord.py endpoints may close attempt files without consuming the original upload's lifetime."""
    path = tmp_path / 'upload.txt'
    path.write_bytes(b'path upload')
    stream = BytesIO(b'prefix-buffer upload')
    stream.seek(7)
    originals = (
        File(path, filename='path.txt', spoiler=True, description='path description'),
        File(stream, filename='buffer.txt', description='buffer description'),
    )
    attempts: list[str] = []
    wrappers: list[File] = []

    def consume(operation: str, files: Sequence[File] | None) -> None:
        assert files is not None
        attempts.append(operation)
        assert [file.fp.read() for file in files] == [b'path upload', b'buffer upload']
        assert [file.to_dict(index) for index, file in enumerate(files)] == [
            file.to_dict(index) for index, file in enumerate(originals)
        ]
        assert all(file is not original for file, original in zip(files, originals, strict=True))
        assert all(file not in wrappers for file in files)
        wrappers.extend(files)

    error = NotFound(MagicMock(status=404, reason='Not Found'), 'Unavailable message')

    async def edit(_channel_id: int, _message_id: int, *, params: MultipartParameters) -> dict[str, Any]:
        consume('edit', params.files)
        raise error

    async def send(_channel_id: int, *, params: MultipartParameters) -> dict[str, Any]:
        consume('send', params.files)
        if send_fails:
            raise error
        return api.message({})

    async def response(*_args: object, params: MultipartParameters, **_kwargs: object) -> dict[str, Any]:
        if params.files:
            consume('response', params.files)
        raise error

    async def original_edit(*_args: object, files: Sequence[File], **_kwargs: object) -> dict[str, Any]:
        consume('original_edit', files)
        raise error

    async def followup(*_args: object, files: Sequence[File], **_kwargs: object) -> dict[str, Any]:
        consume('followup', files)
        raise error

    monkeypatch.setattr(api.client.http, 'edit_message', edit)
    monkeypatch.setattr(api.client.http, 'send_message', send)
    display = FlowDisplay()
    target = api.channel.get_partial_message(int(api.message({})['id']))
    display.start(api.channel, target)
    if route != 'channel':
        interaction = api.interaction(target.id)
        if route == 'acknowledged':
            await interaction.response.defer()
        display.begin_response(interaction)
        adapter = async_context.get()
        monkeypatch.setattr(adapter, 'create_interaction_response', response)
        monkeypatch.setattr(adapter, 'execute_webhook', followup)
        monkeypatch.setattr(adapter, 'edit_original_interaction_response', original_edit)

    message = ComponentV2Message(items=(TextDisplay('Upload'),), files=originals, edit_original=True)
    if send_fails:
        with pytest.raises(NotFound):
            await display.replace(message, None)
    else:
        await display.replace(message, None)
        assert display.message is not None
        assert display.message.id != target.id

    expected_routes = {
        'channel': ['edit', 'send'],
        'initial': ['response', 'edit', 'response', 'send'],
        'acknowledged': ['original_edit', 'edit', 'followup', 'send'],
    }
    assert attempts == expected_routes[route]
    assert originals[0].fp.closed
    assert not stream.closed
    stream.close()


@pytest.mark.parametrize('ending', ['model', 'finish', 'cancel'])
@pytest.mark.parametrize('submitted', [False, True])
@pytest.mark.asyncio
async def test_modal_task_is_reclaimed_with_its_model(  # noqa: PLR0915 - Check waiting and running modal cleanup across all exit paths.
    api: _API,
    ending: str,
    submitted: bool,
) -> None:
    """Leaving a model reclaims both pending modals and callbacks, including async callback cleanup."""
    started = asyncio.Event()
    closed = asyncio.Event()
    cleanup_tasks: list[ExternalResultTask] = []
    controller = Controller(_Screen('first', lambda _: Result.continue_flow()))

    async def pending() -> Result:
        await asyncio.Event().wait()
        return Result.continue_flow()

    async def callback(_: Interaction) -> Result:
        started.set()
        try:
            return await pending()
        finally:
            await asyncio.sleep(0)
            cleanup_tasks.append(controller.create_external_result(pending, life_time=ExternalTaskLifeTime.MODEL))
            closed.set()

    class NextModel(_Screen):
        def before_invoke(self) -> None:
            assert closed.is_set() is submitted
            assert all(task.task.cancelled() for task in cleanup_tasks)

    invocation = asyncio.create_task(controller.invoke(api.channel))
    async with asyncio.timeout(3):
        await _wait_for_screen(controller, 'first', invocation)
        assert controller._display.message is not None
        source_id = controller._display.message.id
        modal_task = await send_modal(
            callback, api.interaction(source_id), ModalConfig('Input'), (TextDisplay('Submit'),), controller=controller
        )
        modal = next(iter(api.client._connection._view_store._modals.values()))
        interaction = api.interaction(source_id)
        interaction.type = InteractionType.modal_submit
        if submitted:
            await modal._dispatch_submit(interaction, [], {})
            await started.wait()

        if ending == 'model':
            next_model = NextModel('next', lambda _: Result.continue_flow())
            controller.create_external_result(lambda: Result.next_model(next_model))
            await _wait_for_screen(controller, 'next', invocation)
            assert modal_task.task.cancelled()
            controller.create_external_result(Result.finish_flow)
        elif ending == 'finish':
            controller.create_external_result(Result.finish_flow)
        else:
            invocation.cancel()

        if ending == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await invocation
        else:
            await invocation
        if not submitted:
            await modal._dispatch_submit(interaction, [], {})
            assert not started.is_set()

    assert modal.is_finished()
    assert modal_task.task.cancelled()
    assert closed.is_set() is submitted
    assert all(task.task.cancelled() for task in cleanup_tasks)
    assert not controller._tasks


@pytest.mark.parametrize('transition', ['message', 'same', 'equal'])
@pytest.mark.parametrize('submitted', [False, True])
@pytest.mark.asyncio
async def test_modal_task_survives_same_model_updates(api: _API, transition: str, submitted: bool) -> None:
    """Ordinary modals keep waiting or executing across message replacements and equal-model transitions."""
    started = asyncio.Event()
    release = asyncio.Event()

    class EqualScreen(_Screen):  # noqa: PLW1641 - Mutable models are intentionally unhashable.
        def __eq__(self, other: object) -> bool:
            return isinstance(other, EqualScreen)

    initial_model = EqualScreen('first', lambda _: Result.continue_flow())
    controller = Controller(initial_model)

    async def callback(_: Interaction) -> Result:
        started.set()
        await release.wait()
        return Result.send_message(Message(content='Completed'))

    invocation = asyncio.create_task(controller.invoke(api.channel))
    async with asyncio.timeout(3):
        await _wait_for_screen(controller, 'first', invocation)
        assert controller._display.message is not None
        source_id = controller._display.message.id
        modal_task = await send_modal(
            callback, api.interaction(source_id), ModalConfig('Input'), (TextDisplay('Submit'),), controller=controller
        )
        modal = next(iter(api.client._connection._view_store._modals.values()))
        interaction = api.interaction(source_id)
        interaction.type = InteractionType.modal_submit
        if submitted:
            await modal._dispatch_submit(interaction, [], {})
            await started.wait()

        next_model = EqualScreen('next', lambda _: Result.continue_flow())
        if transition == 'same':
            initial_model.content = 'next'
            result = Result.next_model(initial_model)
        elif transition == 'equal':
            result = Result.next_model(next_model)
        else:
            result = Result.send_message(next_model.message())
        controller.create_external_result(lambda: result)
        await _wait_for_screen(controller, 'next', invocation)
        assert not modal_task.done()
        if not submitted:
            assert not modal.is_finished()
            await modal._dispatch_submit(interaction, [], {})
            await started.wait()
        release.set()
        await invocation
    assert not modal_task.task.cancelled()
    assert not controller._tasks
    assert any(message['content'] == 'Completed' for message in api.messages.values())


@pytest.mark.asyncio
async def test_modal_callback_cleanup_error_propagates_from_invoke(api: _API) -> None:
    """A modal callback's cancellation failure cannot escape the controller's cleanup handling."""
    started = asyncio.Event()
    failure = RuntimeError('modal cleanup failed')
    controller = Controller(_Screen('first', lambda _: Result.continue_flow()))

    async def callback(_: Interaction) -> Result:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            raise failure

    invocation = asyncio.create_task(controller.invoke(api.channel))
    async with asyncio.timeout(3):
        await _wait_for_screen(controller, 'first', invocation)
        assert controller._display.message is not None
        source_id = controller._display.message.id
        await send_modal(
            callback, api.interaction(source_id), ModalConfig('Input'), (TextDisplay('Submit'),), controller=controller
        )
        modal = next(iter(api.client._connection._view_store._modals.values()))
        interaction = api.interaction(source_id)
        interaction.type = InteractionType.modal_submit
        await modal._dispatch_submit(interaction, [], {})
        await started.wait()
        controller.create_external_result(Result.finish_flow)
        with pytest.raises(RuntimeError, match='modal cleanup failed') as raised:
            await invocation
    assert raised.value is failure
    assert not controller._tasks


@pytest.mark.parametrize('v2', [False, True])
@pytest.mark.parametrize('route', ['initial', 'deferred', 'followup'])
@pytest.mark.parametrize('config', [{}, {'timeout': None}, {'timeout': 0.01}])
@pytest.mark.asyncio
async def test_ephemeral_view_uses_config_at_construction(api: _API, v2: bool, route: str, config: ViewConfig) -> None:
    """Flow constructs views from ViewConfig and leaves subsequent discord.py timeout changes intact."""
    controller = Controller(ModelBase())
    display = FlowDisplay()
    interaction = api.interaction()
    if route == 'deferred':
        await interaction.response.defer(ephemeral=True)
    elif route == 'followup':
        await interaction.response.send_message('Acknowledged', ephemeral=True)
    display.start(interaction)
    message = _Screen('first', lambda _: Result.continue_flow(), v2=v2, ephemeral=True).message()
    first = create_view(config, message.items or (), controller)
    assert first.timeout == config.get('timeout')
    await display.replace(message, first)
    first.stop()
    assert display.message is not None
    display.begin_response(api.interaction(display.message.id))
    second = create_view(config, message.items or (), controller)
    assert second.timeout == config.get('timeout')
    await display.replace(message._replace(edit_original=True), second)
    if config.get('timeout') is not None:
        assert await asyncio.wait_for(second.wait(), timeout=1)
    else:
        await asyncio.sleep(0.02)
        assert not second.is_finished()
        second.stop()
