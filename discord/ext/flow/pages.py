from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

from discord.utils import maybe_coroutine

from .modal import ModalConfig, TextInput, send_modal
from .model import Button, Message, ModelBase

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from typing import Any

    from discord import Client, Interaction
    from discord.utils import MaybeAwaitableFunc

    S = TypeVar('S', bound=ModelBase)

__all__ = ('Paginator', 'paginator')

T = TypeVar('T')

FIRST_EMOJI = '\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f'
PREVIOUS_EMOJI = '\N{BLACK LEFT-POINTING TRIANGLE}\ufe0f'
NEXT_EMOJI = '\N{BLACK RIGHT-POINTING TRIANGLE}\ufe0f'
LAST_EMOJI = '\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f'


class Paginator(Generic[T]):
    """Paginator. This class is used to paginate messages.

    You should use `paginator` decorator and return this class instance.

    Args:
        message_builder (MaybeAwaitableFunc[[Sequence[T], int, int], Message]):
            generate message from separated values.
            Arguments are Separated values, current page number and max page number.
        values (Sequence[T]): values to paginate.
        per_page (int, optional): items per page. Defaults to 10.
        start_page (int, optional): start page number. Defaults to 0.
        row (int, optional): row number of control buttons. Defaults to 4.
    """

    message_builder: MaybeAwaitableFunc[[Sequence[T], int, int], Message]
    values: Sequence[T]
    per_page: int
    max_page: int
    current_page: int = 0

    def __init__(
        self,
        message_builder: MaybeAwaitableFunc[[Sequence[T], int, int], Message],
        values: Sequence[T],
        per_page: int = 10,
        start_page: int = 0,
        row: int = 4,
    ) -> None:
        self.message_builder = message_builder
        self.values = values
        self.current_page = start_page
        self.per_page = per_page
        div, mod = divmod(len(values), per_page)
        self.max_page = div + (mod != 0)
        self.row = row

    async def _message(self, *, edit_original: bool = False) -> Message:
        msg = await maybe_coroutine(
            self.message_builder,
            self.values[self.per_page * self.current_page : self.per_page * (self.current_page + 1)],
            self.current_page,
            self.max_page,
        )
        items = () if msg.items is None else tuple(msg.items)
        if len(items) > 20:
            raise ValueError('Message.items must be less than 20')

        disabled = self.max_page == 0
        control_items: tuple[Button, ...] = (
            Button(emoji=FIRST_EMOJI, row=self.row, disabled=disabled, callback=self._go_to_first_page),
            Button(emoji=PREVIOUS_EMOJI, row=self.row, disabled=disabled, callback=self._go_to_previous_page),
            Button(
                label=f'{self.current_page + 1}/{self.max_page}' if self.max_page > 0 else '1',
                row=self.row,
                disabled=disabled,
                callback=self._go_to_page,
            ),
            Button(emoji=NEXT_EMOJI, row=self.row, disabled=disabled, callback=self._go_to_next_page),
            Button(emoji=LAST_EMOJI, row=self.row, disabled=disabled, callback=self._go_to_last_page),
        )

        return msg._replace(items=items + control_items, edit_original=edit_original)

    def _set_page_number(self, page_number: int) -> None:
        if 0 <= page_number < self.max_page:
            self.current_page = page_number

    async def _go_to_first_page(self, _: Interaction[Client]) -> Message:
        self._set_page_number(0)
        return await self._message(edit_original=True)

    async def _go_to_previous_page(self, _: Interaction[Client]) -> Message:
        self._set_page_number(self.current_page - 1)
        return await self._message(edit_original=True)

    async def _go_to_page(self, interaction: Interaction[Client]) -> tuple[Message, Interaction[Client]]:
        texts, interaction = await send_modal(
            interaction,
            ModalConfig(title='Page Number'),
            (TextInput(label='page number', placeholder=f'1 ~ {self.max_page}', required=True),),
        )
        assert len(texts) >= 1
        assert texts[0].isdigit()
        self._set_page_number(int(texts[0]) - 1)
        msg = await self._message(edit_original=True)
        return (msg, interaction)

    async def _go_to_next_page(self, _: Interaction[Client]) -> Message:
        self._set_page_number(self.current_page + 1)
        return await self._message(edit_original=True)

    async def _go_to_last_page(self, _: Interaction[Client]) -> Message:
        self._set_page_number(self.max_page - 1)
        return await self._message(edit_original=True)


def paginator(func: MaybeAwaitableFunc[[S], Paginator[Any]]) -> Callable[[S], Awaitable[Message]]:
    """Decorator to paginate message. This decorator wraps function in ModelBase.message.

    Args:
        func (MaybeAwaitableFunc[[S], Paginator[Any]]): function to generate Paginator.

    Returns:
        Callable[[S], Awaitable[Message]]: wrapped function.

    """

    async def wrapper(self: S) -> Message:
        return await (await maybe_coroutine(func, self))._message()

    return wrapper
