from __future__ import annotations

from asyncio import get_running_loop
from collections.abc import Awaitable, Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, replace
from logging import getLogger
from typing import TYPE_CHECKING

from discord import Interaction, ui
from discord.utils import MISSING, maybe_coroutine

from .controller import Controller, _get_controller
from .external_task import ExternalTaskLifeTime
from .item import (
    ChannelSelect,
    Checkbox,
    CheckboxGroup,
    FileUpload,
    Label,
    MentionableSelect,
    ModalInputItem,
    ModalInputType,
    ModalItem,
    ModalItemType,
    RadioGroup,
    RoleSelect,
    Select,
    TextDisplay,
    TextInput,
    UserSelect,
)
from .result import Result
from .util import map_or, unwrap_or

if TYPE_CHECKING:
    from asyncio import Future
    from typing import Any

    from discord.ui.view import BaseView
    from discord.utils import MaybeAwaitableFunc

    from .external_task import ExternalResultTask


__all__ = ('ModalCallback', 'ModalConfig', 'send_modal')


logger = getLogger(__name__)


@dataclass
class ModalConfig:
    """discord.ui.Modal configuration for flow."""

    title: str
    timeout: float | None = None
    custom_id: str | None = None


def _to_ui_input(config: ModalInputType) -> ui.Item[BaseView]:  # noqa: C901, PLR0911
    match config:
        case TextInput():
            return ui.TextInput(
                label=config.label,
                style=config.style,
                custom_id=unwrap_or(config.custom_id, MISSING),
                placeholder=config.placeholder,
                default=config.default,
                required=config.required,
                min_length=config.min_length,
                max_length=config.max_length,
                row=config.row,
                id=config.id,
            )
        case Select():
            return ui.Select(
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
        case UserSelect():
            return ui.UserSelect(
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
        case RoleSelect():
            return ui.RoleSelect(
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
        case MentionableSelect():
            return ui.MentionableSelect(
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
        case ChannelSelect():
            return ui.ChannelSelect(
                custom_id=unwrap_or(config.custom_id, MISSING),
                channel_types=map_or(config.channel_types, MISSING, list),
                placeholder=config.placeholder,
                min_values=config.min_values,
                max_values=config.max_values,
                disabled=config.disabled,
                required=config.required,
                row=config.row,
                default_values=unwrap_or(config.default_values, MISSING),
                id=config.id,
            )
        case FileUpload():
            return ui.FileUpload(
                custom_id=unwrap_or(config.custom_id, MISSING),
                required=config.required,
                min_values=config.min_values,
                max_values=config.max_values,
                id=config.id,
            )
        case RadioGroup():
            return ui.RadioGroup(
                custom_id=unwrap_or(config.custom_id, MISSING),
                required=config.required,
                options=list(config.options),
                id=config.id,
            )
        case CheckboxGroup():
            return ui.CheckboxGroup(
                custom_id=unwrap_or(config.custom_id, MISSING),
                required=config.required,
                min_values=config.min_values,
                max_values=config.max_values,
                options=list(config.options),
                id=config.id,
            )
        case Checkbox():
            return ui.Checkbox(
                custom_id=unwrap_or(config.custom_id, MISSING),
                default=config.default,
                id=config.id,
            )
        case _:
            raise TypeError(f'{type(config).__name__} is not a valid modal input.')


type ModalCallback = Callable[[Interaction], Result | Awaitable[Result]]


def _to_ui_modal_item(
    item: ModalItemType,
) -> tuple[ui.Item[BaseView], tuple[ModalInputItem, ui.Item[BaseView]] | None]:
    if isinstance(item, TextDisplay):
        # discord.py types TextDisplay against LayoutView even though Modal formally accepts it.
        return ui.TextDisplay(item.content, id=item.id), None  # type: ignore[return-value, reportUnknownVariableType]
    if isinstance(item, Label):
        child = _to_ui_input(item.component.item)
        label = ui.Label(text=item.text, component=child, description=item.description, id=item.id)
        label.row = item.row
        return label, (item.component, child)
    if isinstance(item, ModalItem) and isinstance(item.item, TextInput):
        child = _to_ui_input(item.item)
        return child, (item, child)
    raise TypeError(f'{type(item).__name__} is not a valid modal top-level item.')


class _InnerModal(ui.Modal):
    fut: Future[Result]

    def __init__(
        self,
        config: ModalConfig,
        items: Sequence[ModalItemType],
        callback: ModalCallback,
        *,
        controller: Controller | None = None,
        on_timeout: MaybeAwaitableFunc[[], Any] | None = None,
    ) -> None:
        super().__init__(title=config.title, timeout=config.timeout, custom_id=unwrap_or(config.custom_id, MISSING))
        self.callback = callback
        self.controller = controller
        self._on_timeout = on_timeout
        self._submit_started = False
        self.fut = get_running_loop().create_future()
        bindings: list[tuple[ModalInputItem, ui.Item[BaseView]]] = []
        for item in items:
            child, binding = _to_ui_modal_item(item)
            self.add_item(child)
            if binding is not None:
                bindings.append(binding)
        for modal_item, child in bindings:
            modal_item._bind(child)
        self._modal_items = tuple(modal_item for modal_item, _ in bindings)

    async def on_submit(self, interaction: Interaction) -> None:
        if self.fut.done():
            self.stop()
            return
        self._submit_started = True
        try:
            for item in self._modal_items:
                item._mark_submitted()
            context = nullcontext() if self.controller is None else self.controller._set_to_context()
            with context:
                result = await maybe_coroutine(self.callback, interaction)
        except BaseException as exception:
            if not self.fut.done():
                self.fut.set_exception(exception)
            raise
        else:
            if result._interaction is None:
                result = replace(result, _interaction=interaction)
            if not self.fut.done():
                self.fut.set_result(result)
        finally:
            self.stop()

    async def on_timeout(self) -> None:
        try:
            if self._on_timeout is not None:
                await maybe_coroutine(self._on_timeout)
        except BaseException as exception:
            if not self._submit_started and not self.fut.done():
                self.fut.set_exception(exception)
            else:
                logger.exception('Modal timeout handler failed.')
        else:
            if not self._submit_started and not self.fut.done():
                self.fut.cancel()

    async def _wait(self) -> Result:
        try:
            return await self.fut
        finally:
            self.stop()


async def send_modal(  # noqa: PLR0913
    callback: ModalCallback,
    interaction: Interaction,
    config: ModalConfig,
    items: Sequence[ModalItemType],
    *,
    controller: Controller | None = None,
    on_timeout: MaybeAwaitableFunc[[], Any] | None = None,
) -> ExternalResultTask:
    """Send a modal and register its submit result with the active flow.

    Read each submitted input through the ``value`` property of the ModalItem captured by the callback.

    The timeout is handled by :class:`discord.ui.Modal`: when it expires before submission, the external result task
    is cancelled after ``on_timeout`` returns. If submission has already started, its callback result is preserved.
    A timeout handler failure before submission is delivered through the external result task; after submission starts,
    the failure is logged while the submit result remains authoritative.

    Args:
        callback: Callback invoked with the interaction that submitted the modal.
        interaction: Interaction used to send the modal.
        config: :class:`discord.ui.Modal` configuration, including its timeout.
        items: Modal fields and display items.
        controller: Controller that owns this modal. If omitted, the active flow controller is used.
        on_timeout: Optional callback invoked when the modal's configured timeout expires. Its return value is
            discarded. If it raises before submission, the exception is propagated to the modal's external result. If
            submission has started, the exception is logged and does not replace the submit result.
    """
    if controller is None:
        controller = _get_controller()
    modal = _InnerModal(config=config, items=items, callback=callback, controller=controller, on_timeout=on_timeout)
    await interaction.response.send_modal(modal)
    try:
        return controller.create_external_result(modal._wait, name='modal-wait', life_time=ExternalTaskLifeTime.MODEL)
    except BaseException:
        modal.stop()
        raise
