from __future__ import annotations

from asyncio import CancelledError, get_running_loop
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING, TypeVar

from discord import ui
from discord.utils import MISSING, maybe_coroutine

from .model import (
    ActionRow,
    Button,
    ChannelSelect,
    Container,
    FileDisplay,
    Link,
    MediaGallery,
    MentionableSelect,
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
    from asyncio import Future
    from collections.abc import Sequence

    from discord import Interaction

    from .controller import Controller
    from .model import ActionRowItemType, LegacyItemType, V2ItemType, ViewConfig
    from .result import Result

V = TypeVar('V', '_View', '_LayoutView')


class _Button(ui.Button[V]):
    view: V

    def __init__(self, config: Button) -> None:
        super().__init__(
            label=config.label,
            custom_id=config.custom_id,
            disabled=config.disabled,
            style=config.style,
            emoji=config.emoji,
            row=config.row,
        )
        self.config = config

    async def callback(self, interaction: Interaction) -> None:
        with self.view.controller._set_to_context(), suppress(CancelledError):
            await self.view._set_result(await maybe_coroutine(self.config.callback, interaction), interaction)


class _Link(ui.Button[V]):
    def __init__(self, config: Link) -> None:
        super().__init__(
            url=config.url, label=config.label, disabled=config.disabled, emoji=config.emoji, row=config.row
        )


class _Select(ui.Select[V]):
    view: V

    def __init__(self, config: Select) -> None:
        super().__init__(
            custom_id=unwrap_or(config.custom_id, MISSING),
            placeholder=config.placeholder,
            min_values=config.min_values,
            max_values=config.max_values,
            options=map_or(config.options, MISSING, list),
            disabled=config.disabled,
            row=config.row,
        )
        self.config = config

    async def callback(self, interaction: Interaction) -> None:
        with self.view.controller._set_to_context(), suppress(CancelledError):
            await self.view._set_result(
                await maybe_coroutine(self.config.callback, interaction, self.values), interaction
            )


class _UserSelect(ui.UserSelect[V]):
    view: V

    def __init__(self, config: UserSelect) -> None:
        super().__init__(
            custom_id=unwrap_or(config.custom_id, MISSING),
            placeholder=config.placeholder,
            min_values=config.min_values,
            max_values=config.max_values,
            disabled=config.disabled,
            row=config.row,
            default_values=unwrap_or(config.default_values, MISSING),
        )
        self.config = config

    async def callback(self, interaction: Interaction) -> None:
        with self.view.controller._set_to_context(), suppress(CancelledError):
            await self.view._set_result(
                await maybe_coroutine(self.config.callback, interaction, self.values), interaction
            )


class _RoleSelect(ui.RoleSelect[V]):
    view: V

    def __init__(self, config: RoleSelect) -> None:
        super().__init__(
            custom_id=unwrap_or(config.custom_id, MISSING),
            placeholder=config.placeholder,
            min_values=config.min_values,
            max_values=config.max_values,
            disabled=config.disabled,
            row=config.row,
            default_values=unwrap_or(config.default_values, MISSING),
        )
        self.config = config

    async def callback(self, interaction: Interaction) -> None:
        with self.view.controller._set_to_context(), suppress(CancelledError):
            await self.view._set_result(
                await maybe_coroutine(self.config.callback, interaction, self.values), interaction
            )


class _MentionableSelect(ui.MentionableSelect[V]):
    view: V

    def __init__(self, config: MentionableSelect) -> None:
        super().__init__(
            custom_id=unwrap_or(config.custom_id, MISSING),
            placeholder=config.placeholder,
            min_values=config.min_values,
            max_values=config.max_values,
            disabled=config.disabled,
            row=config.row,
            default_values=unwrap_or(config.default_values, MISSING),
        )
        self.config = config

    async def callback(self, interaction: Interaction) -> None:
        with self.view.controller._set_to_context(), suppress(CancelledError):
            await self.view._set_result(
                await maybe_coroutine(self.config.callback, interaction, self.values), interaction
            )


class _ChannelSelect(ui.ChannelSelect[V]):
    view: V

    def __init__(self, config: ChannelSelect) -> None:
        super().__init__(
            custom_id=unwrap_or(config.custom_id, MISSING),
            placeholder=config.placeholder,
            min_values=config.min_values,
            max_values=config.max_values,
            disabled=config.disabled,
            row=config.row,
            channel_types=map_or(config.channel_types, MISSING, list),
            default_values=unwrap_or(config.default_values, MISSING),
        )
        self.config = config

    async def callback(self, interaction: Interaction) -> None:
        with self.view.controller._set_to_context(), suppress(CancelledError):
            await self.view._set_result(
                await maybe_coroutine(self.config.callback, interaction, self.values), interaction
            )


def _to_action_item(item: ActionRowItemType) -> ui.Item[ui.LayoutView]:  # noqa: PLR0911
    match item:
        case Button(_):
            return _Button['_LayoutView'](item)
        case Link(_):
            return _Link['_LayoutView'](item)
        case Select(_):
            return _Select['_LayoutView'](item)
        case UserSelect(_):
            return _UserSelect['_LayoutView'](item)
        case RoleSelect(_):
            return _RoleSelect['_LayoutView'](item)
        case MentionableSelect(_):
            return _MentionableSelect['_LayoutView'](item)
        case ChannelSelect(_):
            return _ChannelSelect['_LayoutView'](item)


def _to_v2_item(item: V2ItemType) -> ui.Item[ui.LayoutView]:  # noqa: PLR0911
    match item:
        case ActionRow(items, item_id):
            return ui.ActionRow(*(_to_action_item(child) for child in items), id=item_id)
        case TextDisplay(content, item_id):
            return ui.TextDisplay(content, id=item_id)
        case Section(items, accessory, item_id):
            children = tuple(
                ui.TextDisplay['_LayoutView'](child)
                if isinstance(child, str)
                else ui.TextDisplay(child.content, id=child.id)
                for child in items
            )
            if isinstance(accessory, Thumbnail):
                ui_accessory: ui.Item[ui.LayoutView] = ui.Thumbnail(
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
        case FileDisplay(media, spoiler, item_id):
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


def _add_old_items(view: ui.View, items: Sequence[LegacyItemType]) -> None:
    for item in items:
        match item:
            case Button(_):
                view.add_item(_Button(item))
            case Link(_):
                view.add_item(_Link(item))
            case Select(_):
                view.add_item(_Select(item))
            case UserSelect(_):
                view.add_item(_UserSelect(item))
            case RoleSelect(_):
                view.add_item(_RoleSelect(item))
            case MentionableSelect(_):
                view.add_item(_MentionableSelect(item))
            case ChannelSelect(_):
                view.add_item(_ChannelSelect(item))


def _add_v2_items(view: ui.LayoutView, items: Sequence[V2ItemType]) -> None:
    for item in items:
        view.add_item(_to_v2_item(item))


class _ViewLifecycleMixin:
    config: ViewConfig
    fut: Future[Result]
    controller: Controller

    def _init_flow_state(self, config: ViewConfig, controller: Controller) -> None:
        self.config = config
        self.fut = get_running_loop().create_future()
        self.controller = controller

    async def _set_result(self, result: Result, messageable: Interaction) -> None:
        if result._interaction is None:
            result = replace(result, _interaction=messageable)
        self.fut.set_result(result)

    async def _wait(self) -> Result:
        ret = await self.fut
        self.fut = get_running_loop().create_future()
        return ret

    def _reset_fut(self) -> None:
        """Reset the result future if the current one has completed."""
        if self.fut.done():
            self.fut = get_running_loop().create_future()


class _View(_ViewLifecycleMixin, ui.View):
    def __init__(self, config: ViewConfig, items: Sequence[LegacyItemType], controller: Controller) -> None:
        super().__init__(timeout=config.get('timeout'))
        _add_old_items(self, items)
        self._init_flow_state(config, controller)

    def set_items(self, items: Sequence[LegacyItemType]) -> None:
        _add_old_items(self, items)


class _LayoutView(_ViewLifecycleMixin, ui.LayoutView):
    def __init__(self, config: ViewConfig, items: Sequence[V2ItemType], controller: Controller) -> None:
        super().__init__(timeout=config.get('timeout'))
        _add_v2_items(self, items)
        self._init_flow_state(config, controller)

    def set_items(self, items: Sequence[V2ItemType]) -> None:
        _add_v2_items(self, items)


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
