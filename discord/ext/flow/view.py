from __future__ import annotations

from asyncio import CancelledError, get_running_loop
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING

from discord import ui
from discord.utils import MISSING, maybe_coroutine

from .model import Button, ChannelSelect, Link, MentionableSelect, RoleSelect, Select, UserSelect
from .util import map_or, unwrap_or

if TYPE_CHECKING:
    from asyncio import Future
    from collections.abc import Sequence

    from discord import Interaction

    from .controller import Controller
    from .model import ItemType, ViewConfig
    from .result import Result


class _Button(ui.Button['_View']):
    view: _View

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


class _Link(ui.Button['_View']):
    def __init__(self, config: Link) -> None:
        super().__init__(label=config.label, disabled=config.disabled, emoji=config.emoji, row=config.row)


class _Select(ui.Select['_View']):
    view: _View

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


class _UserSelect(ui.UserSelect['_View']):
    view: _View

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


class _RoleSelect(ui.RoleSelect['_View']):
    view: _View

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


class _MentionableSelect(ui.MentionableSelect['_View']):
    view: _View

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


class _ChannelSelect(ui.ChannelSelect['_View']):
    view: _View

    def __init__(self, config: ChannelSelect) -> None:
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


class _View(ui.View):
    config: ViewConfig
    fut: Future[Result]
    controller: Controller

    def __init__(self, config: ViewConfig, items: Sequence[ItemType], controller: Controller) -> None:
        super().__init__(timeout=config.get('timeout'))
        self.config = config
        self.set_items(items)
        self.fut = get_running_loop().create_future()
        self.controller = controller

    def set_items(self, items: Sequence[ItemType]) -> None:
        for item in items:
            match item:
                case Button(_):
                    self.add_item(_Button(item))
                case Link(_):
                    self.add_item(_Link(item))
                case Select(_):
                    self.add_item(_Select(item))
                case UserSelect(_):
                    self.add_item(_UserSelect(item))
                case RoleSelect(_):
                    self.add_item(_RoleSelect(item))
                case MentionableSelect(_):
                    self.add_item(_MentionableSelect(item))
                case ChannelSelect(_):
                    self.add_item(_ChannelSelect(item))

    async def _set_result(self, result: Result, messageable: Interaction) -> None:
        if result._interaction is None:
            result = replace(result, _interaction=messageable)
        self.fut.set_result(result)

    async def _wait(self) -> Result:
        ret = await self.fut
        self.fut = get_running_loop().create_future()
        return ret

    def _reset_fut(self) -> None:
        """Resets the future if the current one is done."""
        if self.fut.done():
            self.fut = get_running_loop().create_future()
