from __future__ import annotations

from asyncio import Future, get_running_loop
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, TypedDict, TypeVarTuple, overload

from discord import TextStyle, ui
from discord.utils import maybe_coroutine

from .controller import Controller, ExternalTaskLifeTime, _get_controller

if TYPE_CHECKING:
    from typing import TypeAlias

    import discord
    from discord import Client
    from discord.utils import MaybeAwaitableFunc

    from .external_task import ExternalResultTask as ERT  # noqa: N817
    from .result import Result

    Ts = TypeVarTuple('Ts')
    Interaction = discord.Interaction[Client]
    CT1: TypeAlias = MaybeAwaitableFunc[[Interaction, tuple[str]], Result]
    CT2: TypeAlias = MaybeAwaitableFunc[[Interaction, tuple[str, str]], Result]
    CT3: TypeAlias = MaybeAwaitableFunc[[Interaction, tuple[str, str, str]], Result]
    CT4: TypeAlias = MaybeAwaitableFunc[[Interaction, tuple[str, str, str, str]], Result]
    CT5: TypeAlias = MaybeAwaitableFunc[[Interaction, tuple[str, str, str, str, str]], Result]
    CT1to5: TypeAlias = CT1 | CT2 | CT3 | CT4 | CT5
    TI1: TypeAlias = tuple['TextInput']
    TI2: TypeAlias = tuple['TextInput', 'TextInput']
    TI3: TypeAlias = tuple['TextInput', 'TextInput', 'TextInput']
    TI4: TypeAlias = tuple['TextInput', 'TextInput', 'TextInput', 'TextInput']
    TI5: TypeAlias = tuple['TextInput', 'TextInput', 'TextInput', 'TextInput', 'TextInput']
    TI1To5: TypeAlias = TI1 | TI2 | TI3 | TI4 | TI5

__all__ = ('ModalConfig', 'TextInput', 'send_modal')


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


class _InnerModal(ui.Modal):
    fut: Future[Result]

    def __init__(self, config: ModalConfig, text_inputs: TI1To5, callback: CT1to5) -> None:
        kwargs: ModalConfigKWargs = {'title': config.title, 'timeout': config.timeout}
        if config.custom_id is not None:
            kwargs['custom_id'] = config.custom_id
        super().__init__(**kwargs)
        self.callback = callback
        self.fut = get_running_loop().create_future()
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

    async def on_submit(self, interaction: Interaction) -> None:
        children: list[ui.TextInput[Self]] = []
        for child in self.children:
            assert isinstance(child, ui.TextInput)
            children.append(child)
        results = tuple(child.value for child in children)
        result = await maybe_coroutine(self.callback, interaction, results)  # type: ignore[reportGeneralTypeIssues, arg-type]
        self.fut.set_result(result)
        self.stop()

    async def _wait(self) -> Result:
        return await self.fut


@overload
async def send_modal(
    callback: CT1,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI1,
    *,
    controller: Controller | None = None,
) -> ERT: ...
@overload
async def send_modal(
    callback: CT2,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI2,
    *,
    controller: Controller | None = None,
) -> ERT: ...
@overload
async def send_modal(
    callback: CT3,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI3,
    *,
    controller: Controller | None = None,
) -> ERT: ...
@overload
async def send_modal(
    callback: CT4,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI4,
    *,
    controller: Controller | None = None,
) -> ERT: ...
@overload
async def send_modal(
    callback: CT5,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI5,
    *,
    controller: Controller | None = None,
) -> ERT: ...


async def send_modal(
    callback: CT1to5,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI1To5,
    *,
    controller: Controller | None = None,
) -> ERT:
    """Send modal. call this function in any view item callback.

    Args:
        callback (CallbackType[str] | ... | CallbackType[str, str, str, str, str]): callback function.
        interaction (Interaction): interaction to send modal.
        config (ModalConfig): config for modal.
        text_inputs (tuple[TextInput] | ... | tuple[TextInput, TextInput, TextInput, TextInput, TextInput]):
            text inputs for modal.
        controller (Controller | None):  The Controller instance to manage this modal's task if you want to use it.
            Defaults to None. If None, it will use the current controller.

    Returns:
        ExternalResultTask: external result task for this modal.
    """
    modal = _InnerModal(config=config, text_inputs=text_inputs, callback=callback)
    await interaction.response.send_modal(modal)
    if controller is None:
        controller = _get_controller()
    return controller.create_external_result(modal._wait, name='modal-wait', life_time=ExternalTaskLifeTime.MODEL)
