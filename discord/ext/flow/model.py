from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, overload

from .item import is_sequence_all_v2_item, is_sequence_legacy_item, is_sequence_v2_item

__all__ = (
    'ComponentV2Message',
    'LegacyMessage',
    'Message',
    'ModelBase',
    'create_message',
)


if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any, Literal, TypedDict

    from discord import AllowedMentions, Embed, File as SendableFile, Poll
    from discord.ui import LayoutView, View
    from discord.utils import MaybeAwaitable

    from .item import CreateItemType, ItemType as _ItemType, LegacyItemType, V2ItemType

    type ItemType = _ItemType

    __all__ += (  # type: ignore[reportUnsupportedDunderAll, assignment]
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
        files: Sequence[SendableFile]
        delete_after: float
        allowed_mentions: AllowedMentions
        view: LayoutView | View
        suppress_embeds: bool
        ephemeral: bool
        silent: bool
        poll: Poll


class Message(NamedTuple):
    """A legacy message to send to Discord. See discord.Messageable.send for more info.

    Note:
        - `items` is a Sequence of `Button`, `Select`, or etc. if None or not set, send message and stop flow.
        - `view` will set by this lib. you can not set it.
        - `embed`, `file`, or `sticker` is not support. use `embeds`, `files`, or `stickers` instead.
        - `reference` is not support in Interaction.
        - `edit_original` will edit original message if True.
        - `disable_items` disables all items when this message stops being the active flow message.
    """

    content: str | None = None
    items: Sequence[LegacyItemType] | None = None
    tts: bool = False
    embeds: Sequence[Embed] | None = None
    files: Sequence[SendableFile] | None = None
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
        - `disable_items` disables all items when this message stops being the active flow message.
    """

    items: Sequence[V2ItemType]
    files: Sequence[SendableFile] | None = None
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
    files: Sequence[SendableFile] | None = None,
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
    files: Sequence[SendableFile] | None = None,
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
    content: None = None,
    items: Sequence[V2ItemType],
    tts: Literal[False] = False,
    embeds: None = None,
    files: Sequence[SendableFile] | None = None,
    delete_after: float | None = None,
    allowed_mentions: AllowedMentions | None = None,
    suppress_embeds: Literal[False] = False,
    ephemeral: bool = False,
    silent: bool = False,
    poll: None = None,
    edit_original: bool = False,
    disable_items: bool = False,
) -> ComponentV2Message: ...


@overload
def create_message(
    *,
    content: None = None,
    items: Sequence[CreateItemType],
    tts: Literal[False] = False,
    embeds: None = None,
    files: Sequence[SendableFile] | None = None,
    delete_after: float | None = None,
    allowed_mentions: AllowedMentions | None = None,
    suppress_embeds: Literal[False] = False,
    ephemeral: bool = False,
    silent: bool = False,
    poll: None = None,
    edit_original: bool = False,
    disable_items: bool = False,
) -> ComponentV2Message | LegacyMessage: ...


def create_message(  # noqa: PLR0913
    *,
    content: str | None = None,
    items: Sequence[CreateItemType] | None = None,
    tts: bool = False,
    embeds: Sequence[Embed] | None = None,
    files: Sequence[SendableFile] | None = None,
    delete_after: float | None = None,
    allowed_mentions: AllowedMentions | None = None,
    suppress_embeds: bool = False,
    ephemeral: bool = False,
    silent: bool = False,
    poll: Poll | None = None,
    edit_original: bool = False,
    disable_items: bool = False,
) -> ComponentV2Message | LegacyMessage:
    """Create a V2 or legacy message according to the supplied component items.

    Component V2 messages cannot use non-default values for ``content``, ``tts``,
    ``embeds``, ``poll``, or ``suppress_embeds``. With a broad ``CreateItemType``
    annotation, only fields valid for both message kinds are accepted; narrow
    ``items`` to ``LegacyItemType`` before using legacy-only fields.
    """
    if items is not None and is_sequence_v2_item(items):
        if not is_sequence_all_v2_item(items):
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
        return ComponentV2Message(
            items=items,
            files=files,
            delete_after=delete_after,
            allowed_mentions=allowed_mentions,
            ephemeral=ephemeral,
            silent=silent,
            edit_original=edit_original,
            disable_items=disable_items,
        )
    if items is not None and not is_sequence_legacy_item(items):
        raise AssertionError('Component items must be legacy items when no Component V2 item is present.')
    return LegacyMessage(
        content=content,
        items=items,
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


class ModelBase:
    """The base class that all models must inherit from.

    A model may optionally define ``on_error(error: ExceptionGroup[Exception])``.
    It receives only this model's UI callback errors and view timeouts after
    those operations have failed to handle them themselves. External task
    errors are always sent to the controller's fallback handler.
    """

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
