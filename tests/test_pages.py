from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from discord.ext.flow import (
    ActionRow,
    Button,
    ComponentV2Message,
    ComponentV2Paginator,
    Container,
    LegacyMessage,
    ModelBase,
    Paginator,
    PaginatorControls,
    Result,
    Section,
    TextDisplay,
    paginator,
)
from discord.ext.flow.controller import Controller
from discord.ext.flow.pages import NEXT_EMOJI
from discord.ext.flow.view import create_view
from discord.utils import maybe_coroutine

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from discord import Interaction
    from discord.ext.flow import ExternalResultTask


def _finish(_: Interaction) -> Result:
    return Result.finish_flow()


class _ModalTask:
    cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


class _Model(ModelBase):
    def message(self) -> LegacyMessage:
        return LegacyMessage()


@pytest.mark.asyncio
async def test_legacy_paginator_retains_page_and_row_behavior() -> None:
    """The shared paginator internals preserve the existing legacy API and layout."""
    calls: list[tuple[tuple[int, ...], int, int]] = []

    def builder(values: tuple[int, ...], current_page: int, max_page: int) -> LegacyMessage:
        calls.append((values, current_page, max_page))
        return LegacyMessage(content='page', items=(Button(label='Finish', callback=_finish),))

    pagination = Paginator(builder, values=range(12), per_page=5, start_page=1, row=3)
    message = await pagination._message()

    assert calls == [((5, 6, 7, 8, 9), 1, 3)]
    assert message.content == 'page'
    assert message.items is not None
    assert len(message.items) == 6
    controls = message.items[1:]
    assert all(isinstance(item, Button) and item.row == 3 for item in controls)
    assert isinstance(controls[2], Button)
    assert controls[2].label == '2/3'


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
    assert all(isinstance(item, Button) and item.row is None for item in row.items)
    assert isinstance(first, Button)
    assert first.disabled
    assert isinstance(previous, Button)
    assert previous.disabled
    assert isinstance(page, Button)
    assert page.label == '1/3'
    assert not page.disabled
    assert isinstance(next_button, Button)
    assert next_button.emoji == NEXT_EMOJI
    assert not next_button.disabled
    assert isinstance(last, Button)
    assert not last.disabled

    result = await maybe_coroutine(next_button.callback, cast('Interaction', object()))

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
    assert [cast('Button', item).emoji for item in first_row.items] == ['◀️', '⏮️']
    assert isinstance(section.accessory, Button)
    assert section.accessory.label == '1/2'
    assert [cast('Button', item).emoji for item in last_row.items] == ['⏭️', '▶️']


@pytest.mark.asyncio
async def test_component_v2_paginator_page_modal_edits_selected_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direct-page control keeps the existing modal-based navigation behavior."""
    callback: Callable[[Interaction, tuple[str]], Awaitable[Result]] | None = None
    task = _ModalTask()

    async def fake_send_modal(
        modal_callback: Callable[[Interaction, tuple[str]], Awaitable[Result]],
        *_: object,
        **__: object,
    ) -> _ModalTask:
        nonlocal callback
        callback = modal_callback
        return task

    monkeypatch.setattr('discord.ext.flow.pages.send_modal', fake_send_modal)

    def builder(values: tuple[int, ...], _: int, __: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(items=(TextDisplay(str(values)), ActionRow(items=controls)))

    pagination = ComponentV2Paginator(builder, values=range(30))
    message = await pagination._message()
    row = message.items[1]
    assert isinstance(row, ActionRow)
    page = row.items[2]
    assert isinstance(page, Button)
    interaction = cast('Interaction', object())

    result = await maybe_coroutine(page.callback, interaction)

    assert result == Result.continue_flow()
    assert callback is not None
    selected = await callback(interaction, ('2',))
    assert pagination.current_page == 1
    assert isinstance(selected._message, ComponentV2Message)
    assert selected._message.edit_original
    assert selected._interaction is interaction


@pytest.mark.asyncio
async def test_nested_v2_callback_cancels_paginator_modal_tasks() -> None:
    """Callbacks nested in V2 layouts receive the paginator's modal cleanup wrapper."""
    task = _ModalTask()

    def builder(_: tuple[int, ...], __: int, ___: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(
            items=(
                Container(
                    items=(
                        ActionRow(items=(Button(label='Finish', callback=_finish),)),
                        ActionRow(items=controls),
                    ),
                ),
            ),
        )

    pagination = ComponentV2Paginator(builder, values=(1,))
    pagination.modal_tasks.append(cast('ExternalResultTask', task))
    message = await pagination._message()
    container = message.items[0]
    assert isinstance(container, Container)
    row = container.items[0]
    assert isinstance(row, ActionRow)
    finish = row.items[0]
    assert isinstance(finish, Button)

    await maybe_coroutine(finish.callback, cast('Interaction', object()))

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
                ActionRow(items=(Button(label='Finish', callback=show_static),)),
                ActionRow(items=controls),
            ),
        )

    pagination = ComponentV2Paginator(builder, values=(1,))
    pagination.modal_tasks.append(cast('ExternalResultTask', task))
    message = await pagination._message()
    row = message.items[0]
    assert isinstance(row, ActionRow)
    finish = row.items[0]
    assert isinstance(finish, Button)

    await maybe_coroutine(finish.callback, cast('Interaction', object()))

    assert task.cancelled


@pytest.mark.asyncio
async def test_disabled_v2_callback_cancels_paginator_modal_tasks() -> None:
    """A result containing only disabled controls finishes pagination and cancels outstanding modals."""
    task = _ModalTask()

    def show_disabled(_: Interaction) -> Result:
        return Result.send_message(
            ComponentV2Message(items=(ActionRow(items=(Button(label='Disabled', callback=_finish, disabled=True),)),))
        )

    def builder(_: tuple[int, ...], __: int, ___: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(
            items=(
                ActionRow(items=(Button(label='Finish', callback=show_disabled),)),
                ActionRow(items=controls),
            ),
        )

    pagination = ComponentV2Paginator(builder, values=(1,))
    pagination.modal_tasks.append(cast('ExternalResultTask', task))
    message = await pagination._message()
    row = message.items[0]
    assert isinstance(row, ActionRow)
    finish = row.items[0]
    assert isinstance(finish, Button)

    await maybe_coroutine(finish.callback, cast('Interaction', object()))

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
                        accessory=Button(label='Disabled', callback=_finish, disabled=True),
                    ),
                )
            )
        )

    def builder(_: tuple[int, ...], __: int, ___: int, controls: PaginatorControls) -> ComponentV2Message:
        return ComponentV2Message(
            items=(
                ActionRow(items=(Button(label='Finish', callback=show_disabled),)),
                ActionRow(items=controls),
            ),
        )

    pagination = ComponentV2Paginator(builder, values=(1,))
    pagination.modal_tasks.append(cast('ExternalResultTask', task))
    message = await pagination._message()
    row = message.items[0]
    assert isinstance(row, ActionRow)
    finish = row.items[0]
    assert isinstance(finish, Button)

    await maybe_coroutine(finish.callback, cast('Interaction', object()))

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
        create_view({}, message.items, Controller(_Model()))


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
