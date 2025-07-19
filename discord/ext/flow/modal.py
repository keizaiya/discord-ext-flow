from __future__ import annotations

from asyncio import get_running_loop
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict, overload

from discord import TextStyle, ui
from discord.utils import maybe_coroutine

from .controller import Controller, _get_controller
from .external_task import ExternalTaskLifeTime

if TYPE_CHECKING:
    from asyncio import Future
    from typing import Self

    from discord import Interaction
    from discord.utils import MaybeAwaitableFunc

    from .external_task import ExternalResultTask
    from .result import Result

    type CT1 = MaybeAwaitableFunc[[Interaction, tuple[str]], Result]
    type CT2 = MaybeAwaitableFunc[[Interaction, tuple[str, str]], Result]
    type CT3 = MaybeAwaitableFunc[[Interaction, tuple[str, str, str]], Result]
    type CT4 = MaybeAwaitableFunc[[Interaction, tuple[str, str, str, str]], Result]
    type CT5 = MaybeAwaitableFunc[[Interaction, tuple[str, str, str, str, str]], Result]
    type CT1To5 = CT1 | CT2 | CT3 | CT4 | CT5
    type TI1 = tuple['TextInput']
    type TI2 = tuple['TextInput', 'TextInput']
    type TI3 = tuple['TextInput', 'TextInput', 'TextInput']
    type TI4 = tuple['TextInput', 'TextInput', 'TextInput', 'TextInput']
    type TI5 = tuple['TextInput', 'TextInput', 'TextInput', 'TextInput', 'TextInput']
    type TI1To5 = TI1 | TI2 | TI3 | TI4 | TI5

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

    def __init__(self, config: ModalConfig, text_inputs: TI1To5, callback: CT1To5) -> None:
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
) -> ExternalResultTask: ...
@overload
async def send_modal(
    callback: CT2,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI2,
    *,
    controller: Controller | None = None,
) -> ExternalResultTask: ...
@overload
async def send_modal(
    callback: CT3,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI3,
    *,
    controller: Controller | None = None,
) -> ExternalResultTask: ...
@overload
async def send_modal(
    callback: CT4,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI4,
    *,
    controller: Controller | None = None,
) -> ExternalResultTask: ...
@overload
async def send_modal(
    callback: CT5,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI5,
    *,
    controller: Controller | None = None,
) -> ExternalResultTask: ...


async def send_modal(
    callback: CT1To5,
    interaction: Interaction,
    config: ModalConfig,
    text_inputs: TI1To5,
    *,
    controller: Controller | None = None,
) -> ExternalResultTask:
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
