from __future__ import annotations

from asyncio import Future, Task, get_running_loop
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple, TypedDict

from discord import Client, Interaction, TextStyle, ui

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = ('ModalConfig', 'ModalController', 'ModalResult', 'TextInput', 'send_modal')


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


class ModalResult(NamedTuple):
    """Result of modal. length of texts is same as length of text_inputs in ModalConfig."""

    texts: tuple[str, ...]
    interaction: Interaction[Client]


class InnerModal(ui.Modal):
    result: ModalResult

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
        self.result = ModalResult(tuple(results), interaction)
        self.stop()


async def send_modal(
    interaction: Interaction[Client], config: ModalConfig, text_inputs: Sequence[TextInput]
) -> ModalResult:
    """Text input modal.

    Args:
        interaction (Interaction): Interaction to send modal. This interaction will be consumed.
        config (ModalConfig): config for modal.
        text_inputs (Sequence[TextInput]): text inputs for modal.

    Returns:
        ModalResult: Result of modal. length of .texts is same as length of text_inputs in ModalConfig.
    """
    inner_modal = InnerModal(config, text_inputs)
    await interaction.response.send_modal(inner_modal)
    await inner_modal.wait()
    return inner_modal.result


class ModalController:
    """Modal controller.

    you should...
    - construct this class and save it to Model.
    """

    def __init__(self) -> None:
        self.__stopped: Future[bool] = get_running_loop().create_future()
        self.modals: list[tuple[InnerModal, Task[None]]] = []

    async def _wait_modal(self, modal: InnerModal, result_future: Future[ModalResult]) -> None:
        await modal.wait()

        if self.__stopped.done():
            result_future.cancel()
            return

        self.__stopped.set_result(True)
        result_future.set_result(modal.result)
        self.__inner_cancel()

    def stop(self) -> None:
        """Stop all modals. You should call this method in Model.after_invoke method."""
        if self.__stopped.done():
            return
        self.__stopped.set_result(False)
        self.__inner_cancel()

    def __inner_cancel(self) -> None:
        for m, t in self.modals:
            m.stop()
            t.cancel()

    def is_finished(self) -> bool:
        """This modal controller is finished or not.

        Returns:
            bool: True if finished.
        """
        return self.__stopped.done()

    async def wait(self) -> bool:
        """Wait until all modals are finished.

        Returns:
            bool: False if call stop method.
        """
        return await self.__stopped

    async def send_modal(
        self,
        interaction: Interaction,
        config: ModalConfig,
        text_inputs: Sequence[TextInput],
    ) -> ModalResult:
        """Send modal. call this method in any view item callback.

        Args:
            interaction (Interaction): interaction to send modal.
            config (ModalConfig): config for modal.
            text_inputs (Sequence[TextInput]): text inputs for modal.

        Returns:
            ModalResult: result of modal.

        Exceptions:
            asyncio.CancelledError:
                if call stop method, receive value from user interaction in other model or any other reason,
                raise this error. if you want catch this error and call this in flow's callback,
                you should raise any Error. not return Result.
        """
        result_future: Future[ModalResult] = get_running_loop().create_future()
        modal = InnerModal(config, text_inputs)
        await interaction.response.send_modal(modal)
        task = get_running_loop().create_task(self._wait_modal(modal, result_future))
        self.modals.append((modal, task))
        return await result_future
