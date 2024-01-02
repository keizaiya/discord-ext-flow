from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from discord import Client, Interaction, TextStyle, ui

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('TextInput', 'ModalConfig', 'send_modal')


@dataclass
class TextInput:
    """Text input config for modal. see discord.ui.TextInput."""

    label: str
    style: TextStyle = TextStyle.short
    custom_id: str | None = None
    placeholder: str | None = None
    default: str | None = None
    required: bool = False
    min_length: int | None = None
    max_length: int | None = None
    row: int | None = None


class TextInputKWargs(TypedDict, total=False):
    label: str
    custom_id: str
    style: TextStyle
    placeholder: str | None
    default: str | None
    required: bool
    min_length: int | None
    max_length: int | None
    row: int | None


@dataclass
class ModalConfig:
    """Config for modal. see discord.ui.Modal."""

    title: str
    timeout: float | None = None
    custom_id: str | None = None


class ModalConfigKWargs(TypedDict, total=False):
    title: str
    timeout: float | None
    custom_id: str


class InnerModal(ui.Modal):
    results: tuple[str, ...]
    interaction: Interaction[Client]

    def __init__(self, config: ModalConfig, text_inputs: Sequence[TextInput]) -> None:
        kwargs: ModalConfigKWargs = {'title': config.title, 'timeout': config.timeout}
        if config.custom_id is not None:
            kwargs['custom_id'] = config.custom_id
        super().__init__(**kwargs)
        for text_input in text_inputs:
            ti_kwargs: TextInputKWargs = {
                'label': text_input.label,
                'style': text_input.style,
                'placeholder': text_input.placeholder,
                'default': text_input.default,
                'required': text_input.required,
                'min_length': text_input.min_length,
                'max_length': text_input.max_length,
                'row': text_input.row,
            }
            if text_input.custom_id is not None:
                ti_kwargs['custom_id'] = text_input.custom_id
            self.add_item(ui.TextInput(**ti_kwargs))

    async def on_submit(self, interaction: Interaction[Client]) -> None:
        results: list[str] = []
        for child in self.children:
            assert isinstance(child, ui.TextInput)
            results.append(child.value)
        self.results = tuple(results)
        self.interaction = interaction
        self.stop()


async def send_modal(
    interaction: Interaction[Client], config: ModalConfig, text_inputs: Sequence[TextInput]
) -> tuple[tuple[str, ...], Interaction[Client]]:
    """Text input modal.

    Args:
        interaction (Interaction): Interaction to send modal. This interaction will be consumed.
        config (ModalConfig): config for modal.
        text_inputs (Sequence[TextInput]): text inputs for modal.

    Returns:
        tuple[tuple[str, ...], Interaction]: results and interaction. results length is same as text_inputs length.
    """
    inner_modal = InnerModal(config, text_inputs)
    await interaction.response.send_modal(inner_modal)
    await inner_modal.wait()
    return inner_modal.results, inner_modal.interaction
