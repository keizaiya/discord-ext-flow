from __future__ import annotations

from typing import TYPE_CHECKING, TypeGuard
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import Interaction, ui
from discord.ext.flow import (
    ActionRow,
    Button,
    ComponentV2Message,
    ComponentV2Paginator,
    Container,
    ExternalResultTask,
    InteractiveItem,
    LegacyMessage,
    ModalCallback,
    ModalConfig,
    ModalItemType,
    ModelBase,
    Paginator,
    PaginatorControls,
    Result,
    Section,
    TextDisplay,
    paginator,
)
from discord.ext.flow.controller import Controller
from discord.ext.flow.modal import _InnerModal
from discord.ext.flow.pages import NEXT_EMOJI
from discord.ext.flow.result import _ResultTypeEnum
from discord.ext.flow.view import create_view
from discord.utils import maybe_coroutine

if TYPE_CHECKING:
    from collections.abc import Sequence

    from discord.ext.flow import ActionRowItemType, InteractiveButton


def _finish(_: Interaction) -> Result:
    return Result.finish_flow()


class _ModalTask(ExternalResultTask):
    cancelled = False

    def __init__(self) -> None:
        self.cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


class _Model(ModelBase):
    def message(self) -> LegacyMessage:
        return LegacyMessage()


def _interaction() -> Interaction:
    return MagicMock(spec=Interaction)


def _is_interactive_button(item: ActionRowItemType) -> TypeGuard[InteractiveButton]:
    return isinstance(item, InteractiveItem) and isinstance(item.item, Button)


@pytest.mark.asyncio
async def test_legacy_paginator_retains_page_and_row_behavior() -> None:
    """The shared paginator internals preserve the existing legacy API and layout."""
    calls: list[tuple[tuple[int, ...], int, int]] = []

    def builder(values: tuple[int, ...], current_page: int, max_page: int) -> LegacyMessage:
        calls.append((values, current_page, max_page))
        return LegacyMessage(content='page', items=(Button(label='Finish').on(callback=_finish),))

    pagination = Paginator(builder, values=range(12), per_page=5, start_page=1, row=3)
    message = await pagination._message()

    assert calls == [((5, 6, 7, 8, 9), 1, 3)]
    assert message.content == 'page'
    assert message.items is not None
    assert len(message.items) == 6
    controls = message.items[1:]
    assert all(
        isinstance(item, InteractiveItem) and isinstance(item.item, Button) and item.item.row == 3 for item in controls
    )
    assert isinstance(controls[2], InteractiveItem)
    assert isinstance(controls[2].item, Button)
    assert controls[2].item.label == '2/3'


@pytest.mark.asyncio
async def test_component_v2_paginator_places_controls_in_container_and_changes_page() -> None:
    """V2 controls can be nested in a container and retain their navigation callbacks."""
    calls: list[tuple[tuple[int, ...], int, int]] = []

    def builder(
        values: tuple[int, ...], current_page: int, max_page: int, controls: PaginatorControls
    ) -> ComponentV2Message:
        calls.append((values, current_page, max_page))
        return ComponentV2Message(
            items=(
                Container(
                    items=(
                        TextDisplay('\n'.join(map(str, values))),
                        ActionRow(items=controls),
                    ),
                ),
            ),
        )

    pagination = ComponentV2Paginator(builder, values=range(12), per_page=5)
    message = await pagination._message()

    container = message.items[0]
    assert isinstance(container, Container)
    row = container.items[1]
    assert isinstance(row, ActionRow)
    first, previous, page, next_button, last = row.items
    assert all(
        isinstance(item, InteractiveItem) and isinstance(item.item, Button) and item.item.row is None
        for item in row.items
    )
    assert isinstance(first, InteractiveItem)
    assert isinstance(first.item, Button)
    assert first.item.disabled
    assert isinstance(previous, InteractiveItem)
    assert isinstance(previous.item, Button)
    assert previous.item.disabled
    assert isinstance(page, InteractiveItem)
    assert isinstance(page.item, Button)
    assert page.item.label == '1/3'
    assert not page.item.disabled
    assert isinstance(next_button, InteractiveItem)
    assert isinstance(next_button.item, Button)
    assert next_button.item.emoji == NEXT_EMOJI
    assert not next_button.item.disabled
    assert isinstance(last, InteractiveItem)
    assert isinstance(last.item, Button)
    assert not last.item.disabled

    assert _is_interactive_button(next_button)
    result = await maybe_coroutine(next_button.callback, _interaction())

    assert calls == [((0, 1, 2, 3, 4), 0, 3), ((5, 6, 7, 8, 9), 1, 3)]
    assert isinstance(result._message, ComponentV2Message)
    assert result._message.edit_original


@pytest.mark.asyncio
async def test_component_v2_paginator_controls_can_be_split_and_reordered() -> None:
    """Named controls allow layouts other than the standard five-button action row."""

    async def builder(_: tuple[int, ...], __: int, ___: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(
            items=(
                ActionRow(items=(controls.previous, controls.first)),
                Section(items=('Choose a page',), accessory=controls.page),
                ActionRow(items=(controls.last, controls.next)),
            ),
        )

    message = await ComponentV2Paginator(builder, values=range(20))._message()

    first_row, section, last_row = message.items
    assert isinstance(first_row, ActionRow)
    assert isinstance(section, Section)
    assert isinstance(last_row, ActionRow)
    first, previous = first_row.items
    assert _is_interactive_button(first)
    assert _is_interactive_button(previous)
    assert [first.item.emoji, previous.item.emoji] == ['◀️', '⏮️']
    assert isinstance(section.accessory, InteractiveItem)
    assert isinstance(section.accessory.item, Button)
    assert section.accessory.item.label == '1/2'
    last, next_button = last_row.items
    assert _is_interactive_button(last)
    assert _is_interactive_button(next_button)
    assert [last.item.emoji, next_button.item.emoji] == ['⏭️', '▶️']


@pytest.mark.asyncio
async def test_component_v2_paginator_page_modal_edits_selected_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direct-page control keeps the existing modal-based navigation behavior."""
    captured: tuple[ModalCallback, ModalConfig, Sequence[ModalItemType]] | None = None
    task = _ModalTask()

    async def fake_send_modal(
        modal_callback: ModalCallback,
        _: Interaction,
        config: ModalConfig,
        items: Sequence[ModalItemType],
        **__: object,
    ) -> _ModalTask:
        nonlocal captured
        captured = (modal_callback, config, items)
        return task

    monkeypatch.setattr('discord.ext.flow.pages.send_modal', fake_send_modal)

    def builder(values: tuple[int, ...], _: int, __: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(items=(TextDisplay(str(values)), ActionRow(items=controls)))

    pagination = ComponentV2Paginator(builder, values=range(30))
    message = await pagination._message()
    row = message.items[1]
    assert isinstance(row, ActionRow)
    page = row.items[2]
    assert isinstance(page, InteractiveItem)
    assert isinstance(page.item, Button)
    assert _is_interactive_button(page)
    interaction = _interaction()

    result = await maybe_coroutine(page.callback, interaction)

    assert result == Result.continue_flow()
    assert captured is not None
    callback, config, items = captured
    submitted = _InnerModal(config, items, callback)
    label = submitted.children[0]
    assert isinstance(label, ui.Label)
    assert isinstance(label.component, ui.TextInput)
    await submitted._scheduled_task(
        interaction,
        [{'type': 18, 'component': {'type': 4, 'custom_id': label.component.custom_id, 'value': '2'}}],
        {},
    )
    selected = await submitted._wait()
    assert pagination.current_page == 1
    assert isinstance(selected._message, ComponentV2Message)
    assert selected._message.edit_original
    assert selected._interaction is interaction


@pytest.mark.parametrize('value', ['not a page', '0', '4'])
@pytest.mark.asyncio
async def test_component_v2_paginator_page_modal_acknowledges_invalid_page(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Invalid page submissions are acknowledged without rebuilding or changing the paginator."""
    captured: tuple[ModalCallback, ModalConfig, Sequence[ModalItemType]] | None = None
    task = _ModalTask()

    async def fake_send_modal(
        modal_callback: ModalCallback,
        _: Interaction,
        config: ModalConfig,
        items: Sequence[ModalItemType],
        **__: object,
    ) -> _ModalTask:
        nonlocal captured
        captured = (modal_callback, config, items)
        return task

    monkeypatch.setattr('discord.ext.flow.pages.send_modal', fake_send_modal)

    calls: list[tuple[tuple[int, ...], int, int]] = []

    def builder(
        values: tuple[int, ...], current: int, max_page: int, controls: PaginatorControls
    ) -> ComponentV2Message:
        calls.append((values, current, max_page))
        return ComponentV2Message(items=(TextDisplay(str(values)), ActionRow(items=controls)))

    pagination = ComponentV2Paginator(builder, values=range(30))
    message = await pagination._message()
    row = message.items[1]
    assert isinstance(row, ActionRow)
    page = row.items[2]
    assert _is_interactive_button(page)

    await maybe_coroutine(page.callback, _interaction())
    assert captured is not None
    callback, config, items = captured
    submitted = _InnerModal(config, items, callback)
    label = submitted.children[0]
    assert isinstance(label, ui.Label)
    assert isinstance(label.component, ui.TextInput)
    submit_interaction = MagicMock(spec=Interaction, **{'response.defer': AsyncMock()})

    await submitted._scheduled_task(
        submit_interaction,
        [{'type': 18, 'component': {'type': 4, 'custom_id': label.component.custom_id, 'value': value}}],
        {},
    )
    result = await submitted._wait()

    assert result._type is _ResultTypeEnum.CONTINUE
    submit_interaction.response.defer.assert_awaited_once_with()
    assert calls == [((0, 1, 2, 3, 4, 5, 6, 7, 8, 9), 0, 3)]
    assert pagination.current_page == 0


@pytest.mark.asyncio
async def test_nested_v2_callback_cancels_paginator_modal_tasks() -> None:
    """Callbacks nested in V2 layouts receive the paginator's modal cleanup wrapper."""
    task = _ModalTask()

    def builder(_: tuple[int, ...], __: int, ___: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(
            items=(
                Container(
                    items=(
                        ActionRow(items=(Button(label='Finish').on(callback=_finish),)),
                        ActionRow(items=controls),
                    ),
                ),
            ),
        )

    pagination = ComponentV2Paginator(builder, values=(1,))
    pagination.modal_tasks.append(task)
    message = await pagination._message()
    container = message.items[0]
    assert isinstance(container, Container)
    row = container.items[0]
    assert isinstance(row, ActionRow)
    finish = row.items[0]
    assert isinstance(finish, InteractiveItem)
    assert isinstance(finish.item, Button)

    assert _is_interactive_button(finish)
    await maybe_coroutine(finish.callback, _interaction())

    assert task.cancelled


@pytest.mark.asyncio
async def test_static_v2_callback_cancels_paginator_modal_tasks() -> None:
    """A non-dispatchable V2 result finishes pagination and cancels outstanding modals."""
    task = _ModalTask()

    def show_static(_: Interaction) -> Result:
        return Result.send_message(ComponentV2Message(items=(TextDisplay('Finished'),)))

    def builder(_: tuple[int, ...], __: int, ___: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(
            items=(
                ActionRow(items=(Button(label='Finish').on(callback=show_static),)),
                ActionRow(items=controls),
            ),
        )

    pagination = ComponentV2Paginator(builder, values=(1,))
    pagination.modal_tasks.append(task)
    message = await pagination._message()
    row = message.items[0]
    assert isinstance(row, ActionRow)
    finish = row.items[0]
    assert isinstance(finish, InteractiveItem)
    assert isinstance(finish.item, Button)

    assert _is_interactive_button(finish)
    await maybe_coroutine(finish.callback, _interaction())

    assert task.cancelled


@pytest.mark.asyncio
async def test_disabled_v2_callback_cancels_paginator_modal_tasks() -> None:
    """A result containing only disabled controls finishes pagination and cancels outstanding modals."""
    task = _ModalTask()

    def show_disabled(_: Interaction) -> Result:
        return Result.send_message(
            ComponentV2Message(
                items=(ActionRow(items=(Button(label='Disabled', disabled=True).on(callback=_finish),)),)
            )
        )

    def builder(_: tuple[int, ...], __: int, ___: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(
            items=(
                ActionRow(items=(Button(label='Finish').on(callback=show_disabled),)),
                ActionRow(items=controls),
            ),
        )

    pagination = ComponentV2Paginator(builder, values=(1,))
    pagination.modal_tasks.append(task)
    message = await pagination._message()
    row = message.items[0]
    assert isinstance(row, ActionRow)
    finish = row.items[0]
    assert isinstance(finish, InteractiveItem)
    assert isinstance(finish.item, Button)

    assert _is_interactive_button(finish)
    await maybe_coroutine(finish.callback, _interaction())

    assert task.cancelled


@pytest.mark.asyncio
async def test_disabled_section_callback_cancels_paginator_modal_tasks() -> None:
    """A result with only a disabled Section accessory finishes pagination and cancels outstanding modals."""
    task = _ModalTask()

    def show_disabled(_: Interaction) -> Result:
        return Result.send_message(
            ComponentV2Message(
                items=(
                    Section(
                        items=('Finished',),
                        accessory=Button(label='Disabled', disabled=True).on(callback=_finish),
                    ),
                )
            )
        )

    def builder(_: tuple[int, ...], __: int, ___: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(
            items=(
                ActionRow(items=(Button(label='Finish').on(callback=show_disabled),)),
                ActionRow(items=controls),
            ),
        )

    pagination = ComponentV2Paginator(builder, values=(1,))
    pagination.modal_tasks.append(task)
    message = await pagination._message()
    row = message.items[0]
    assert isinstance(row, ActionRow)
    finish = row.items[0]
    assert isinstance(finish, InteractiveItem)
    assert isinstance(finish.item, Button)

    assert _is_interactive_button(finish)
    await maybe_coroutine(finish.callback, _interaction())

    assert task.cancelled


@pytest.mark.asyncio
async def test_component_v2_paginator_respects_message_wide_component_limit() -> None:
    """Paginator layout conversion delegates Discord's 40-component limit to discord.py."""

    def builder(_: tuple[int, ...], __: int, ___: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(
            items=(*tuple(TextDisplay(str(index)) for index in range(35)), ActionRow(items=controls))
        )

    message = await ComponentV2Paginator(builder, values=(1,))._message()

    with pytest.raises(ValueError, match=r'maximum number of children exceeded \(40\)'):
        create_view({}, message.items, Controller(_Model()), _Model())


@pytest.mark.asyncio
async def test_paginator_decorator_supports_both_message_modes() -> None:
    """The existing decorator renders both paginator implementations."""

    @paginator
    def legacy() -> Paginator[int]:
        return Paginator(lambda *_: LegacyMessage(content='legacy'), values=())  # type: ignore[reportUnknownArgumentType]

    @paginator
    def component_v2() -> ComponentV2Paginator[int]:
        return ComponentV2Paginator(
            lambda *args: ComponentV2Message(items=(TextDisplay(str(args[0])), ActionRow(items=args[3]))),  # type: ignore[reportUnknownArgumentType]
            values=(),
        )

    legacy_message = await legacy()
    component_v2_message = await component_v2()
    assert type(legacy_message) is LegacyMessage
    assert type(component_v2_message) is ComponentV2Message


@pytest.mark.asyncio
async def test_paginator_decorator_preserves_invalid_builder_attribute_error() -> None:
    """The public decorator keeps its historical dynamic-dispatch failure for invalid builder results."""

    def invalid_builder() -> object:
        return object()

    # The public overload rejects this deliberately invalid runtime input.
    wrapped = paginator(invalid_builder)  # type: ignore[arg-type, call-overload, reportUnknownVariableType]

    with pytest.raises(AttributeError, match='_message'):
        await wrapped()  # type: ignore[no-untyped-call]
