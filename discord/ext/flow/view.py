from __future__ import annotations

from typing import TYPE_CHECKING

from discord import Client, Interaction, ui
from discord.utils import MISSING, maybe_coroutine

from .model import Button, ChannelSelect, Link, MentionableSelect, RoleSelect, Select, UserSelect
from .result import _ResultTypeEnum
from .util import unwrap_or

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .model import ItemType, ModelBase, ViewConfig
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

    async def callback(self, interaction: Interaction[Client]) -> None:
        await self.view.set_result(await maybe_coroutine(self.config.callback, interaction), interaction)


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
            options=list(config.options),
            disabled=config.disabled,
            row=config.row,
        )
        self.config = config

    async def callback(self, interaction: Interaction[Client]) -> None:
        await self.view.set_result(await maybe_coroutine(self.config.callback, interaction, self.values), interaction)


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
            default_values=config.default_values,
        )
        self.config = config

    async def callback(self, interaction: Interaction[Client]) -> None:
        await self.view.set_result(await maybe_coroutine(self.config.callback, interaction, self.values), interaction)


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
            default_values=config.default_values,
        )
        self.config = config

    async def callback(self, interaction: Interaction[Client]) -> None:
        await self.view.set_result(await maybe_coroutine(self.config.callback, interaction, self.values), interaction)


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
            default_values=config.default_values,
        )
        self.config = config

    async def callback(self, interaction: Interaction[Client]) -> None:
        await self.view.set_result(await maybe_coroutine(self.config.callback, interaction, self.values), interaction)


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
            default_values=config.default_values,
        )
        self.config = config

    async def callback(self, interaction: Interaction[Client]) -> None:
        await self.view.set_result(await maybe_coroutine(self.config.callback, interaction, self.values), interaction)


class _View(ui.View):
    result: tuple[ModelBase, Interaction[Client]] | None = None
    config: ViewConfig

    def __init__(self, config: ViewConfig, items: Sequence[ItemType]) -> None:
        super().__init__(timeout=config.get('timeout'))
        self.config = config
        self.set_items(items)

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

    async def set_result(self, result: Result, interaction: Interaction[Client]) -> None:
        if result._interaction is not None:
            interaction = result._interaction
        match result._type:
            case _ResultTypeEnum.MESSAGE:
                assert result._message is not None
                msg = result._message
                self.clear_items()
                self.set_items(msg.items or ())
                if msg.edit_original:
                    await interaction.response.edit_message(
                        content=msg.content,
                        embeds=msg.embeds or (),
                        attachments=msg.files or (),
                        view=self,
                        allowed_mentions=msg.allowed_mentions,
                        delete_after=msg.delete_after,
                    )
                else:
                    kwargs = msg._to_dict()
                    kwargs['view'] = self
                    await interaction.response.send_message(**kwargs)
                if not msg.items:
                    self.stop()
            case _ResultTypeEnum.MODEL:
                assert result._model is not None
                self.result = (result._model, interaction)
                self.stop()
            case _ResultTypeEnum.CONTINUE | _ResultTypeEnum.FINISH:
                if not interaction.response.is_done():
                    raise RuntimeError('Callback MUST consume interaction.')
                if result._is_end:
                    self.stop()
