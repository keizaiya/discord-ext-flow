from __future__ import annotations

import asyncio
from datetime import timedelta
from io import BytesIO
from itertools import count
from types import SimpleNamespace
from typing import TYPE_CHECKING, assert_type
from unittest.mock import AsyncMock, MagicMock

import discord.ext.flow.controller as controller_module
import discord.ext.flow.util as util_module
import pytest
from discord import (
    ChannelType,
    Client,
    File as SendableFile,
    Forbidden,
    Interaction,
    InteractionResponseType,
    MediaGalleryItem,
    Poll,
    ui,
)
from discord.abc import Messageable
from discord.ext.flow import (
    ActionRow,
    Button,
    ChannelSelect,
    ComponentV2Message,
    Container,
    File,
    InteractiveItem,
    LegacyMessage,
    Link,
    MediaGallery,
    MentionableSelect,
    Message,
    ModelBase,
    PremiumButton,
    Result,
    RoleSelect,
    Section,
    Select,
    TextDisplay,
    UserSelect,
    create_message,
)
from discord.ext.flow.controller import Controller
from discord.ext.flow.util import _Editable, into_edit_kwargs, send_helper
from discord.ext.flow.view import _LayoutView, _View, create_view

if TYPE_CHECKING:
    from discord import Member, Role, User
    from discord.app_commands import AppCommandChannel, AppCommandThread
    from discord.utils import MaybeAwaitableFunc


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


class _Model(ModelBase):
    def message(self) -> LegacyMessage:
        return LegacyMessage()


class _StaticV2Model(ModelBase):
    after_invoked = False

    def message(self) -> ComponentV2Message:
        return ComponentV2Message(items=(TextDisplay('Finished'),))

    def after_invoke(self) -> None:
        self.after_invoked = True


class _ExternalTaskV2Model(ModelBase):
    def __init__(self, controller: Controller, next_model: ModelBase, interaction: Interaction[Client]) -> None:
        self.controller = controller
        self.next_model = next_model
        self.interaction = interaction

    def before_invoke(self) -> None:
        async def transition() -> Result:
            return Result.next_model(self.next_model, interaction=self.interaction)

        self.controller.create_external_result(transition)

    def message(self) -> ComponentV2Message:
        return ComponentV2Message(items=(TextDisplay('Waiting'),))


class _ReplacingExternalTaskV2Model(ModelBase):
    def __init__(
        self,
        controller: Controller,
        next_model: ModelBase,
        interaction: Interaction[Client],
        transition_event: asyncio.Event,
    ) -> None:
        self.controller = controller
        self.next_model = next_model
        self.interaction = interaction
        self.transition_event = transition_event

    def before_invoke(self) -> None:
        async def replace_message() -> Result:
            return Result.send_message(
                ComponentV2Message(items=(TextDisplay('Still waiting'),)),
                interaction=self.interaction,
            )

        async def transition() -> Result:
            await self.transition_event.wait()
            return Result.next_model(self.next_model, interaction=self.interaction)

        self.controller.create_external_result(replace_message)
        self.controller.create_external_result(transition)

    def message(self) -> ComponentV2Message:
        return ComponentV2Message(items=(TextDisplay('Waiting'),))


class _ChainedExternalTaskV2Model(ModelBase):
    def __init__(
        self,
        controller: Controller,
        next_model: ModelBase,
        interaction: Interaction[Client],
        transition_event: asyncio.Event,
    ) -> None:
        self.controller = controller
        self.next_model = next_model
        self.interaction = interaction
        self.transition_event = transition_event

    def before_invoke(self) -> None:
        async def replace_message() -> Result:
            async def transition() -> Result:
                await self.transition_event.wait()
                return Result.next_model(self.next_model, interaction=self.interaction)

            self.controller.create_external_result(transition)
            return Result.send_message(
                ComponentV2Message(items=(TextDisplay('Still waiting'),)),
                interaction=self.interaction,
            )

        self.controller.create_external_result(replace_message)

    def message(self) -> ComponentV2Message:
        return ComponentV2Message(items=(TextDisplay('Waiting'),))


def _callback(_: Interaction[Client]) -> Result:
    return Result.finish_flow()


def test_on_returns_an_identity_preserving_generic_named_tuple() -> None:
    """Callback bindings are immutable tuple records which retain their raw config and callback."""
    raw = Button(label='Continue')
    binding = raw.on(callback=_callback)

    assert isinstance(binding, InteractiveItem)
    assert isinstance(binding, tuple)
    assert binding._fields == ('item', 'callback')
    assert binding[0] is raw
    assert binding.item is raw
    assert binding[1] is binding.callback is _callback


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'raw',
    [
        Button(),
        Select(),
        UserSelect(),
        RoleSelect(),
        MentionableSelect(),
        ChannelSelect(),
    ],
)
async def test_raw_callback_capable_items_are_rejected_from_messages(raw: object) -> None:
    """A raw Button or Select can only become a message component through .on()."""
    with pytest.raises(TypeError, match=r'must be bound with \.on'):
        create_view({}, (raw,), Controller(_Model()))  # type: ignore[arg-type, reportArgumentType]


@pytest.mark.asyncio
async def test_legacy_items_continue_to_use_view() -> None:
    """Legacy-only messages retain discord.ui.View behavior."""
    view = create_view({}, (Button().on(callback=_callback),), Controller(_Model()))

    assert isinstance(view, _View)
    assert not isinstance(view, _LayoutView)
    assert isinstance(view.children[0], ui.Button)


@pytest.mark.asyncio
async def test_v2_items_select_layout_view() -> None:
    """A V2 item causes the flow to use discord.ui.LayoutView."""
    text = TextDisplay('Component V2 content')
    view = create_view({}, (text,), Controller(_Model()))

    assert isinstance(view, _LayoutView)
    assert isinstance(view.children[0], ui.TextDisplay)
    assert view.children[0].content == text.content


@pytest.mark.parametrize(
    'items',
    [
        (Button().on(callback=_callback),),
        (TextDisplay('Component V2 content'),),
    ],
)
@pytest.mark.asyncio
async def test_view_types_share_result_lifecycle(items: tuple[object, ...]) -> None:
    """Legacy and V2 views use the same result future lifecycle."""
    view = create_view({}, items, Controller(_Model()))  # type: ignore[arg-type, reportArgumentType]
    interaction = _interaction()

    await view._set_result(Result.finish_flow(), interaction)
    completed_future = view.fut
    result = await view._wait()

    assert result._interaction is interaction
    assert result._is_end
    assert completed_future.done()
    assert view.fut is not completed_future
    assert not view.fut.done()


@pytest.mark.asyncio
async def test_static_v2_model_completes_without_waiting_for_interaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal V2 model sends its layout and completes the controller invocation."""
    send = AsyncMock(return_value=_sent())
    monkeypatch.setattr(controller_module, 'send_helper', send)
    model = _StaticV2Model()

    await asyncio.wait_for(Controller(model).invoke(_messageable()), timeout=0.1)

    await_args = send.await_args
    assert await_args is not None
    view = await_args.args[2]
    assert isinstance(view, _LayoutView)
    assert view.is_finished()
    assert model.after_invoked


@pytest.mark.asyncio
async def test_static_v2_model_waits_for_registered_external_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """A static layout still permits an external task to drive a model transition."""
    send = AsyncMock(return_value=_sent())
    monkeypatch.setattr(controller_module, 'send_helper', send)
    controller = Controller(_StaticV2Model())
    terminal_model = _StaticV2Model()
    interaction = _interaction()
    controller.model = _ExternalTaskV2Model(controller, terminal_model, interaction)

    await asyncio.wait_for(controller.invoke(_messageable()), timeout=0.1)

    assert send.await_count == 2
    assert send.await_args_list[1].args[0] is interaction
    assert terminal_model.after_invoked


@pytest.mark.asyncio
async def test_static_message_replacement_keeps_pending_external_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """A static replacement does not cancel another task that can still transition the flow."""
    replacement_sent = asyncio.Event()

    async def send(*_: object) -> _Editable:
        if send_mock.await_count == 2:
            replacement_sent.set()
        return _sent()

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)
    monkeypatch.setattr(util_module, 'send_helper', send_mock)
    controller = Controller(_StaticV2Model())
    terminal_model = _StaticV2Model()
    interaction = _interaction()
    transition_event = asyncio.Event()
    controller.model = _ReplacingExternalTaskV2Model(controller, terminal_model, interaction, transition_event)

    invoke = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)

    assert not invoke.done()
    transition_event.set()
    await asyncio.wait_for(invoke, timeout=0.1)
    assert send_mock.await_count == 3
    assert terminal_model.after_invoked


@pytest.mark.asyncio
async def test_static_message_replacement_keeps_newly_registered_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task-created successor can transition the flow after its creator renders a static message."""
    replacement_sent = asyncio.Event()

    async def send(*_: object) -> _Editable:
        if send_mock.await_count == 2:
            replacement_sent.set()
        return _sent()

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)
    monkeypatch.setattr(util_module, 'send_helper', send_mock)
    controller = Controller(_StaticV2Model())
    terminal_model = _StaticV2Model()
    interaction = _interaction()
    transition_event = asyncio.Event()
    controller.model = _ChainedExternalTaskV2Model(controller, terminal_model, interaction, transition_event)

    invoke = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(replacement_sent.wait(), timeout=0.1)

    assert not invoke.done()
    transition_event.set()
    await asyncio.wait_for(invoke, timeout=0.1)
    assert send_mock.await_count == 3
    assert terminal_model.after_invoked


@pytest.mark.asyncio
async def test_static_v2_model_stops_after_its_only_external_task_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A static flow completes after reporting the failure of its final result source."""
    send = AsyncMock(return_value=_sent())
    monkeypatch.setattr(controller_module, 'send_helper', send)

    class TrackingController(Controller):
        def __init__(self, model: ModelBase) -> None:
            super().__init__(model)
            self.on_error_mock = AsyncMock()

        async def on_error(self, exception_group: BaseExceptionGroup) -> None:
            await self.on_error_mock(exception_group)

    class FailingModel(_StaticV2Model):
        def __init__(self, controller: Controller) -> None:
            self.controller = controller

        def before_invoke(self) -> None:
            async def fail() -> Result:
                raise RuntimeError('external task failed')

            self.controller.create_external_result(fail)

    controller = TrackingController(_StaticV2Model())
    model = FailingModel(controller)
    controller.model = model

    await asyncio.wait_for(controller.invoke(_messageable()), timeout=0.1)

    controller.on_error_mock.assert_awaited_once()
    assert model.after_invoked


@pytest.mark.asyncio
async def test_section_wraps_string_children_as_text_displays() -> None:
    """Section string children use discord.py's TextDisplay shorthand."""
    section = Section(items=('First', TextDisplay('Second', id=2)), accessory=Button().on(callback=_callback))
    view = create_view({}, (section,), Controller(_Model()))

    assert isinstance(view, _LayoutView)
    item = view.children[0]
    assert isinstance(item, ui.Section)
    first, second = item.children
    assert isinstance(first, ui.TextDisplay)
    assert isinstance(second, ui.TextDisplay)
    assert [first.content, second.content] == ['First', 'Second']
    assert first.id is None
    assert second.id == 2


@pytest.mark.asyncio
async def test_nested_v2_button_returns_result_to_flow() -> None:
    """A flow button nested in a V2 container feeds its Result to the controller."""
    layout = Container(
        items=(
            TextDisplay('Component V2 content'),
            ActionRow(items=(Button(label='Continue').on(callback=_callback),)),
        ),
    )
    view = create_view(
        {},
        (layout,),
        Controller(_Model()),
    )

    assert isinstance(view, _LayoutView)
    container = view.children[0]
    assert isinstance(container, ui.Container)
    action_row = container.children[1]
    assert isinstance(action_row, ui.ActionRow)
    button = action_row.children[0]
    assert isinstance(button, ui.Button)

    interaction = _interaction()
    await button.callback(interaction)

    result = await view._wait()
    assert result._is_end
    assert result._interaction is interaction


@pytest.mark.asyncio
async def test_v2_callback_update_to_static_layout_stops_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing callback controls with static V2 content completes the current flow model."""
    sent = asyncio.Event()
    editables = (_editable(), _editable())

    class Model(ModelBase):
        def message(self) -> ComponentV2Message:
            return ComponentV2Message(items=(ActionRow(items=(Button(label='Finish').on(callback=_callback),)),))

    async def send(*_: object) -> _Editable:
        sent.set()
        return _sent(editables[send_mock.await_count - 1])

    send_mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(controller_module, 'send_helper', send_mock)
    controller = Controller(Model())
    invocation = asyncio.create_task(controller.invoke(_messageable()))
    await asyncio.wait_for(sent.wait(), timeout=0.1)
    view = send_mock.await_args_list[0].args[2]
    assert isinstance(view, _LayoutView)
    interaction = _interaction()

    result = Result.send_message(ComponentV2Message(items=(TextDisplay('Finished'),)), interaction=interaction)
    await view._set_result(result, interaction)
    await asyncio.wait_for(invocation, timeout=0.1)

    assert view.is_finished()
    replacement = send_mock.await_args_list[1].args[2]
    assert isinstance(replacement, _LayoutView)
    assert replacement.is_finished()
    assert isinstance(replacement.children[0], ui.TextDisplay)


@pytest.mark.asyncio
async def test_link_builds_a_link_button() -> None:
    """Flow link items preserve their target URL in Component V2 layouts."""
    view = create_view({}, (ActionRow(items=(Link(url='https://example.com'),)),), Controller(_Model()))

    assert isinstance(view, _LayoutView)
    row = view.children[0]
    assert isinstance(row, ui.ActionRow)
    link = row.children[0]
    assert isinstance(link, ui.Button)
    assert link.url == 'https://example.com'
    assert link.style.name == 'link'


@pytest.mark.asyncio
async def test_section_accepts_link_accessory() -> None:
    """A link-style button is a valid Section accessory in public typing and at runtime."""
    section = Section(items=('Documentation',), accessory=Link(url='https://example.com/docs'))

    view = create_view({}, (section,), Controller(_Model()))

    assert isinstance(view, _LayoutView)
    rendered_section = view.children[0]
    assert isinstance(rendered_section, ui.Section)
    assert isinstance(rendered_section.accessory, ui.Button)
    assert rendered_section.accessory.url == 'https://example.com/docs'


@pytest.mark.asyncio
async def test_button_link_and_premium_button_preserve_discord_component_ids() -> None:
    """Button variants retain numeric ids and premium buttons remain raw/non-interactive."""
    button = Button(label='Continue', id=1).on(callback=_callback)
    link = Link(url='https://example.com', id=2)
    premium = PremiumButton(123, disabled=True, row=1, id=3)

    legacy = create_view({}, (button, link, premium), Controller(_Model()))
    rendered_button, rendered_link, rendered_premium = legacy.children
    assert isinstance(rendered_button, ui.Button)
    assert rendered_button.id == 1
    assert isinstance(rendered_link, ui.Button)
    assert rendered_link.id == 2
    assert isinstance(rendered_premium, ui.Button)
    assert rendered_premium.to_component_dict() == {'type': 2, 'style': 6, 'disabled': True, 'id': 3, 'sku_id': '123'}
    assert rendered_premium.row == 1
    assert rendered_premium.is_dispatchable() is False


@pytest.mark.asyncio
async def test_premium_button_is_valid_in_v2_action_rows_and_section_accessories() -> None:
    """The raw premium config can occupy every non-interactive Button position."""
    premium = PremiumButton(456)
    layout = create_view(
        {},
        (
            ActionRow(items=(premium,)),
            Section(items=('Upgrade',), accessory=premium),
        ),
        Controller(_Model()),
    )

    row, section = layout.children
    assert isinstance(row, ui.ActionRow)
    # discord.py's Item generic is invariant, so this runtime narrowing leaves its view type unknown.
    assert isinstance(row.children[0], ui.Button)  # type: ignore[reportUnknownMemberType]
    assert row.children[0].sku_id == 456  # type: ignore[reportUnknownMemberType]
    assert isinstance(section, ui.Section)
    # discord.py's Item generic is invariant, so this runtime narrowing leaves its view type unknown.
    assert isinstance(section.accessory, ui.Button)  # type: ignore[reportUnknownMemberType]
    assert section.accessory.sku_id == 456  # type: ignore[reportUnknownMemberType]


def test_link_url_is_required_and_accepts_positional_or_keyword_binding() -> None:
    """A link target is required and is the first positional config field."""
    positional = Link('https://example.com/docs', 'Documentation')
    keyword = Link(url='https://example.com/docs', label='Documentation')

    assert positional == keyword

    link = Link('https://example.com/docs', label='Documentation')
    assert link.label == 'Documentation'
    assert link.url == 'https://example.com/docs'

    with pytest.raises(TypeError, match="missing 1 required positional argument: 'url'"):
        Link()  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_channel_select_preserves_channel_types() -> None:
    """ChannelSelect forwards its configured channel type filter to discord.py."""
    select = ChannelSelect(channel_types=(ChannelType.text,)).on(
        callback=lambda _interaction, _values: Result.finish_flow()
    )
    view = create_view({}, (ActionRow(items=(select,)),), Controller(_Model()))

    assert isinstance(view, _LayoutView)
    row = view.children[0]
    assert isinstance(row, ui.ActionRow)
    item = row.children[0]
    assert isinstance(item, ui.ChannelSelect)
    assert item.channel_types == [ChannelType.text]


def test_message_types_remain_named_tuples() -> None:
    """Both public message models retain their immutable NamedTuple behavior."""
    text = TextDisplay('Component V2 content')

    component_message = ComponentV2Message(items=(text,))
    legacy_message = LegacyMessage(content='legacy')

    assert LegacyMessage is Message
    assert isinstance(component_message, tuple)
    assert isinstance(legacy_message, tuple)
    assert component_message._replace(silent=True).silent
    assert legacy_message._replace(silent=True).silent


def test_create_message_selects_component_mode() -> None:
    """The factory selects V2 or legacy message models from their items."""
    text = TextDisplay('Component V2 content')

    assert isinstance(create_message(items=(text,)), ComponentV2Message)
    assert type(create_message(content='legacy')) is Message


if TYPE_CHECKING:

    def _check_create_message_overloads() -> None:  # type: ignore[reportUnusedFunction]
        v2_message: ComponentV2Message = create_message(items=(TextDisplay('content'),))
        legacy_message: LegacyMessage = create_message(items=(Button().on(callback=_callback),))
        assert v2_message
        assert legacy_message

    def _check_callback_binding_types() -> None:  # type: ignore[reportUnusedFunction]
        def button_callback(_: Interaction[Client]) -> Result:
            return Result.finish_flow()

        def select_callback(_: Interaction[Client], __: list[str]) -> Result:
            return Result.finish_flow()

        def user_callback(_: Interaction[Client], __: list[User | Member]) -> Result:
            return Result.finish_flow()

        def role_callback(_: Interaction[Client], __: list[Role]) -> Result:
            return Result.finish_flow()

        def mentionable_callback(_: Interaction[Client], __: list[User | Member | Role]) -> Result:
            return Result.finish_flow()

        def channel_callback(_: Interaction[Client], __: list[AppCommandChannel | AppCommandThread]) -> Result:
            return Result.finish_flow()

        button = Button()
        select = Select()
        user = UserSelect()
        role = RoleSelect()
        mentionable = MentionableSelect()
        channel = ChannelSelect()

        assert_type(button.on(callback=button_callback).item, Button)
        assert_type(button.on(callback=button_callback).callback, MaybeAwaitableFunc[[Interaction[Client]], Result])
        assert_type(select.on(callback=select_callback).item, Select)
        assert_type(
            select.on(callback=select_callback).callback, MaybeAwaitableFunc[[Interaction[Client], list[str]], Result]
        )
        assert_type(
            user.on(callback=user_callback).callback,
            MaybeAwaitableFunc[[Interaction[Client], list[User | Member]], Result],
        )
        assert_type(
            role.on(callback=role_callback).callback, MaybeAwaitableFunc[[Interaction[Client], list[Role]], Result]
        )
        assert_type(
            mentionable.on(callback=mentionable_callback).callback,
            MaybeAwaitableFunc[[Interaction[Client], list[User | Member | Role]], Result],
        )
        assert_type(
            channel.on(callback=channel_callback).callback,
            MaybeAwaitableFunc[[Interaction[Client], list[AppCommandChannel | AppCommandThread]], Result],
        )

        raw_button = Button()
        raw_select = Select()
        premium = PremiumButton(1)
        LegacyMessage(items=(raw_button,))  # type: ignore[arg-type]
        ActionRow(items=(raw_select,))  # type: ignore[arg-type]
        LegacyMessage(items=(premium,))
        ActionRow(items=(premium,))
        Section(items=('Upgrade',), accessory=premium)


def test_create_message_rejects_v2_incompatible_fields() -> None:
    """The factory rejects traditional message fields when a V2 item is present."""
    text = TextDisplay('Component V2 content')

    with pytest.raises(ValueError, match='content'):
        create_message(items=(text,), content='invalid')
    with pytest.raises(ValueError, match='embeds'):
        create_message(items=(text,), embeds=())
    with pytest.raises(ValueError, match='poll'):
        create_message(items=(text,), poll=Poll('Question?', timedelta(hours=1)))
    with pytest.raises(ValueError, match='tts'):
        create_message(items=(text,), tts=True)
    with pytest.raises(ValueError, match='suppress_embeds'):
        create_message(items=(text,), suppress_embeds=True)
    with pytest.raises(ValueError, match='cannot be mixed'):
        create_message(items=(text, Button().on(callback=_callback)))


def test_v2_edit_preserves_attachments_when_files_are_omitted() -> None:
    """Editing a V2 message without files leaves existing attachments unchanged."""
    text = TextDisplay('Component V2 content')
    message = ComponentV2Message(items=(text,))

    kwargs = into_edit_kwargs(message._to_dict(), components_v2=True)

    assert kwargs['content'] is None if 'content' in kwargs else True
    assert kwargs['embeds'] == () if 'embeds' in kwargs else True
    assert 'attachments' not in kwargs


def test_legacy_to_v2_edit_preserves_new_file_attachments() -> None:
    """A single multipart edit can replace legacy attachments while enabling V2."""
    text = TextDisplay('Component V2 content')
    attachment = SendableFile(BytesIO(b''), filename='attachment.txt')
    message = ComponentV2Message(items=(text,), files=(attachment,))

    kwargs = into_edit_kwargs(message._to_dict(), components_v2=True)

    assert kwargs['content'] is None if 'content' in kwargs else True
    assert kwargs['embeds'] == () if 'embeds' in kwargs else True
    assert 'attachments' in kwargs
    assert kwargs['attachments'] == (attachment,)


def test_v2_edit_accepts_explicit_file_attachments() -> None:
    """A V2 edit can replace attachments in the same request."""
    attachment = SendableFile(BytesIO(b''), filename='attachment.txt')
    message = ComponentV2Message(items=(TextDisplay('Component V2 content'),), files=(attachment,))

    kwargs = into_edit_kwargs(message._to_dict(), components_v2=True)

    assert 'attachments' in kwargs
    assert kwargs['attachments'] == (attachment,)


def test_v2_edit_accepts_explicit_empty_file_attachments() -> None:
    """An explicit empty file sequence removes all attachments during a V2 edit."""
    message = ComponentV2Message(items=(TextDisplay('Component V2 content'),), files=())

    kwargs = into_edit_kwargs(message._to_dict(), components_v2=True)

    assert 'attachments' in kwargs
    assert kwargs['attachments'] == ()


@pytest.mark.parametrize('response_done', [False, True])
@pytest.mark.asyncio
async def test_legacy_edit_of_v2_target_is_delegated_to_discord(
    monkeypatch: pytest.MonkeyPatch, *, response_done: bool
) -> None:
    """Legacy edits are attempted without inspecting the target's component mode."""
    edited_message = _editable()
    interaction_message = _editable()
    response = SimpleNamespace(
        is_done=lambda: response_done,
        edit_message=AsyncMock(),
        send_message=AsyncMock(),
        type=None,
    )
    followup = SimpleNamespace(send=AsyncMock())

    class FakeInteraction:
        def __init__(self) -> None:
            self.response = response
            self.followup = followup
            self.message = object()
            self.original_response = AsyncMock(return_value=interaction_message)

    interaction = FakeInteraction()
    edit = SimpleNamespace(edit=AsyncMock(return_value=edited_message))
    monkeypatch.setattr(util_module, 'Interaction', FakeInteraction)

    returned = await send_helper(
        interaction,  # type: ignore[arg-type, reportArgumentType]  # Runtime Interaction is monkeypatched to FakeInteraction.
        LegacyMessage(content='Legacy state', edit_original=True),
        None,
        edit,  # type: ignore[arg-type, reportArgumentType]  # Minimal fake deliberately exercises only edit().
    )

    followup.send.assert_not_awaited()
    response.send_message.assert_not_awaited()
    if response_done:
        response.edit_message.assert_not_awaited()
        edit.edit.assert_awaited_once_with(content='Legacy state')
        assert returned is edited_message
    else:
        response.edit_message.assert_awaited_once_with(content='Legacy state')
        edit.edit.assert_not_awaited()
        assert returned is interaction_message


@pytest.mark.asyncio
async def test_legacy_partial_message_is_edited_without_fetching() -> None:
    """Legacy edits do not require Read Message History for an ordinary partial target."""

    class FakePartialMessage:
        def __init__(self) -> None:
            self.fetch = AsyncMock(side_effect=Forbidden(MagicMock(), 'Missing Access'))
            self.edit = AsyncMock(return_value=object())

    edit = FakePartialMessage()
    messageable = SimpleNamespace(send=AsyncMock())

    returned = await send_helper(
        messageable,  # type: ignore[arg-type, reportArgumentType]  # Minimal fake deliberately exercises only send().
        LegacyMessage(content='Updated', edit_original=True),
        None,
        edit,  # type: ignore[arg-type, reportArgumentType]  # Minimal fake deliberately exercises only edit().
    )

    edit.fetch.assert_not_awaited()
    edit.edit.assert_awaited_once_with(content='Updated')
    messageable.send.assert_not_awaited()
    assert returned is edit.edit.return_value


@pytest.mark.asyncio
async def test_v2_edit_of_partial_message_does_not_fetch() -> None:
    """A V2 edit does not require extra read permissions for an ordinary partial target."""

    class FakePartialMessage:
        def __init__(self) -> None:
            self.fetch = AsyncMock(side_effect=Forbidden(MagicMock(), 'Missing Access'))
            self.edit = AsyncMock(return_value=object())

    edit = FakePartialMessage()
    messageable = SimpleNamespace(send=AsyncMock())

    returned = await send_helper(
        messageable,  # type: ignore[arg-type, reportArgumentType]  # Minimal fake deliberately exercises only send().
        ComponentV2Message(items=(TextDisplay('Updated'),), edit_original=True),
        None,
        edit,  # type: ignore[arg-type, reportArgumentType]  # Minimal fake deliberately exercises only edit().
    )

    edit.fetch.assert_not_awaited()
    edit.edit.assert_awaited_once()
    edit_await_args = edit.edit.await_args
    assert edit_await_args is not None
    edit_kwargs = edit_await_args.kwargs
    assert 'attachments' not in edit_kwargs
    messageable.send.assert_not_awaited()
    assert returned is edit.edit.return_value


@pytest.mark.asyncio
async def test_deferred_ephemeral_v2_response_retains_interaction_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deferred ephemeral responses remain editable and deletable through their interaction webhook."""
    response_message = SimpleNamespace(
        flags=SimpleNamespace(ephemeral=True),
        delete=AsyncMock(),
    )
    response = SimpleNamespace(
        is_done=lambda: True,
        type=InteractionResponseType.deferred_channel_message,
    )

    class FakeInteraction:
        def __init__(self) -> None:
            self.response = response
            self.edit_original_response = AsyncMock(return_value=response_message)

    interaction = FakeInteraction()
    monkeypatch.setattr(util_module, 'Interaction', FakeInteraction)

    returned = await send_helper(
        interaction,  # type: ignore[arg-type, reportArgumentType]  # Runtime Interaction is monkeypatched to FakeInteraction.
        ComponentV2Message(
            items=(TextDisplay('Ephemeral'),),
            ephemeral=True,
            delete_after=1,
        ),
        None,
        None,
    )

    assert returned is response_message
    response_message.delete.assert_awaited_once_with(delay=1)


def test_v2_edit_matches_discord_py_for_incompatible_kwargs() -> None:
    """The low-level converter follows discord.py and lets its V2 values replace legacy values."""
    kwargs = Message(content='legacy')._to_dict()

    converted = into_edit_kwargs(kwargs, components_v2=True)

    assert converted['content'] is None if 'content' in converted else True
    assert converted['embeds'] == () if 'embeds' in converted else True
    assert 'attachments' not in converted


@pytest.mark.parametrize(
    'items',
    [
        tuple(Button().on(callback=_callback) for _ in range(6)),
        (Button().on(callback=_callback), Select().on(callback=lambda _interaction, _values: Result.finish_flow())),
        (
            Select().on(callback=lambda _interaction, _values: Result.finish_flow()),
            Select().on(callback=lambda _interaction, _values: Result.finish_flow()),
        ),
    ],
)
@pytest.mark.asyncio
async def test_action_row_delegates_width_validation_to_discord_py(items: tuple[object, ...]) -> None:
    """Action Row configs stay thin and defer width validation until conversion."""
    row = ActionRow(items=items)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match='maximum number of children exceeded'):
        create_view({}, (row,), Controller(_Model()))


def test_empty_action_row_matches_discord_py() -> None:
    """discord.py permits construction of an empty ActionRow."""
    ActionRow(items=())


@pytest.mark.parametrize('count', [4])
@pytest.mark.asyncio
async def test_section_delegates_text_item_count_validation_to_discord_py(count: int) -> None:
    """Section configs defer text item count validation until conversion."""
    section = Section(
        items=tuple(TextDisplay(str(index)) for index in range(count)),
        accessory=Button().on(callback=_callback),
    )

    with pytest.raises(ValueError, match=r'maximum number of children exceeded \(3\)'):
        create_view({}, (section,), Controller(_Model()))


def test_empty_section_matches_discord_py() -> None:
    """discord.py permits construction of an empty Section."""
    Section(items=(), accessory=Button().on(callback=_callback))


@pytest.mark.parametrize('count', [11])
@pytest.mark.asyncio
async def test_media_gallery_delegates_item_count_validation_to_discord_py(count: int) -> None:
    """Media Gallery configs do not impose a count limit ahead of discord.py."""
    gallery = MediaGallery(items=tuple(MediaGalleryItem('https://example.com/image.png') for _ in range(count)))

    view = create_view({}, (gallery,), Controller(_Model()))

    converted = view.children[0]
    assert isinstance(converted, ui.MediaGallery)
    assert len(converted.items) == count


def test_empty_media_gallery_matches_discord_py() -> None:
    """discord.py permits construction of an empty MediaGallery."""
    MediaGallery(items=())


def test_file_display_string_validation_matches_discord_py() -> None:
    """discord.py passes File media strings through without local validation."""
    File('https://example.com/file.txt')


@pytest.mark.asyncio
async def test_v2_layout_delegates_total_component_count_validation_to_discord_py() -> None:
    """Layout conversion delegates discord.py's nested component count validation."""
    rows = tuple(ActionRow(items=(Button().on(callback=_callback),)) for _ in range(20))

    with pytest.raises(ValueError, match=r'maximum number of children exceeded \(40\)'):
        create_view({}, (Container(items=rows),), Controller(_Model()))


@pytest.mark.asyncio
async def test_v2_layout_allows_duplicate_item_ids_like_discord_py() -> None:
    """discord.py does not locally validate explicit component ID uniqueness."""
    create_view({}, (TextDisplay('first', id=1), TextDisplay('second', id=1)), Controller(_Model()))


@pytest.mark.asyncio
async def test_v2_layout_allows_duplicate_custom_ids_like_discord_py() -> None:
    """discord.py does not locally validate interactive custom ID uniqueness."""
    rows = (
        ActionRow(items=(Button(custom_id='duplicate').on(callback=_callback),)),
        ActionRow(items=(Button(custom_id='duplicate').on(callback=_callback),)),
    )

    create_view({}, rows, Controller(_Model()))
