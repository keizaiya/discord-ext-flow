from __future__ import annotations

import warnings
from typing import cast

import pytest
from discord import Client, Interaction, TextStyle, ui
from discord.ext.flow import ModalConfig, Result, TextInput
from discord.ext.flow.modal import _InnerModal


def test_text_input_uses_discord_py_keyword_only_constructor_style() -> None:
    """TextInput rejects ambiguous positional fields like the corresponding discord.py components."""
    text_input = TextInput(
        label='Body',
        style=TextStyle.paragraph,
        custom_id='body',
        placeholder='Write here',
        default='Default',
        required=True,
        min_length=1,
        max_length=100,
        row=2,
    )

    assert text_input.style is TextStyle.paragraph
    assert text_input.custom_id == 'body'
    assert text_input.placeholder == 'Write here'
    assert text_input.default == 'Default'
    assert text_input.required
    assert text_input.min_length == 1
    assert text_input.max_length == 100
    assert text_input.row == 2
    assert text_input.description is None


@pytest.mark.asyncio
async def test_modal_uses_labels_without_deprecated_text_input_arguments() -> None:
    """Text inputs are nested in the Label structure required by Modal V2."""

    def callback(_: Interaction[Client], values: tuple[str]) -> Result:
        assert values == ('updated',)
        return Result.finish_flow()

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter('always')
        modal = _InnerModal(
            ModalConfig(title='Edit'),
            (TextInput(label='Title', description='New title', id=10, label_id=20),),
            callback,
        )

    assert not caught_warnings
    label = modal.children[0]
    assert isinstance(label, ui.Label)
    assert label.text == 'Title'
    assert label.description == 'New title'
    assert label.id == 20
    assert label.component is modal.text_inputs[0]
    assert modal.text_inputs[0].id == 10

    modal.text_inputs[0]._value = 'updated'
    await modal.on_submit(cast('Interaction[Client]', object()))
    assert await modal._wait() == Result.finish_flow()


@pytest.mark.asyncio
async def test_modal_delegates_text_input_rows_to_discord_py() -> None:
    """Rows are passed to discord.py without changing the V2 label layout."""
    modal = _InnerModal(
        ModalConfig(title='Ordered'),
        (
            TextInput(label='Last', row=4),
            TextInput(label='Implicit'),
            TextInput(label='First', row=1),
        ),
        lambda _interaction, _values: Result.finish_flow(),
    )

    assert [label.text for label in modal.children if isinstance(label, ui.Label)] == ['Last', 'Implicit', 'First']
    assert [text_input.row for text_input in modal.text_inputs] == [4, None, 1]

    with pytest.raises(ValueError, match='row cannot be negative or greater than or equal to 5'):
        _InnerModal(
            ModalConfig(title='Invalid'),
            (TextInput(label='Invalid', row=5),),
            lambda _interaction, _values: Result.finish_flow(),
        )


@pytest.mark.asyncio
async def test_modal_callback_exception_completes_waiter() -> None:
    """A failed callback is observable by the controller instead of hanging its waiter."""
    error = RuntimeError('callback failed')

    def callback(_: Interaction[Client], __: tuple[str]) -> Result:
        raise error

    modal = _InnerModal(ModalConfig(title='Edit'), (TextInput(label='Title'),), callback)

    with pytest.raises(RuntimeError, match='callback failed'):
        await modal.on_submit(cast('Interaction[Client]', object()))
    with pytest.raises(RuntimeError, match='callback failed'):
        await modal._wait()
    assert modal.is_finished()
