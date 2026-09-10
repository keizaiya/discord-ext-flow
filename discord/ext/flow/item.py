from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from discord import (
    Attachment,
    ButtonStyle,
    CheckboxGroupOption,
    Interaction,
    Member,
    RadioGroupOption,
    Role,
    SelectOption,
    SeparatorSpacing,
    TextStyle,
    User,
    ui,
)
from discord.app_commands import AppCommandChannel, AppCommandThread
from discord.utils import MaybeAwaitable

from .result import Result

__all__ = (
    'ActionRow',
    'Button',
    'ButtonCallback',
    'ChannelSelect',
    'ChannelSelectCallback',
    'Checkbox',
    'CheckboxGroup',
    'CheckboxGroupOption',
    'Container',
    'File',
    'FileUpload',
    'InteractiveButton',
    'InteractiveChannelSelect',
    'InteractiveItem',
    'InteractiveMentionableSelect',
    'InteractiveRoleSelect',
    'InteractiveSelect',
    'InteractiveUserSelect',
    'Label',
    'Link',
    'MediaGallery',
    'MentionableSelect',
    'MentionableSelectCallback',
    'ModalInputItem',
    'ModalInputType',
    'ModalItem',
    'ModalItemType',
    'ModalValue',
    'PremiumButton',
    'RadioGroup',
    'RadioGroupOption',
    'RoleSelect',
    'RoleSelectCallback',
    'Section',
    'Select',
    'SelectCallback',
    'SelectOption',
    'Separator',
    'TextDisplay',
    'TextInput',
    'Thumbnail',
    'UserSelect',
    'UserSelectCallback',
)

if TYPE_CHECKING:
    import sys
    from collections.abc import Sequence

    from discord import (
        ChannelType,
        ClientUser,
        Colour,
        Emoji,
        File as SendableFile,
        MediaGalleryItem,
        Object,
        PartialEmoji,
        SelectDefaultValue,
        Thread,
        UnfurledMediaItem,
    )
    from discord.abc import GuildChannel
    from discord.ui.view import BaseView

    if sys.version_info < (3, 13):
        from typing_extensions import TypeIs
    else:
        from typing import TypeIs

    __all__ += (  # type: ignore[reportUnsupportedDunderAll, assignment]
        'ActionRowItemType',
        'ChannelSelectDefaultValues',
        'ContainerItemType',
        'CreateItemType',
        'ItemType',
        'LegacyItemType',
        'MentionableSelectDefaultValues',
        'RoleSelectDefaultValues',
        'UserSelectDefaultValues',
        'V2ItemType',
    )

    type UserSelectDefaultValues = SelectDefaultValue | Object | Member | ClientUser | User
    type RoleSelectDefaultValues = SelectDefaultValue | Object | Role
    type MentionableSelectDefaultValues = SelectDefaultValue | Object | Member | ClientUser | User | Role
    type ChannelSelectDefaultValues = (
        SelectDefaultValue | Object | GuildChannel | AppCommandChannel | AppCommandThread | Thread
    )


type ButtonCallback = Callable[[Interaction], MaybeAwaitable[Result]]
type SelectCallback = Callable[[Interaction, list[str]], MaybeAwaitable[Result]]
type UserSelectCallback = Callable[[Interaction, list[User | Member]], MaybeAwaitable[Result]]
type RoleSelectCallback = Callable[[Interaction, list[Role]], MaybeAwaitable[Result]]
type MentionableSelectCallback = Callable[[Interaction, list[User | Member | Role]], MaybeAwaitable[Result]]
type ChannelSelectCallback = Callable[[Interaction, list[AppCommandChannel | AppCommandThread]], MaybeAwaitable[Result]]


class InteractiveItem[ItemT, CallbackT](NamedTuple):
    """A component config explicitly bound to a flow callback."""

    item: ItemT
    callback: CallbackT


@dataclass
class Button:
    """discord.ui.Button config. Bind it with :meth:`on` before using it in a message."""

    label: str | None = None
    custom_id: str | None = None
    disabled: bool = False
    style: ButtonStyle = ButtonStyle.secondary
    emoji: str | Emoji | PartialEmoji | None = None
    row: int | None = None
    id: int | None = None

    def on(self, *, callback: ButtonCallback) -> InteractiveButton:
        """Bind a flow callback for use in a message or layout."""
        return InteractiveItem(item=self, callback=callback)


@dataclass
class Link:
    """discord.ui.Button for link without callback for Message.items."""

    url: str
    label: str | None = None
    disabled: bool = False
    emoji: str | Emoji | PartialEmoji | None = None
    row: int | None = None
    id: int | None = None


@dataclass
class PremiumButton:
    """discord.ui.Button for a premium SKU without callback for Message.items."""

    sku_id: int
    disabled: bool = False
    row: int | None = None
    id: int | None = None


@dataclass
class Select:
    """discord.ui.Select config for a message or modal Label."""

    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    required: bool = True
    row: int | None = None
    options: Sequence[SelectOption] = ()
    id: int | None = None

    def on(self, *, callback: SelectCallback) -> InteractiveSelect:
        """Bind a flow callback for use in a message or layout."""
        return InteractiveItem(item=self, callback=callback)

    def field(self) -> ModalItem[Select, SelectValue]:
        """Bind this config to a submitted modal value."""
        return ModalItem(item=self)


@dataclass
class UserSelect:
    """discord.ui.UserSelect config for a message or modal Label."""

    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    required: bool = False
    row: int | None = None
    default_values: Sequence[UserSelectDefaultValues] | None = None
    id: int | None = None

    def on(self, *, callback: UserSelectCallback) -> InteractiveUserSelect:
        """Bind a flow callback for use in a message or layout."""
        return InteractiveItem(item=self, callback=callback)

    def field(self) -> ModalItem[UserSelect, UserSelectValue]:
        """Bind this config to a submitted modal value."""
        return ModalItem(item=self)


@dataclass
class RoleSelect:
    """discord.ui.RoleSelect config for a message or modal Label."""

    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    required: bool = False
    row: int | None = None
    default_values: Sequence[RoleSelectDefaultValues] | None = None
    id: int | None = None

    def on(self, *, callback: RoleSelectCallback) -> InteractiveRoleSelect:
        """Bind a flow callback for use in a message or layout."""
        return InteractiveItem(item=self, callback=callback)

    def field(self) -> ModalItem[RoleSelect, RoleSelectValue]:
        """Bind this config to a submitted modal value."""
        return ModalItem(item=self)


@dataclass
class MentionableSelect:
    """discord.ui.MentionableSelect config for a message or modal Label."""

    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    required: bool = False
    row: int | None = None
    default_values: Sequence[MentionableSelectDefaultValues] | None = None
    id: int | None = None

    def on(self, *, callback: MentionableSelectCallback) -> InteractiveMentionableSelect:
        """Bind a flow callback for use in a message or layout."""
        return InteractiveItem(item=self, callback=callback)

    def field(self) -> ModalItem[MentionableSelect, MentionableSelectValue]:
        """Bind this config to a submitted modal value."""
        return ModalItem(item=self)


@dataclass
class ChannelSelect:
    """discord.ui.ChannelSelect config for a message or modal Label."""

    placeholder: str | None = None
    custom_id: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    required: bool = False
    row: int | None = None
    channel_types: Sequence[ChannelType] | None = field(default=None, kw_only=True)
    default_values: Sequence[ChannelSelectDefaultValues] | None = None
    id: int | None = None

    def on(self, *, callback: ChannelSelectCallback) -> InteractiveChannelSelect:
        """Bind a flow callback for use in a message or layout."""
        return InteractiveItem(item=self, callback=callback)

    def field(self) -> ModalItem[ChannelSelect, ChannelSelectValue]:
        """Bind this config to a submitted modal value."""
        return ModalItem(item=self)


type InteractiveButton = InteractiveItem[Button, ButtonCallback]
type InteractiveSelect = InteractiveItem[Select, SelectCallback]
type InteractiveUserSelect = InteractiveItem[UserSelect, UserSelectCallback]
type InteractiveRoleSelect = InteractiveItem[RoleSelect, RoleSelectCallback]
type InteractiveMentionableSelect = InteractiveItem[MentionableSelect, MentionableSelectCallback]
type InteractiveChannelSelect = InteractiveItem[ChannelSelect, ChannelSelectCallback]


@dataclass
class ModalItem[ConfigT, ValueT]:
    """A modal input config bound to the value populated by one modal submission."""

    item: ConfigT
    _ui_item: ui.Item[BaseView] | None = field(default=None, init=False, repr=False)
    _submitted: bool = field(default=False, init=False, repr=False)

    @property
    def value(self) -> ValueT:
        """Return the submitted value, or fail if the modal has not been submitted."""
        if not self._submitted:
            raise RuntimeError('Modal item has not been submitted.')
        if self._ui_item is None:
            raise RuntimeError('Modal item is not bound to a modal.')
        return _modal_value(self._ui_item)  # type: ignore[return-value, reportReturnType]

    def _bind(self, item: ui.Item[BaseView]) -> None:
        if self._ui_item is not None:
            raise ValueError('Modal item instances cannot be reused.')
        self._ui_item = item

    def _mark_submitted(self) -> None:
        if self._ui_item is None:
            raise RuntimeError('Modal item is not bound to a modal.')
        self._submitted = True


def _modal_value(item: ui.Item[BaseView]) -> object:
    if isinstance(item, (ui.TextInput, ui.RadioGroup, ui.Checkbox)):
        return item.value
    if isinstance(
        item,
        (
            ui.Select,
            ui.UserSelect,
            ui.RoleSelect,
            ui.MentionableSelect,
            ui.ChannelSelect,
            ui.FileUpload,
            ui.CheckboxGroup,
        ),
    ):
        return item.values
    raise TypeError(f'{type(item).__name__} is not a valid modal input.')


@dataclass
class TextInput:
    """discord.ui.TextInput for a modal Label or the legacy top level."""

    label: str | None = None
    style: TextStyle = TextStyle.short
    custom_id: str | None = None
    placeholder: str | None = None
    default: str | None = None
    required: bool = True
    min_length: int | None = None
    max_length: int | None = None
    row: int | None = None
    id: int | None = None

    def field(self) -> ModalItem[TextInput, TextInputValue]:
        """Bind this config to a submitted modal value."""
        return ModalItem(item=self)


@dataclass
class FileUpload:
    """discord.ui.FileUpload for a modal Label."""

    custom_id: str | None = None
    required: bool = True
    min_values: int | None = None
    max_values: int | None = None
    id: int | None = None

    def field(self) -> ModalItem[FileUpload, FileUploadValue]:
        """Bind this config to a submitted modal value."""
        return ModalItem(item=self)


@dataclass
class RadioGroup:
    """discord.ui.RadioGroup for a modal Label."""

    custom_id: str | None = None
    required: bool = True
    options: Sequence[RadioGroupOption] = ()
    id: int | None = None

    def field(self) -> ModalItem[RadioGroup, RadioGroupValue]:
        """Bind this config to a submitted modal value."""
        return ModalItem(item=self)


@dataclass
class CheckboxGroup:
    """discord.ui.CheckboxGroup for a modal Label."""

    custom_id: str | None = None
    required: bool = True
    min_values: int | None = None
    max_values: int | None = None
    options: Sequence[CheckboxGroupOption] = ()
    id: int | None = None

    def field(self) -> ModalItem[CheckboxGroup, CheckboxGroupValue]:
        """Bind this config to a submitted modal value."""
        return ModalItem(item=self)


@dataclass
class Checkbox:
    """discord.ui.Checkbox for a modal Label."""

    custom_id: str | None = None
    default: bool = False
    id: int | None = None

    def field(self) -> ModalItem[Checkbox, CheckboxValue]:
        """Bind this config to a submitted modal value."""
        return ModalItem(item=self)


@dataclass
class Label:
    """discord.ui.Label containing one formal modal input."""

    text: str
    component: ModalInputItem
    description: str | None = None
    id: int | None = None
    row: int | None = None


type TextInputValue = str
type SelectValue = list[str]
type UserSelectValue = list[Member | User | str]
type RoleSelectValue = list[Role | str]
type MentionableSelectValue = list[Member | User | Role | str]
type ChannelSelectValue = list[AppCommandChannel | AppCommandThread | str]
type FileUploadValue = list[Attachment]
type RadioGroupValue = str | None
type CheckboxGroupValue = list[str]
type CheckboxValue = bool
type ModalValue = (
    TextInputValue
    | SelectValue
    | UserSelectValue
    | RoleSelectValue
    | MentionableSelectValue
    | ChannelSelectValue
    | FileUploadValue
    | RadioGroupValue
    | CheckboxGroupValue
    | CheckboxValue
)
type ModalInputType = (
    TextInput
    | Select
    | UserSelect
    | RoleSelect
    | MentionableSelect
    | ChannelSelect
    | FileUpload
    | RadioGroup
    | CheckboxGroup
    | Checkbox
)
type ModalInputItem = (
    ModalItem[TextInput, TextInputValue]
    | ModalItem[Select, SelectValue]
    | ModalItem[UserSelect, UserSelectValue]
    | ModalItem[RoleSelect, RoleSelectValue]
    | ModalItem[MentionableSelect, MentionableSelectValue]
    | ModalItem[ChannelSelect, ChannelSelectValue]
    | ModalItem[FileUpload, FileUploadValue]
    | ModalItem[RadioGroup, RadioGroupValue]
    | ModalItem[CheckboxGroup, CheckboxGroupValue]
    | ModalItem[Checkbox, CheckboxValue]
)
type ModalItemType = Label | TextDisplay | ModalItem[TextInput, TextInputValue]


@dataclass
class TextDisplay:
    """discord.ui.TextDisplay for ComponentV2Message.items or a flow Modal."""

    content: str
    id: int | None = None


@dataclass
class Thumbnail:
    """discord.ui.Thumbnail for Section.accessory."""

    media: str | SendableFile | UnfurledMediaItem
    description: str | None = None
    spoiler: bool = False
    id: int | None = None


@dataclass
class MediaGallery:
    """discord.ui.MediaGallery for ComponentV2Message.items."""

    items: Sequence[MediaGalleryItem]
    id: int | None = None


@dataclass
class File:
    """discord.ui.File for ComponentV2Message.items."""

    media: str | UnfurledMediaItem | SendableFile
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
    accessory: Thumbnail | InteractiveButton | Link | PremiumButton
    id: int | None = None


@dataclass
class Container:
    """discord.ui.Container for ComponentV2Message.items."""

    items: Sequence[ContainerItemType]
    accent_color: Colour | int | None = None
    spoiler: bool = False
    id: int | None = None


_V2_TOP_LEVEL_TYPES = (ActionRow, Container, File, MediaGallery, Section, Separator, TextDisplay)


def is_sequence_v2_item(items: Sequence[object]) -> TypeIs[Sequence[V2ItemType]]:
    """Check whether any item requires a Component V2 message."""
    return any(isinstance(item, _V2_TOP_LEVEL_TYPES) for item in items)


def is_sequence_all_v2_item(items: Sequence[object]) -> TypeIs[Sequence[V2ItemType]]:
    """Check whether every item is a Component V2 top-level item."""
    return all(isinstance(item, _V2_TOP_LEVEL_TYPES) for item in items)


def is_sequence_legacy_item(items: Sequence[object]) -> TypeIs[Sequence[LegacyItemType]]:
    """Check whether every item is a legacy top-level item."""
    return all(not isinstance(item, _V2_TOP_LEVEL_TYPES) for item in items)


if TYPE_CHECKING:
    type LegacyItemType = (
        InteractiveButton
        | Link
        | PremiumButton
        | InteractiveSelect
        | InteractiveUserSelect
        | InteractiveRoleSelect
        | InteractiveMentionableSelect
        | InteractiveChannelSelect
    )
    type ActionRowItemType = LegacyItemType
    type ContainerItemType = ActionRow | Section | TextDisplay | MediaGallery | File | Separator
    type V2ItemType = ActionRow | Section | TextDisplay | MediaGallery | File | Separator | Container
    type CreateItemType = LegacyItemType | V2ItemType
    type ItemType = CreateItemType


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
