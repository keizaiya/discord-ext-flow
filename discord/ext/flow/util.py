from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from discord import Interaction

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TypeGuard, TypeVar, Unpack

    from discord import (
        AllowedMentions,
        Client,
        Embed,
        File,
        Message,
    )
    from discord.abc import Messageable
    from discord.ui import View

    T = TypeVar('T')
    U = TypeVar('U')


def unwrap_or(value: T | None, default: U) -> T | U:
    """Return value if value is not None, otherwise return default."""
    if value is None:
        return default
    return value


class _SendKwType(TypedDict, total=False):
    content: str
    tts: bool
    embeds: Sequence[Embed]
    files: Sequence[File]
    allowed_mentions: AllowedMentions
    view: View
    ephemeral: bool
    suppress_embeds: bool
    silent: bool


class _MessageableSendKwType(TypedDict, total=False):
    content: str
    tts: bool
    embeds: Sequence[Embed]
    files: Sequence[File]
    allowed_mentions: AllowedMentions
    view: View
    suppress_embeds: bool
    silent: bool
    delete_after: float


def _is_messageable_kw(kwargs: _SendKwType) -> TypeGuard[_MessageableSendKwType]:
    kwargs.pop('ephemeral', None)
    return True


async def sender(
    messageable: Messageable | Interaction[Client],
    *,
    delete_after: float | None = None,
    **kwargs: Unpack[_SendKwType],
) -> Message:
    """Helper function to send message. use messageable or interaction."""
    if isinstance(messageable, Interaction):
        msg: Message
        if messageable.response.is_done():
            msg = await messageable.followup.send(**kwargs, wait=True)
        else:
            await messageable.response.send_message(**kwargs)
            msg = await messageable.original_response()

        if delete_after is not None:
            await msg.delete(delay=delete_after)

        return msg
    # if Messageable
    kw: _SendKwType = {**kwargs}
    if _is_messageable_kw(kw):
        kwargs_: _MessageableSendKwType = kw
        if delete_after is not None:
            kwargs_['delete_after'] = delete_after
        return await messageable.send(**kwargs_)

    raise AssertionError('unreachable')
