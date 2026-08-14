from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple, overload

from discord.utils import maybe_coroutine

from .item import (
    ActionRow,
    Button,
    Container,
    InteractiveButton,
    InteractiveItem,
    Label,
    Section,
    TextInput,
)
from .modal import ModalConfig, send_modal
from .model import ComponentV2Message, LegacyMessage
from .result import Result, _ResultTypeEnum
from .util import items_can_produce_result

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from discord import Interaction
    from discord.utils import MaybeAwaitableFunc

    from .external_task import ExternalResultTask
    from .item import ActionRowItemType, ContainerItemType, LegacyItemType, V2ItemType


__all__ = ('ComponentV2Paginator', 'Paginator', 'PaginatorControls', 'paginator')


FIRST_EMOJI = '\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f'
PREVIOUS_EMOJI = '\N{BLACK LEFT-POINTING TRIANGLE}\ufe0f'
NEXT_EMOJI = '\N{BLACK RIGHT-POINTING TRIANGLE}\ufe0f'
LAST_EMOJI = '\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f'


class PaginatorControls(NamedTuple):
    """Navigation buttons supplied to a Component V2 paginator message builder."""

    first: InteractiveButton
    previous: InteractiveButton
    page: InteractiveButton
    next: InteractiveButton
    last: InteractiveButton


def _result_finishes_pagination(result: Result) -> bool:
    if result._type in (_ResultTypeEnum.MODEL, _ResultTypeEnum.FINISH):
        return True
    if result._type != _ResultTypeEnum.MESSAGE or result._message is None:
        return False
    message = result._message
    return message.disable_items or not message.items or not items_can_produce_result(message.items)


class _BasePaginator[T, M: (ComponentV2Message, LegacyMessage)]:
    values: tuple[T, ...]
    per_page: int
    max_page: int
    current_page: int = 0
    modal_tasks: list[ExternalResultTask]

    def __init__(self, values: Sequence[T], per_page: int, start_page: int) -> None:
        self.values = tuple(values)
        self.current_page = start_page
        self.per_page = per_page
        div, mod = divmod(len(values), per_page)
        self.max_page = div + (mod != 0)
        self.modal_tasks = []

    async def _message(self, *, edit_original: bool = False) -> M:
        raise NotImplementedError

    def _create_controls(self, *, row: int | None = None) -> PaginatorControls:
        label = f'{self.current_page + 1}/{self.max_page}' if self.max_page > 0 else '1/1'

        not_paging = self.max_page == 0
        is_first_page = not_paging or self.current_page == 0
        is_final_page = not_paging or self.current_page == self.max_page - 1

        return PaginatorControls(
            first=Button(emoji=FIRST_EMOJI, row=row, disabled=is_first_page).on(callback=self._go_to_first_page),
            previous=Button(emoji=PREVIOUS_EMOJI, row=row, disabled=is_first_page).on(
                callback=self._go_to_previous_page
            ),
            page=Button(label=label, row=row, disabled=not_paging).on(callback=self._go_to_page),
            next=Button(emoji=NEXT_EMOJI, row=row, disabled=is_final_page).on(callback=self._go_to_next_page),
            last=Button(emoji=LAST_EMOJI, row=row, disabled=is_final_page).on(callback=self._go_to_last_page),
        )

    def _finalize_modal[**P](self, callback: MaybeAwaitableFunc[P, Result]) -> Callable[P, Awaitable[Result]]:
        async def finalize(*args: P.args, **kwargs: P.kwargs) -> Result:
            result = await maybe_coroutine(callback, *args, **kwargs)
            if _result_finishes_pagination(result):
                for task in self.modal_tasks:
                    task.cancel()
            return result

        return finalize

    def _set_page_number(self, page_number: int) -> None:
        if 0 <= page_number < self.max_page:
            self.current_page = page_number

    async def _go_to_first_page(self, _: Interaction) -> Result:
        self._set_page_number(0)
        return Result.send_message(message=await self._message(edit_original=True))

    async def _go_to_previous_page(self, _: Interaction) -> Result:
        self._set_page_number(self.current_page - 1)
        return Result.send_message(message=await self._message(edit_original=True))

    async def _go_to_page(self, interaction: Interaction) -> Result:
        page_number = TextInput(placeholder=f'1 ~ {self.max_page}').field()

        async def callback(interaction: Interaction) -> Result:
            text = page_number.value
            assert text.isdigit()
            self._set_page_number(int(text) - 1)
            return Result.send_message(message=await self._message(edit_original=True), interaction=interaction)

        task = await send_modal(
            callback,
            interaction,
            ModalConfig(title='Page Number'),
            (
                Label(
                    text='Page number',
                    component=page_number,
                ),
            ),
        )
        self.modal_tasks.append(task)
        self.modal_tasks[:] = [t for t in self.modal_tasks if not t.done()]

        return Result.continue_flow()

    async def _go_to_next_page(self, _: Interaction) -> Result:
        self._set_page_number(self.current_page + 1)
        return Result.send_message(message=await self._message(edit_original=True))

    async def _go_to_last_page(self, _: Interaction) -> Result:
        self._set_page_number(self.max_page - 1)
        return Result.send_message(message=await self._message(edit_original=True))


class Paginator[T](_BasePaginator[T, LegacyMessage]):
    """Paginator for legacy messages.

    You should use `paginator` decorator and return this class instance.

    Args:
        message_builder (MaybeAwaitableFunc[[tuple[T, ...], int, int], LegacyMessage]):
            generate message from separated values.
            Arguments are Separated values, current page number and max page number.
        values (Sequence[T]): values to paginate.
        per_page (int, optional): items per page. Defaults to 10.
        start_page (int, optional): start page number. Defaults to 0.
        row (int, optional): row number of control buttons. Defaults to 4.
    """

    message_builder: MaybeAwaitableFunc[[tuple[T, ...], int, int], LegacyMessage]

    def __init__(
        self,
        message_builder: MaybeAwaitableFunc[[tuple[T, ...], int, int], LegacyMessage],
        values: Sequence[T],
        per_page: int = 10,
        start_page: int = 0,
        row: int = 4,
    ) -> None:
        super().__init__(values, per_page, start_page)
        self.message_builder = message_builder
        self.row = row

    async def _message(self, *, edit_original: bool = False) -> LegacyMessage:
        msg = await maybe_coroutine(
            self.message_builder,
            self.values[self.per_page * self.current_page : self.per_page * (self.current_page + 1)],
            self.current_page,
            self.max_page,
        )
        items: list[LegacyItemType] = []
        if msg.items is not None:
            for item in msg.items:
                if isinstance(item, InteractiveItem):
                    item = item.item.on(callback=self._finalize_modal(item.callback))  # type: ignore[reportArgumentType,arg-type] # noqa: PLW2901
                items.append(item)
        if len(items) > 20:
            raise ValueError('LegacyMessage.items must be less than 20')

        return msg._replace(
            items=(*items, *self._create_controls(row=self.row)),
            edit_original=edit_original or msg.edit_original,
        )


class ComponentV2Paginator[T](_BasePaginator[T, ComponentV2Message]):
    """Paginator whose controls can be placed in a Component V2 layout.

    You should use `paginator` decorator and return this class instance. The message builder receives named navigation
    controls as its fourth argument. Place them in one or more `ActionRow` objects or use individual buttons as
    `Section` accessories.

    Args:
        message_builder (MaybeAwaitableFunc[[tuple[T, ...], int, int, PaginatorControls], ComponentV2Message]):
            Generate a Component V2 message from separated values, the zero-based current page number, the number of
            pages, and the navigation controls.
        values (Sequence[T]): values to paginate.
        per_page (int, optional): items per page. Defaults to 10.
        start_page (int, optional): start page number. Defaults to 0.
    """

    message_builder: MaybeAwaitableFunc[[tuple[T, ...], int, int, PaginatorControls], ComponentV2Message]

    def __init__(
        self,
        message_builder: MaybeAwaitableFunc[[tuple[T, ...], int, int, PaginatorControls], ComponentV2Message],
        values: Sequence[T],
        per_page: int = 10,
        start_page: int = 0,
    ) -> None:
        super().__init__(values, per_page, start_page)
        self.message_builder = message_builder

    async def _message(self, *, edit_original: bool = False) -> ComponentV2Message:
        msg = await maybe_coroutine(
            self.message_builder,
            self.values[self.per_page * self.current_page : self.per_page * (self.current_page + 1)],
            self.current_page,
            self.max_page,
            self._create_controls(),
        )
        items = tuple(self._finalize_v2_item(item) for item in msg.items)
        return msg._replace(items=items, edit_original=edit_original or msg.edit_original)

    def _finalize_container_item(self, item: ContainerItemType) -> ContainerItemType:
        if isinstance(item, ActionRow):
            items: list[ActionRowItemType] = []
            for child in item.items:
                if isinstance(child, InteractiveItem):
                    child = child.item.on(callback=self._finalize_modal(child.callback))  # type: ignore[reportArgumentType,arg-type] # noqa: PLW2901
                items.append(child)
            return replace(item, items=items)
        if isinstance(item, Section) and isinstance(item.accessory, InteractiveItem):
            return replace(
                item,
                accessory=item.accessory.item.on(callback=self._finalize_modal(item.accessory.callback)),
            )
        return item

    def _finalize_v2_item(self, item: V2ItemType) -> V2ItemType:
        if isinstance(item, Container):
            return replace(item, items=tuple(self._finalize_container_item(child) for child in item.items))
        return self._finalize_container_item(item)


@overload
def paginator[**P, T](
    func: MaybeAwaitableFunc[P, Paginator[T]],
) -> Callable[P, Awaitable[LegacyMessage]]: ...


@overload
def paginator[**P, T](
    func: MaybeAwaitableFunc[P, ComponentV2Paginator[T]],
) -> Callable[P, Awaitable[ComponentV2Message]]: ...


def paginator[**P, T](
    func: MaybeAwaitableFunc[P, Paginator[T] | ComponentV2Paginator[T]],
) -> Callable[P, Awaitable[LegacyMessage | ComponentV2Message]]:
    """Wrap a function returning a legacy or Component V2 paginator for use as `ModelBase.message`."""

    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> ComponentV2Message | LegacyMessage:
        paginator_instance = await maybe_coroutine(func, *args, **kwargs)
        # discord.py's maybe_coroutine stub discards the generic return type here.
        return await paginator_instance._message()  # type: ignore[no-any-return, attr-defined]

    return wrapper
