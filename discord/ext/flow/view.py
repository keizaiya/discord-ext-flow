from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from discord import ui
from discord.utils import MISSING

from .item import (
    ActionRow,
    Button,
    ChannelSelect,
    Container,
    File,
    InteractiveItem,
    Link,
    MediaGallery,
    MentionableSelect,
    PremiumButton,
    RoleSelect,
    Section,
    Select,
    Separator,
    TextDisplay,
    Thumbnail,
    UserSelect,
    is_sequence_v2_item,
)
from .util import map_or, unwrap_or

if TYPE_CHECKING:
    import sys
    from collections.abc import Sequence

    from discord import Interaction

    from .controller import Controller
    from .item import (
        ButtonCallback,
        ChannelSelectCallback,
        InteractiveButton,
        InteractiveChannelSelect,
        InteractiveMentionableSelect,
        InteractiveRoleSelect,
        InteractiveSelect,
        InteractiveUserSelect,
        LegacyItemType,
        MentionableSelectCallback,
        RoleSelectCallback,
        SelectCallback,
        UserSelectCallback,
        V2ItemType,
    )
    from .model import ViewConfig

    if sys.version_info < (3, 13):
        from typing_extensions import TypeIs
    else:
        from typing import TypeIs

V = TypeVar('V', '_View', '_LayoutView')


class _Button(ui.Button[V]):
    view: V
    flow_callback: ButtonCallback

    def __init__(self, binding: InteractiveButton) -> None:
        config = binding.item
        super().__init__(
            label=config.label,
            custom_id=config.custom_id,
            disabled=config.disabled,
            style=config.style,
            emoji=config.emoji,
            row=config.row,
            id=config.id,
        )
        self.flow_callback = binding.callback

    async def callback(self, interaction: Interaction) -> None:
        self.view.controller._create_ui_task(self.view, self.flow_callback, interaction)


class _Link(ui.Button[V]):
    def __init__(self, config: Link) -> None:
        super().__init__(
            url=config.url,
            label=config.label,
            disabled=config.disabled,
            emoji=config.emoji,
            row=config.row,
            id=config.id,
        )


class _PremiumButton(ui.Button[V]):
    def __init__(self, config: PremiumButton) -> None:
        super().__init__(sku_id=config.sku_id, disabled=config.disabled, row=config.row, id=config.id)


class _Select(ui.Select[V]):
    view: V
    flow_callback: SelectCallback

    def __init__(self, binding: InteractiveSelect) -> None:
        config = binding.item
        super().__init__(
            custom_id=unwrap_or(config.custom_id, MISSING),
            placeholder=config.placeholder,
            min_values=config.min_values,
            max_values=config.max_values,
            options=list(config.options),
            disabled=config.disabled,
            required=config.required,
            row=config.row,
            id=config.id,
        )
        self.flow_callback = binding.callback

    async def callback(self, interaction: Interaction) -> None:
        self.view.controller._create_ui_task(self.view, self.flow_callback, interaction, self.values)


class _UserSelect(ui.UserSelect[V]):
    view: V
    flow_callback: UserSelectCallback

    def __init__(self, binding: InteractiveUserSelect) -> None:
        config = binding.item
        super().__init__(
            custom_id=unwrap_or(config.custom_id, MISSING),
            placeholder=config.placeholder,
            min_values=config.min_values,
            max_values=config.max_values,
            disabled=config.disabled,
            required=config.required,
            row=config.row,
            default_values=unwrap_or(config.default_values, MISSING),
            id=config.id,
        )
        self.flow_callback = binding.callback

    async def callback(self, interaction: Interaction) -> None:
        self.view.controller._create_ui_task(self.view, self.flow_callback, interaction, self.values)


class _RoleSelect(ui.RoleSelect[V]):
    view: V
    flow_callback: RoleSelectCallback

    def __init__(self, binding: InteractiveRoleSelect) -> None:
        config = binding.item
        super().__init__(
            custom_id=unwrap_or(config.custom_id, MISSING),
            placeholder=config.placeholder,
            min_values=config.min_values,
            max_values=config.max_values,
            disabled=config.disabled,
            required=config.required,
            row=config.row,
            default_values=unwrap_or(config.default_values, MISSING),
            id=config.id,
        )
        self.flow_callback = binding.callback

    async def callback(self, interaction: Interaction) -> None:
        self.view.controller._create_ui_task(self.view, self.flow_callback, interaction, self.values)


class _MentionableSelect(ui.MentionableSelect[V]):
    view: V
    flow_callback: MentionableSelectCallback

    def __init__(self, binding: InteractiveMentionableSelect) -> None:
        config = binding.item
        super().__init__(
            custom_id=unwrap_or(config.custom_id, MISSING),
            placeholder=config.placeholder,
            min_values=config.min_values,
            max_values=config.max_values,
            disabled=config.disabled,
            required=config.required,
            row=config.row,
            default_values=unwrap_or(config.default_values, MISSING),
            id=config.id,
        )
        self.flow_callback = binding.callback

    async def callback(self, interaction: Interaction) -> None:
        self.view.controller._create_ui_task(self.view, self.flow_callback, interaction, self.values)


class _ChannelSelect(ui.ChannelSelect[V]):
    view: V
    flow_callback: ChannelSelectCallback

    def __init__(self, binding: InteractiveChannelSelect) -> None:
        config = binding.item
        super().__init__(
            custom_id=unwrap_or(config.custom_id, MISSING),
            placeholder=config.placeholder,
            min_values=config.min_values,
            max_values=config.max_values,
            disabled=config.disabled,
            required=config.required,
            row=config.row,
            channel_types=map_or(config.channel_types, MISSING, list),
            default_values=unwrap_or(config.default_values, MISSING),
            id=config.id,
        )
        self.flow_callback = binding.callback

    async def callback(self, interaction: Interaction) -> None:
        self.view.controller._create_ui_task(self.view, self.flow_callback, interaction, self.values)


_RAW_INTERACTIVE_TYPES = (Button, Select, UserSelect, RoleSelect, MentionableSelect, ChannelSelect)


def _is_button_binding(item: InteractiveItem[Any, Any]) -> TypeIs[InteractiveButton]:
    return isinstance(item.item, Button)


def _is_select_binding(item: InteractiveItem[Any, Any]) -> TypeIs[InteractiveSelect]:
    return isinstance(item.item, Select)


def _is_user_select_binding(item: InteractiveItem[Any, Any]) -> TypeIs[InteractiveUserSelect]:
    return isinstance(item.item, UserSelect)


def _is_role_select_binding(item: InteractiveItem[Any, Any]) -> TypeIs[InteractiveRoleSelect]:
    return isinstance(item.item, RoleSelect)


def _is_mentionable_select_binding(item: InteractiveItem[Any, Any]) -> TypeIs[InteractiveMentionableSelect]:
    return isinstance(item.item, MentionableSelect)


def _is_channel_select_binding(item: InteractiveItem[Any, Any]) -> TypeIs[InteractiveChannelSelect]:
    return isinstance(item.item, ChannelSelect)


def _to_action_item(item: LegacyItemType) -> ui.Item[V]:  # noqa: C901, PLR0911
    """Build a message component and reject configs without an explicit callback binding."""
    if isinstance(item, Link):
        return _Link[V](item)
    if isinstance(item, PremiumButton):
        return _PremiumButton[V](item)
    if isinstance(item, _RAW_INTERACTIVE_TYPES):
        raise TypeError(f'{type(item).__name__} must be bound with .on(callback=...) before sending a message.')
    if _is_button_binding(item):
        return _Button[V](item)
    if _is_select_binding(item):
        return _Select[V](item)
    if _is_user_select_binding(item):
        return _UserSelect[V](item)
    if _is_role_select_binding(item):
        return _RoleSelect[V](item)
    if _is_mentionable_select_binding(item):
        return _MentionableSelect[V](item)
    if _is_channel_select_binding(item):
        return _ChannelSelect[V](item)
    if isinstance(item, InteractiveItem):
        raise TypeError('The bound component cannot be sent as a message action component.')
    raise TypeError(f'{type(item).__name__} is not a valid message action component.')


def _to_v2_item(item: V2ItemType) -> ui.Item[_LayoutView]:  # noqa: PLR0911
    match item:
        case ActionRow(items, item_id):
            return ui.ActionRow['_LayoutView'](*(_to_action_item(child) for child in items), id=item_id)  # type: ignore[reportUnknownArgumentType,arg-type]
        case TextDisplay(content, item_id):
            return ui.TextDisplay(content, id=item_id)
        case Section(items, accessory, item_id):
            children = tuple(
                ui.TextDisplay['_LayoutView'](child)
                if isinstance(child, str)
                else ui.TextDisplay(child.content, id=child.id)
                for child in items
            )
            ui_accessory: ui.Item[_LayoutView]
            if isinstance(accessory, Thumbnail):
                ui_accessory = ui.Thumbnail(
                    accessory.media,
                    description=accessory.description,
                    spoiler=accessory.spoiler,
                    id=accessory.id,
                )
            else:
                ui_accessory = _to_action_item(accessory)
            return ui.Section(*children, accessory=ui_accessory, id=item_id)
        case MediaGallery(items, item_id):
            return ui.MediaGallery(*items, id=item_id)
        case File(media, spoiler, item_id):
            return ui.File(media, spoiler=spoiler, id=item_id)
        case Separator(visible, spacing, item_id):
            return ui.Separator(visible=visible, spacing=spacing, id=item_id)
        case Container(items, accent_color, spoiler, item_id):
            return ui.Container(
                *(_to_v2_item(child) for child in items),
                accent_color=accent_color,
                spoiler=spoiler,
                id=item_id,
            )


class _ViewLifecycleMixin:
    config: ViewConfig
    controller: Controller

    def _init_flow_state(self, config: ViewConfig, controller: Controller) -> None:
        self.config = config
        self.controller = controller


class _View(_ViewLifecycleMixin, ui.View):
    def __init__(self, config: ViewConfig, items: Sequence[LegacyItemType], controller: Controller) -> None:
        super().__init__(timeout=config.get('timeout'))
        self.set_items(items)
        self._init_flow_state(config, controller)

    def set_items(self, items: Sequence[LegacyItemType]) -> None:
        for item in items:
            self.add_item(_to_action_item(item))  # type: ignore[arg-type, reportArgumentType]  # discord.py Item view generic is invariant.


class _LayoutView(_ViewLifecycleMixin, ui.LayoutView):
    def __init__(self, config: ViewConfig, items: Sequence[V2ItemType], controller: Controller) -> None:
        super().__init__(timeout=config.get('timeout'))
        self.set_items(items)
        self._init_flow_state(config, controller)

    def set_items(self, items: Sequence[V2ItemType]) -> None:
        for item in items:
            self.add_item(_to_v2_item(item))


_ViewType = _View | _LayoutView


def create_view(
    config: ViewConfig,
    items: Sequence[V2ItemType] | Sequence[LegacyItemType],
    controller: Controller,
) -> _ViewType:
    """Create the narrowest discord.py view capable of holding the configured items."""
    if is_sequence_v2_item(items):
        return _LayoutView(config=config, items=items, controller=controller)
    return _View(config=config, items=items, controller=controller)
