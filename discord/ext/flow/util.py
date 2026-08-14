from __future__ import annotations

from asyncio import gather
from typing import TYPE_CHECKING, Protocol, TypedDict

from discord import DiscordException, Interaction, InteractionResponseType, Message as DiscordMessage

from .item import (
    ActionRow,
    Button,
    ChannelSelect,
    Container,
    InteractiveItem,
    MentionableSelect,
    RoleSelect,
    Section,
    Select,
    UserSelect,
)
from .model import ComponentV2Message, LegacyMessage

if TYPE_CHECKING:
    from asyncio import Task
    from collections.abc import Callable, Iterable, Sequence
    from typing import Any

    from discord import AllowedMentions, Attachment, Embed, File
    from discord.abc import Messageable
    from discord.ui import LayoutView, View

    from .item import ItemType
    from .model import MessageKwargs
    from .view import _ViewType

    type Sendable = Interaction | Messageable


def unwrap_or[T, U](value: T | None, default: U) -> T | U:
    """Return value if value is not None, otherwise return default."""
    if value is None:
        return default
    return value


def map_or[T, U, V](value: T | None, default: U, func: Callable[[T], V]) -> V | U:
    """Return func(value) if value is not None, otherwise return default."""
    if value is None:
        return default
    return func(value)


def view_can_produce_result(view: _ViewType) -> bool:
    """Return whether a view contains an enabled item that can dispatch a flow callback."""
    return any(item.is_dispatchable() and not getattr(item, 'disabled', False) for item in view.walk_children())


def _item_can_produce_result(item: ItemType) -> bool:
    match item:
        case InteractiveItem(
            item=(
                Button() | Select() | UserSelect() | RoleSelect() | MentionableSelect() | ChannelSelect()
            ) as component
        ):
            return not component.disabled
        case ActionRow(items=items) | Container(items=items):
            return items_can_produce_result(items)
        case Section(accessory=InteractiveItem(item=Button() as button)):
            return not button.disabled
        case _:
            return False


def items_can_produce_result(items: Sequence[ItemType]) -> bool:
    """Return whether configured items contain an enabled flow callback."""
    return any(_item_can_produce_result(item) for item in items)


class _Editable(Protocol):
    id: int

    async def edit(
        self,
        *,
        content: str | None = None,
        embeds: Sequence[Embed] | None = None,
        attachments: Sequence[Attachment | File] | None = None,
        view: LayoutView | View | None = None,
        allowed_mentions: AllowedMentions | None = None,
    ) -> _Editable:
        """PartialMessage.edit, Message.edit or WebhookMessage.edit."""
        ...


class _SendHelperKWType(TypedDict, total=False):
    content: str
    tts: bool
    embeds: Sequence[Embed]
    files: Sequence[File]
    allowed_mentions: AllowedMentions
    view: LayoutView | View
    suppress_embeds: bool
    silent: bool


def into_send_kwargs(kwargs: MessageKwargs) -> _SendHelperKWType:
    """Build kwargs accepted by send endpoints.

    Send and edit APIs intentionally use separate converters: Discord names uploaded files differently for edits, and
    options such as TTS and silent delivery do not belong to the edit path.
    """
    kw: _SendHelperKWType = {}
    if 'content' in kwargs:
        kw['content'] = kwargs['content']
    if 'tts' in kwargs:
        kw['tts'] = kwargs['tts']
    if 'embeds' in kwargs:
        kw['embeds'] = kwargs['embeds']
    if 'files' in kwargs:
        kw['files'] = kwargs['files']
    if 'allowed_mentions' in kwargs:
        kw['allowed_mentions'] = kwargs['allowed_mentions']
    if 'view' in kwargs:
        kw['view'] = kwargs['view']
    if 'suppress_embeds' in kwargs:
        kw['suppress_embeds'] = kwargs['suppress_embeds']
    if 'silent' in kwargs:
        kw['silent'] = kwargs['silent']
    return kw


class _EditKWType(TypedDict, total=False):
    content: str | None
    embeds: Sequence[Embed]
    attachments: Sequence[Attachment | File]
    allowed_mentions: AllowedMentions
    view: LayoutView | View


def into_edit_kwargs(kwargs: MessageKwargs, *, components_v2: bool = False) -> _EditKWType:
    """Build kwargs accepted by message edit endpoints.

    Switching an existing legacy message to Component V2 requires clearing content and embeds in the same edit. Files
    also become the edit API's ``attachments`` argument, which is why send kwargs cannot be reused here.
    """
    kw: _EditKWType = {}
    if 'content' in kwargs:
        kw['content'] = kwargs['content']
    if 'embeds' in kwargs:
        kw['embeds'] = kwargs['embeds']
    if 'files' in kwargs:
        kw['attachments'] = kwargs['files']
    if 'allowed_mentions' in kwargs:
        kw['allowed_mentions'] = kwargs['allowed_mentions']
    if 'view' in kwargs:
        kw['view'] = kwargs['view']
    if components_v2:
        kw['content'] = None
        kw['embeds'] = ()
    return kw


async def _try_edit_interaction_message(
    interaction: Interaction,
    message: ComponentV2Message | LegacyMessage,
    kwargs: MessageKwargs,
) -> DiscordMessage | None:
    """Try to acknowledge an interaction by editing the message that triggered it.

    This path is preferred for ``edit_original`` because it performs the edit as the interaction's initial response.
    Only Discord API failures fall back to another editable target or a send; programming errors still propagate.
    """
    if interaction.response.is_done() or interaction.message is None:
        return None
    try:
        await interaction.response.edit_message(
            **into_edit_kwargs(
                kwargs,
                components_v2=isinstance(message, ComponentV2Message),
            )
        )
    except DiscordException:
        return None
    return await interaction.original_response()


async def _edit_existing_message(
    editable: _Editable,
    message: ComponentV2Message | LegacyMessage,
    kwargs: MessageKwargs,
) -> _Editable:
    """Edit the known active message without fetching it first."""
    return await editable.edit(
        **into_edit_kwargs(
            kwargs,
            components_v2=isinstance(message, ComponentV2Message),
        )
    )


async def _send_initial_interaction_response(
    interaction: Interaction,
    kwargs: MessageKwargs,
    *,
    ephemeral: bool,
) -> DiscordMessage:
    """Acknowledge an unanswered interaction, then retrieve its editable response message."""
    await interaction.response.send_message(
        ephemeral=ephemeral,
        **into_send_kwargs(kwargs),  # type: ignore[reportArgumentType, arg-type]
    )
    return await interaction.original_response()


async def _send_after_interaction_response(
    interaction: Interaction,
    message: ComponentV2Message | LegacyMessage,
    kwargs: MessageKwargs,
    *,
    ephemeral: bool,
) -> DiscordMessage:
    """Send through an interaction that has already been acknowledged.

    Ordinary acknowledged interactions use a follow-up. A deferred channel response receiving Component V2 content
    must instead complete the deferred original response by editing it; this also retains the editable interaction
    message used by ephemeral responses.
    """
    if (
        isinstance(message, ComponentV2Message)
        and interaction.response.type is InteractionResponseType.deferred_channel_message
    ):
        return await interaction.edit_original_response(**into_edit_kwargs(kwargs, components_v2=True))
    return await interaction.followup.send(  # type: ignore[no-any-return]
        wait=True,
        ephemeral=ephemeral,
        **into_send_kwargs(kwargs),  # type: ignore[reportArgumentType, call-overload]
    )


async def _send_to_messageable(
    messageable: Messageable,
    kwargs: MessageKwargs,
    *,
    delete_after: float | None,
) -> DiscordMessage:
    """Send through a normal Messageable and delegate its deletion timer to discord.py."""
    return await messageable.send(  # type: ignore[no-any-return]
        delete_after=delete_after,  # type: ignore[reportArgumentType, arg-type]
        **into_send_kwargs(kwargs),  # type: ignore[reportArgumentType, arg-type]
    )


async def _schedule_delete(message: DiscordMessage, delete_after: float | None) -> None:
    """Schedule deletion for an interaction response when requested.

    Messageable sends accept ``delete_after`` directly, while interaction response and follow-up paths require the
    timer to be attached to the returned message. Edit paths intentionally do not schedule deletion.
    """
    if delete_after is not None:
        await message.delete(delay=delete_after)


async def send_helper(
    messageable: Sendable,
    message: ComponentV2Message | LegacyMessage,
    view: _ViewType | None,
    edit: _Editable | None,
) -> _Editable:
    """Send or edit a flow message and return the actual editable Discord message.

    ``edit_original`` first tries an unacknowledged interaction edit, then the known active message, and finally falls
    back to sending. Whether an interaction has already been acknowledged determines whether that send is an initial
    response, a follow-up, or completion of a deferred Component V2 response. ``delete_after`` applies only when a new
    message is sent, matching Discord's separate send and edit APIs.
    """
    kwargs = message._to_dict()
    if view is not None:
        kwargs['view'] = view

    if message.edit_original:
        if isinstance(messageable, Interaction):
            interaction_message = await _try_edit_interaction_message(messageable, message, kwargs)
            if interaction_message is not None:
                return interaction_message  # type: ignore[reportReturnType, return-value]
        if edit is not None:
            return await _edit_existing_message(edit, message, kwargs)

    delete_after = kwargs.get('delete_after', None)
    ephemeral = kwargs.get('ephemeral', False)
    if isinstance(messageable, Interaction):
        if messageable.response.is_done():
            msg = await _send_after_interaction_response(messageable, message, kwargs, ephemeral=ephemeral)
        else:
            msg = await _send_initial_interaction_response(messageable, kwargs, ephemeral=ephemeral)
        await _schedule_delete(msg, delete_after)
        return msg  # type: ignore[reportReturnType, return-value]
    return await _send_to_messageable(messageable, kwargs, delete_after=delete_after)  # type: ignore[reportReturnType, return-value]


async def force_cancel_tasks(tasks: Iterable[Task[Any]]) -> None:
    """Force cancel all tasks.

    Args:
        tasks (Iterable[Task[T]]): Tasks to cancel.
    """
    task_list = tuple(tasks)
    for task in task_list:
        task.cancel()
    await gather(*task_list, return_exceptions=True)
