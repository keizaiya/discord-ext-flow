from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypedDict

from discord import Interaction, Message

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Self, TypeVar

    from discord import AllowedMentions, Attachment, Client, Embed, File
    from discord.abc import Messageable
    from discord.ui import View

    from .model import Message as MessageData, MessageKwargs
    from .view import _View

    T = TypeVar('T')
    U = TypeVar('U')


def unwrap_or(value: T | None, default: U) -> T | U:
    """Return value if value is not None, otherwise return default."""
    if value is None:
        return default
    return value


class _Editable(Protocol):
    async def edit(  # noqa: PLR0913
        self,
        *,
        content: str | None = None,
        embeds: Sequence[Embed] | None = None,
        attachments: Sequence[Attachment | File] | None = None,
        view: View | None = None,
        allowed_mentions: AllowedMentions | None = None,
    ) -> Self:
        """Message.edit, InteractionMessage.edit or WebhookMessage.edit."""
        ...


class _SendHelperKWType(TypedDict, total=False):
    content: str
    tts: bool
    embeds: Sequence[Embed]
    files: Sequence[File]
    allowed_mentions: AllowedMentions
    view: View
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
    content: str
    embeds: Sequence[Embed]
    attachments: Sequence[Attachment | File]
    allowed_mentions: AllowedMentions
    view: View


def into_edit_kwargs(kwargs: MessageKwargs) -> _EditKWType:
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
    return kw


async def send_helper(
    messageable: Messageable | Interaction[Client],
    message: MessageData,
    view: _View | None,
    edit_target: _Editable | None,
) -> _Editable:
    """Helper function to send message. use messageable or interaction."""
    kwargs = message._to_dict()
    if view is not None:
        kwargs['view'] = view

    # if edit
    if message.edit_original:
        if isinstance(messageable, Interaction) and not messageable.response.is_done():
            if messageable.message is not None:  # Interaction.message is not None -> can edit
                await messageable.response.edit_message(**into_edit_kwargs(kwargs))
                return await messageable.original_response()  # type: ignore[reportReturnType, return-value]
        elif edit_target is not None:
            return await edit_target.edit(**into_edit_kwargs(kwargs))
        # fallback to send message

    # if send
    msg: Message
    delete_after = kwargs.get('delete_after', None)
    ephemeral = kwargs.get('ephemeral', False)
    kwargs = into_send_kwargs(kwargs)
    if isinstance(messageable, Interaction):
        if messageable.response.is_done():
            msg = await messageable.followup.send(wait=True, ephemeral=ephemeral, **kwargs)
        else:
            await messageable.response.send_message(ephemeral=ephemeral, **kwargs)
            msg = await messageable.original_response()

        if delete_after is not None:
            await msg.delete(delay=delete_after)
    else:
        # type-ignore: can pass None to delete_after
        msg = await messageable.send(delete_after=delete_after, **kwargs)  # type: ignore[reportArgumentType, arg-type]
    # type-ignore: return type is Message, InteractionMessage or WebhookMessage, which are also _Editable
    return msg  # type: ignore[reportReturnType, return-value]
