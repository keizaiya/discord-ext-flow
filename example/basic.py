from __future__ import annotations

from os import getenv
from typing import TYPE_CHECKING

from discord import Client, Intents, Interaction
from discord.app_commands import CommandTree
from discord.ext.flow import Button, Controller, Message, ModelBase

if TYPE_CHECKING:
    from collections.abc import Callable


class StartModel(ModelBase):
    def message(self) -> Message:
        return Message(
            content='start!',
            items=tuple(Button(label=f'{i+1}', callback=self.button_callback(i), row=1) for i in range(5)),
            disable_items=True,
        )

    def button_callback(self, state: int) -> Callable[..., SecondModel]:
        def button(_: Interaction[Client]) -> SecondModel:
            return SecondModel((state,))

        return button


class SecondModel(ModelBase):
    def __init__(self, status: tuple[int]) -> None:
        self.status = status

    def message(self) -> Message:
        return Message(
            content='second!',
            items=(
                Button(label='back!', callback=self.back_button),
                *tuple(Button(label=f'{i+1}', callback=self.button_callback(i), row=1) for i in range(5)),
            ),
            disable_items=True,
        )

    def back_button(self, _: Interaction[Client]) -> StartModel:
        return StartModel()

    def button_callback(self, state: int) -> Callable[..., ThirdModel]:
        def button(_: Interaction[Client]) -> ThirdModel:
            return ThirdModel((*self.status, state))

        return button


class ThirdModel(ModelBase):
    def __init__(self, status: tuple[int, int]) -> None:
        self.status = status

    def message(self) -> Message:
        return Message(
            content='third!',
            items=(
                Button(label='back!', callback=self.back_button),
                *tuple(Button(label=f'{i+1}', callback=self.button_callback(i), row=1) for i in range(5)),
            ),
            disable_items=True,
        )

    def back_button(self, _: Interaction[Client]) -> SecondModel:
        return SecondModel(self.status[:-1])

    def button_callback(self, state: int) -> Callable[..., FourthModel]:
        def button(_: Interaction[Client]) -> FourthModel:
            return FourthModel((*self.status, state))

        return button


class FourthModel(ModelBase):
    def __init__(self, status: tuple[int, int, int]) -> None:
        self.status = status

    def message(self) -> Message:
        return Message(
            content='fourth!',
            items=(
                Button(label='back!', callback=self.back_button),
                *tuple(Button(label=f'{i+1}', callback=self.button_callback(i), row=1) for i in range(5)),
            ),
            disable_items=True,
        )

    def back_button(self, _: Interaction[Client]) -> ThirdModel:
        return ThirdModel(self.status[:-1])

    def button_callback(self, state: int) -> Callable[..., FinishModel]:
        def button(_: Interaction[Client]) -> FinishModel:
            return FinishModel((*self.status, state))

        return button


class FinishModel(ModelBase):
    def __init__(self, status: tuple[int, int, int, int]) -> None:
        self.status = status

    def message(self) -> Message:
        return Message(content='finish!\n' + (', '.join(str(i + 1) for i in self.status)), disable_items=True)


class MyClient(Client):
    def __init__(self) -> None:
        super().__init__(intents=Intents.default())
        self.tree = CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()


client = MyClient()


@client.event
async def on_ready() -> None:
    assert client.user is not None
    print(f'Logged in as {client.user} (ID: {client.user.id})')
    print('------')


@client.tree.command(name='basic')
async def basic(interaction: Interaction[Client]) -> None:
    await Controller(StartModel()).invoke(interaction)


client.run(getenv('TOKEN', ''))
