from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple, overload

from discord import ButtonStyle, SeparatorSpacing

__all__ = (
    'ActionRow',
    'Button',
    'ChannelSelect',
    'ComponentV2Message',
    'Container',
    'FileDisplay',
    'LegacyMessage',
    'Link',
    'MediaGallery',
    'MentionableSelect',
    'Message',
    'ModelBase',
    'RoleSelect',
    'Section',
    'Select',
    'Separator',
    'TextDisplay',
    'Thumbnail',
    'UserSelect',
    'create_message',
)


if TYPE_CHECKING:
    import sys
    from collections.abc import Sequence
    from typing import Any, TypedDict

    from discord import (
        AllowedMentions,
        ChannelType,
        ClientUser,
        Colour,
        Embed,
        Emoji,
        File,
        Interaction,
        MediaGalleryItem,
        Member,
        Object,
        PartialEmoji,
        Poll,
        Role,
        SelectDefaultValue,
        SelectOption,
        Thread,
        UnfurledMediaItem,
        User,
    )
    from discord.abc import GuildChannel
    from discord.app_commands import AppCommandChannel, AppCommandThread
    from discord.ui import LayoutView, View
    from discord.utils import MaybeAwaitable, MaybeAwaitableFunc

    from .result import Result

    if sys.version_info < (3, 13):
        from typing_extensions import TypeIs
    else:
        from typing import TypeIs

    __all__ += (  # type: ignore[reportUnsupportedDunderAll, assignment]
        'ActionRowItemType',
        'ContainerItemType',
        'CreateItemType',
        'ItemType',
        'LegacyItemType',
        'V2ItemType',
        'ValidDefaultValues',
        'ViewConfig',
    )

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
        view: LayoutView | View
        suppress_embeds: bool
        ephemeral: bool
        silent: bool
        poll: Poll

    # copied from discord.ui.select
    type ValidDefaultValues = (
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
    """A legacy message to send to Discord. See discord.Messageable.send for more info.

    Note:
        - `items` is a Sequence of `Button`, `Select`, or etc. if None or not set, send message and stop flow.
        - `view` will set by this lib. you can not set it.
        - `embed`, `file`, or `sticker` is not support. use `embeds`, `files`, or `stickers` instead.
        - `reference` is not support in Interaction.
        - `edit_original` will edit original message if True.
        - `disable_items` will disable all items when call after_invoke.
    """

    content: str | None = None
    items: Sequence[LegacyItemType] | None = None
    tts: bool = False
    embeds: Sequence[Embed] | None = None
    files: Sequence[File] | None = None
    delete_after: float | None = None
    allowed_mentions: AllowedMentions | None = None
    suppress_embeds: bool = False
    ephemeral: bool = False
    silent: bool = False
    poll: Poll | None = None

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
        if self.poll is not None:
            d['poll'] = self.poll
        return d


LegacyMessage = Message


class ComponentV2Message(NamedTuple):
    """A Component V2 message with fields valid for the IS_COMPONENTS_V2 flag.

    Note:
        - `items` is a required Sequence of Component V2 items.
        - `view` will set by this lib. you can not set it.
        - `content`, `tts`, `embeds`, `poll`, and `suppress_embeds` are not supported.
        - `reference` is not support in Interaction.
        - `edit_original` will edit original message if True.
        - `disable_items` will disable all items when call after_invoke.
    """

    items: Sequence[V2ItemType]
    files: Sequence[File] | None = None
    delete_after: float | None = None
    allowed_mentions: AllowedMentions | None = None
    ephemeral: bool = False
    silent: bool = False
    edit_original: bool = False
    disable_items: bool = False

    def _to_dict(self) -> MessageKwargs:
        d: MessageKwargs = {'ephemeral': self.ephemeral, 'silent': self.silent}
        if self.files is not None:
            d['files'] = self.files
        if self.delete_after is not None:
            d['delete_after'] = self.delete_after
        if self.allowed_mentions is not None:
            d['allowed_mentions'] = self.allowed_mentions
        return d


@overload
def create_message(
    *,
    content: str | None = None,
    items: None = None,
    tts: bool = False,
    embeds: Sequence[Embed] | None = None,
    files: Sequence[File] | None = None,
    delete_after: float | None = None,
    allowed_mentions: AllowedMentions | None = None,
    suppress_embeds: bool = False,
    ephemeral: bool = False,
    silent: bool = False,
    poll: Poll | None = None,
    edit_original: bool = False,
    disable_items: bool = False,
) -> LegacyMessage: ...


@overload
def create_message(
    *,
    content: str | None = None,
    items: Sequence[LegacyItemType],
    tts: bool = False,
    embeds: Sequence[Embed] | None = None,
    files: Sequence[File] | None = None,
    delete_after: float | None = None,
    allowed_mentions: AllowedMentions | None = None,
    suppress_embeds: bool = False,
    ephemeral: bool = False,
    silent: bool = False,
    poll: Poll | None = None,
    edit_original: bool = False,
    disable_items: bool = False,
) -> LegacyMessage: ...


@overload
def create_message(
    *,
    content: str | None = None,
    items: Sequence[V2ItemType],
    tts: bool = False,
    embeds: Sequence[Embed] | None = None,
    files: Sequence[File] | None = None,
    delete_after: float | None = None,
    allowed_mentions: AllowedMentions | None = None,
    suppress_embeds: bool = False,
    ephemeral: bool = False,
    silent: bool = False,
    poll: Poll | None = None,
    edit_original: bool = False,
    disable_items: bool = False,
) -> ComponentV2Message: ...


@overload
def create_message(
    *,
    content: str | None = None,
    items: Sequence[CreateItemType],
    tts: bool = False,
    embeds: Sequence[Embed] | None = None,
    files: Sequence[File] | None = None,
    delete_after: float | None = None,
    allowed_mentions: AllowedMentions | None = None,
    suppress_embeds: bool = False,
    ephemeral: bool = False,
    silent: bool = False,
    poll: Poll | None = None,
    edit_original: bool = False,
    disable_items: bool = False,
) -> ComponentV2Message | LegacyMessage: ...


def create_message(  # noqa: PLR0913
    *,
    content: str | None = None,
    items: Sequence[CreateItemType] | None = None,
    tts: bool = False,
    embeds: Sequence[Embed] | None = None,
    files: Sequence[File] | None = None,
    delete_after: float | None = None,
    allowed_mentions: AllowedMentions | None = None,
    suppress_embeds: bool = False,
    ephemeral: bool = False,
    silent: bool = False,
    poll: Poll | None = None,
    edit_original: bool = False,
    disable_items: bool = False,
) -> ComponentV2Message | LegacyMessage:
    """Create a V2 or legacy message according to the supplied component items."""
    from typing import cast  # noqa: PLC0415

    uses_v2 = items is not None and any(_is_v2_top_level_item(item) for item in items)
    if uses_v2:
        if items is not None and not all(_is_v2_top_level_item(item) for item in items):
            raise ValueError('Component V2 top-level items cannot be mixed with legacy top-level items.')
        incompatible_fields = [
            name
            for name, used in (
                ('content', content is not None),
                ('embeds', embeds is not None),
                ('poll', poll is not None),
            )
            if used
        ]
        if tts:
            incompatible_fields.append('tts')
        if suppress_embeds:
            incompatible_fields.append('suppress_embeds')
        if incompatible_fields:
            fields = ', '.join(incompatible_fields)
            raise ValueError(f'Component V2 messages cannot use these fields: {fields}')
        assert items is not None
        return ComponentV2Message(
            items=cast('Sequence[V2ItemType]', items),
            files=files,
            delete_after=delete_after,
            allowed_mentions=allowed_mentions,
            ephemeral=ephemeral,
            silent=silent,
            edit_original=edit_original,
            disable_items=disable_items,
        )
    return LegacyMessage(
        content=content,
        items=cast('Sequence[LegacyItemType] | None', items),
        tts=tts,
        embeds=embeds,
        files=files,
        delete_after=delete_after,
        allowed_mentions=allowed_mentions,
        suppress_embeds=suppress_embeds,
        ephemeral=ephemeral,
        silent=silent,
        poll=poll,
        edit_original=edit_original,
        disable_items=disable_items,
    )


@dataclass
class Button:
    """discord.ui.Button with callback for Message.items.

    Note:
        - you should use Link instead of this if you want to send link.
    """

    callback: MaybeAwaitableFunc[[Interaction], Result]
    label: str | None = None
    custom_id: str | None = None
    disabled: bool = False
    style: ButtonStyle = ButtonStyle.secondary
    emoji: str | Emoji | PartialEmoji | None = None
    row: int | None = None


@dataclass
class Link:
    """discord.ui.Button for link without callback for Message.items."""

    label: str | None = None
    disabled: bool = False
    emoji: str | Emoji | PartialEmoji | None = None
    row: int | None = None
    url: str | None = None


@dataclass
class Select:
    """discord.ui.Select with callback for Message.items.

    Note:
        - options is keyword only argument.
    """

    callback: MaybeAwaitableFunc[[Interaction, list[str]], Result]
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

    callback: MaybeAwaitableFunc[[Interaction, list[User | Member]], Result]
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

    callback: MaybeAwaitableFunc[[Interaction, list[Role]], Result]
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

    callback: MaybeAwaitableFunc[[Interaction, list[User | Member | Role]], Result]
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

    callback: MaybeAwaitableFunc[[Interaction, list[AppCommandChannel | AppCommandThread]], Result]
    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    row: int | None = None
    channel_types: Sequence[ChannelType] | None = field(default=None, kw_only=True)
    default_values: Sequence[ValidDefaultValues] | None = None


@dataclass
class TextDisplay:
    """discord.ui.TextDisplay for ComponentV2Message.items."""

    content: str
    id: int | None = None


@dataclass
class Thumbnail:
    """discord.ui.Thumbnail for Section.accessory."""

    media: str | File | UnfurledMediaItem
    description: str | None = None
    spoiler: bool = False
    id: int | None = None


@dataclass
class MediaGallery:
    """discord.ui.MediaGallery for ComponentV2Message.items."""

    items: Sequence[MediaGalleryItem]
    id: int | None = None


@dataclass
class FileDisplay:
    """discord.ui.File for ComponentV2Message.items."""

    media: str | UnfurledMediaItem | File
    spoiler: bool = False
    id: int | None = None


@dataclass
class Separator:
    """discord.ui.Separator for ComponentV2Message.items."""

    visible: bool = True
    spacing: SeparatorSpacing = SeparatorSpacing.small
    id: int | None = None


@dataclass
class ActionRow:
    """discord.ui.ActionRow for ComponentV2Message.items."""

    items: Sequence[ActionRowItemType]
    id: int | None = None


@dataclass
class Section:
    """discord.ui.Section for ComponentV2Message.items."""

    items: Sequence[TextDisplay | str]
    accessory: Thumbnail | Button | Link
    id: int | None = None


@dataclass
class Container:
    """discord.ui.Container for ComponentV2Message.items."""

    items: Sequence[ContainerItemType]
    accent_color: Colour | int | None = None
    spoiler: bool = False
    id: int | None = None


_V2_TOP_LEVEL_TYPES = (ActionRow, Container, FileDisplay, MediaGallery, Section, Separator, TextDisplay)


def _is_v2_top_level_item(item: object) -> bool:
    """Return whether an item requires a Component V2 message and LayoutView."""
    return isinstance(item, _V2_TOP_LEVEL_TYPES)


def is_sequence_v2_item(items: Sequence[object]) -> TypeIs[Sequence[V2ItemType]]:
    """Check any items are Component V2 Item."""
    return any(_is_v2_top_level_item(item) for item in items)


if TYPE_CHECKING:
    type LegacyItemType = Button | Link | Select | UserSelect | RoleSelect | MentionableSelect | ChannelSelect
    type ActionRowItemType = LegacyItemType
    type ContainerItemType = ActionRow | Section | TextDisplay | MediaGallery | FileDisplay | Separator
    type V2ItemType = ActionRow | Section | TextDisplay | MediaGallery | FileDisplay | Separator | Container
    type CreateItemType = LegacyItemType | V2ItemType
    type ItemType = CreateItemType


class ModelBase:
    """The base class that all models must inherit from."""

    def before_invoke(self) -> MaybeAwaitable[Any]:
        """This method is called before sending message."""
        return None

    def view_config(self) -> MaybeAwaitable[ViewConfig]:
        """This method is called before creating view."""
        return {}

    def message(self) -> MaybeAwaitable[ComponentV2Message | LegacyMessage]:
        """Create message to send."""
        raise NotImplementedError

    def after_invoke(self) -> MaybeAwaitable[Any]:
        """This method is called after sending message."""
        return None
