from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypedDict

from discord import Interaction, Message

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Self, TypeVar, Unpack

    from discord import AllowedMentions, Attachment, Client, Embed, File
    from discord.abc import Messageable
    from discord.ui import View

    from .model import MessageKwargs

    T = TypeVar('T')
    U = TypeVar('U')
    V = TypeVar('V')


def unwrap_or(value: T | None, default: U) -> T | U:
    """Return value if value is not None, otherwise return default."""
    if value is None:
        return default
    return value


def map_or(value: T | None, default: U, func: Callable[[T], V]) -> V | U:
    """Return func(value) if value is not None, otherwise return default."""
    if value is None:
        return default
    return func(value)


class _Editable(Protocol):
    async def edit(
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


async def send_helper(
    messageable: Messageable | Interaction[Client],
    *,
    delete_after: float | None = None,
    ephemeral: bool = False,
    **kwargs: Unpack[_SendHelperKWType],
) -> _Editable:
    """Helper function to send message. use messageable or interaction."""
    msg: Message
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


class _EditKWType(TypedDict, total=False):
    content: str
    embeds: Sequence[Embed]
    attachments: Sequence[Attachment | File]
    allowed_mentions: AllowedMentions
    view: View


def into_edit_kwargs(**kwargs: Unpack[MessageKwargs]) -> _EditKWType:
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
