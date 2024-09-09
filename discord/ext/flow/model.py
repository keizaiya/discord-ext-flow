from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from discord import ButtonStyle

__all__ = (
    'Message',
    'Button',
    'Link',
    'Select',
    'UserSelect',
    'RoleSelect',
    'MentionableSelect',
    'ChannelSelect',
    'ModelBase',
)


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Any, TypeAlias, TypedDict

    from discord import (
        AllowedMentions,
        ChannelType,
        Client,
        ClientUser,
        Embed,
        Emoji,
        File,
        Interaction,
        Member,
        Object,
        PartialEmoji,
        Role,
        SelectDefaultValue,
        SelectOption,
        Thread,
        User,
    )
    from discord.abc import GuildChannel
    from discord.app_commands import AppCommandChannel, AppCommandThread
    from discord.ui import View
    from discord.utils import MaybeAwaitable

    from .result import Result

    __all__ += ('ViewConfig', 'ItemType')  # type: ignore[reportUnsupportedDunderAll, assignment]

    class ViewConfig(TypedDict, total=False):
        """Config for View.

        timeout: View timeout in seconds. If None, use default timeout. see discord.ui.View for more info.
        """

        timeout: float | None

    class MessageKwargs(TypedDict, total=False):
        content: str
        tts: bool
        embeds: Sequence[Embed]
        files: Sequence[File]
        delete_after: float
        allowed_mentions: AllowedMentions
        view: View
        suppress_embeds: bool
        ephemeral: bool
        silent: bool

    # copied from discord.ui.select
    ValidDefaultValues = (
        SelectDefaultValue
        | Object
        | Role
        | Member
        | ClientUser
        | User
        | GuildChannel
        | AppCommandChannel
        | AppCommandThread
        | Thread
    )


class Message(NamedTuple):
    """A message to send to Discord. see discord.Messageable.send for more info.

    Note:
        - `items` is a Sequence of `Button`, `Select`, or etc. if None or not set, send message and stop flow.
        - `view` will set by this lib. you can not set it.
        - `embed`, `file`, or `sticker` is not support. use `embeds`, `files`, or `stickers` instead.
        - `reference` is not support in Interaction.
        - `edit_original` will edit original message if True.
        - `disable_items` will disable all items when call after_invoke.
    """

    content: str | None = None
    items: Sequence[ItemType] | None = None
    tts: bool = False
    embeds: Sequence[Embed] | None = None
    files: Sequence[File] | None = None
    delete_after: float | None = None
    allowed_mentions: AllowedMentions | None = None
    suppress_embeds: bool = False
    ephemeral: bool = False
    silent: bool = False

    edit_original: bool = False
    disable_items: bool = False

    def _to_dict(self) -> MessageKwargs:
        d: MessageKwargs = {
            'tts': self.tts,
            'suppress_embeds': self.suppress_embeds,
            'ephemeral': self.ephemeral,
            'silent': self.silent,
        }
        if self.content is not None:
            d['content'] = self.content
        if self.embeds is not None:
            d['embeds'] = self.embeds
        if self.files is not None:
            d['files'] = self.files
        if self.delete_after is not None:
            d['delete_after'] = self.delete_after
        if self.allowed_mentions is not None:
            d['allowed_mentions'] = self.allowed_mentions
        return d


@dataclass
class Button:
    """discord.ui.Button with callback for Message.items.

    Note:
        - you should use Link instead of this if you want to send link.
    """

    callback: Callable[[Interaction[Client]], MaybeAwaitable[Result]]
    label: str | None = None
    custom_id: str | None = None
    disabled: bool = False
    style: ButtonStyle = ButtonStyle.secondary
    emoji: str | Emoji | PartialEmoji | None = None
    row: int | None = None


@dataclass
class Link:
    """discord.ui.Button for link with callback for Message.items."""

    label: str | None = None
    disabled: bool = False
    emoji: str | Emoji | PartialEmoji | None = None
    row: int | None = None


@dataclass
class Select:
    """discord.ui.Select with callback for Message.items.

    Note:
        - options is keyword only argument.
    """

    callback: Callable[[Interaction[Client], list[str]], MaybeAwaitable[Result]]
    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    row: int | None = None
    options: Sequence[SelectOption] | None = None


@dataclass
class UserSelect:
    """discord.ui.UserSelect with callback for Message.items."""

    callback: Callable[[Interaction[Client], list[User | Member]], MaybeAwaitable[Result]]
    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    row: int | None = None
    default_values: Sequence[ValidDefaultValues] | None = None


@dataclass
class RoleSelect:
    """discord.ui.RoleSelect with callback for Message.items."""

    callback: Callable[[Interaction[Client], list[Role]], MaybeAwaitable[Result]]
    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    row: int | None = None
    default_values: Sequence[ValidDefaultValues] | None = None


@dataclass
class MentionableSelect:
    """discord.ui.MentionableSelect with callback for Message.items."""

    callback: Callable[[Interaction[Client], list[User | Member | Role]], MaybeAwaitable[Result]]
    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    row: int | None = None
    default_values: Sequence[ValidDefaultValues] | None = None


@dataclass
class ChannelSelect:
    """discord.ui.ChannelSelect with callback for Message.items."""

    callback: Callable[[Interaction[Client], list[AppCommandChannel | AppCommandThread]], MaybeAwaitable[Result]]
    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    row: int | None = None
    channel_types: Sequence[ChannelType] | None = field(default=None, kw_only=True)
    default_values: Sequence[ValidDefaultValues] | None = None


if TYPE_CHECKING:
    ItemType: TypeAlias = Button | Link | Select | UserSelect | RoleSelect | MentionableSelect | ChannelSelect


class ModelBase:
    """The base class that all models must inherit from."""

    def before_invoke(self) -> MaybeAwaitable[Any]:
        """This method is called before sending message."""
        return None

    def view_config(self) -> MaybeAwaitable[ViewConfig]:
        """This method is called before creating view."""
        return {}

    def message(self) -> MaybeAwaitable[Message]:
        """Create message to send."""
        raise NotImplementedError

    def after_invoke(self) -> MaybeAwaitable[Any]:
        """This method is called after sending message."""
        return None
