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


def items_can_produce_result(items: Sequence[ItemType]) -> bool:
    """Return whether configured items contain an enabled flow callback."""
    return any(
        (
            (
                # interactive item and correct type and enabled.
                isinstance(item, InteractiveItem)
                and isinstance(item.item, (Button, Select, UserSelect, RoleSelect, MentionableSelect, ChannelSelect))
                and not item.item.disabled
            )
            or (
                # item is container like, and any children are enabled flow callback.
                isinstance(item, (ActionRow, Container)) and items_can_produce_result(item.items)
            )
            or (
                # item have a interactive accessory and enabled.
                isinstance(item, Section)
                and isinstance(item.accessory, InteractiveItem)
                and isinstance(item.accessory.item, Button)
                and not item.accessory.item.disabled
            )
        )
        for item in items
    )


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
    """Convert MessageKwargs to send kwargs type."""
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
    """Convert MessageKwargs to Message.edit kwargs type."""
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


async def _send_after_interaction_response(
    interaction: Interaction,
    message: ComponentV2Message | LegacyMessage,
    kwargs: MessageKwargs,
    *,
    ephemeral: bool,
) -> DiscordMessage:
    """Send after an interaction response while respecting deferred V2 rules."""
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


async def send_helper(
    messageable: Sendable,
    message: ComponentV2Message | LegacyMessage,
    view: _ViewType | None,
    edit: _Editable | None,
) -> _Editable:
    """Send or edit a flow message and return the actual Discord message."""
    kwargs = message._to_dict()
    if view is not None:
        kwargs['view'] = view
    msg: DiscordMessage

    # if edit
    if message.edit_original:
        if (
            isinstance(messageable, Interaction)
            and not messageable.response.is_done()
            and messageable.message is not None
        ):  # Interaction.message is not None -> can edit
            try:
                await messageable.response.edit_message(
                    **into_edit_kwargs(
                        kwargs,
                        components_v2=isinstance(message, ComponentV2Message),
                    )
                )
            except DiscordException:
                pass  # ignore. and fallback.
            else:
                interaction_msg = await messageable.original_response()
                msg = interaction_msg
                return msg  # type: ignore[reportReturnType, return-value]
        if edit is not None:
            return await edit.edit(
                **into_edit_kwargs(
                    kwargs,
                    components_v2=isinstance(message, ComponentV2Message),
                )
            )
        # fallback to send message

    # if send
    delete_after = kwargs.get('delete_after', None)
    ephemeral = kwargs.get('ephemeral', False)
    if isinstance(messageable, Interaction):
        if messageable.response.is_done():
            msg = await _send_after_interaction_response(messageable, message, kwargs, ephemeral=ephemeral)
        else:
            await messageable.response.send_message(
                ephemeral=ephemeral,
                **into_send_kwargs(kwargs),  # type: ignore[reportArgumentType, arg-type]
            )
            msg = await messageable.original_response()

        if delete_after is not None:
            await msg.delete(delay=delete_after)
    else:
        # type-ignore: can pass None to delete_after
        msg = await messageable.send(delete_after=delete_after, **into_send_kwargs(kwargs))  # type: ignore[reportArgumentType, arg-type]
    # type-ignore: return type is Message, InteractionMessage or WebhookMessage, which are also _Editable
    return msg  # type: ignore[reportReturnType, return-value]


async def force_cancel_tasks(tasks: Iterable[Task[Any]]) -> None:
    """Force cancel all tasks.

    Args:
        tasks (Iterable[Task[T]]): Tasks to cancel.
    """
    task_list = tuple(tasks)
    for task in task_list:
        task.cancel()
    await gather(*task_list, return_exceptions=True)
