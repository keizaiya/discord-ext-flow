from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from discord import Interaction

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TypeVar, Unpack

    from discord import AllowedMentions, Attachment, Client, Embed, File, Message
    from discord.abc import Messageable
    from discord.ui import View

    from .model import MessageKwargs

    T = TypeVar('T')
    U = TypeVar('U')


def unwrap_or(value: T | None, default: U) -> T | U:
    """Return value if value is not None, otherwise return default."""
    if value is None:
        return default
    return value


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
) -> Message:
    """Helper function to send message. use messageable or interaction."""
    if isinstance(messageable, Interaction):
        msg: Message
        if messageable.response.is_done():
            msg = await messageable.followup.send(wait=True, ephemeral=ephemeral, **kwargs)
        else:
            await messageable.response.send_message(ephemeral=ephemeral, **kwargs)
            msg = await messageable.original_response()

        if delete_after is not None:
            await msg.delete(delay=delete_after)

        return msg

    # type-ignore: can pass None to delete_after
    return await messageable.send(delete_after=delete_after, **kwargs)  # type: ignore[reportArgumentType, arg-type]


class _EditKWType(TypedDict, total=False):
    content: str
    embeds: Sequence[Embed]
    attachments: Sequence[Attachment | File]
    suppress: bool
    delete_after: float
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
    if 'suppress_embeds' in kwargs:
        kw['suppress'] = kwargs['suppress_embeds']
    if 'delete_after' in kwargs:
        kw['delete_after'] = kwargs['delete_after']
    if 'allowed_mentions' in kwargs:
        kw['allowed_mentions'] = kwargs['allowed_mentions']
    if 'view' in kwargs:
        kw['view'] = kwargs['view']
    return kw
