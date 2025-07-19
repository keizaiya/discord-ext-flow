from __future__ import annotations

from os import getenv

from discord import Client, Intents, Interaction
from discord.app_commands import CommandTree
from discord.ext.flow import Button, Controller, Message, ModelBase, Result


class StartModel(ModelBase):
    def message(self) -> Message:
        return Message(
            content='start!',
            items=tuple(self.button(i) for i in range(5)),
            disable_items=True,
        )

    def button(self, state: int) -> Button:
        def inner(_: Interaction) -> Result:
            return Result.next_model(model=SecondModel((state,)))

        return Button(label=f'{state + 1}', callback=inner, row=1)


class SecondModel(ModelBase):
    def __init__(self, status: tuple[int]) -> None:
        self.status = status

    def message(self) -> Message:
        return Message(
            content='second!',
            items=(
                Button(label='back!', callback=self.back_button),
                *tuple(self.button(i) for i in range(5)),
            ),
            disable_items=True,
        )

    def back_button(self, _: Interaction) -> Result:
        return Result.next_model(model=StartModel())

    def button(self, state: int) -> Button:
        def inner(_: Interaction) -> Result:
            return Result.next_model(model=ThirdModel((*self.status, state)))

        return Button(label=f'{state + 1}', callback=inner, row=1)


class ThirdModel(ModelBase):
    def __init__(self, status: tuple[int, int]) -> None:
        self.status = status

    def message(self) -> Message:
        return Message(
            content='third!',
            items=(
                Button(label='back!', callback=self.back_button),
                *tuple(self.button(i) for i in range(5)),
            ),
            disable_items=True,
        )

    def back_button(self, _: Interaction) -> Result:
        return Result.next_model(model=SecondModel(self.status[:-1]))

    def button(self, state: int) -> Button:
        def inner(_: Interaction) -> Result:
            return Result.next_model(model=FourthModel((*self.status, state)))

        return Button(label=f'{state + 1}', callback=inner, row=1)


class FourthModel(ModelBase):
    def __init__(self, status: tuple[int, int, int]) -> None:
        self.status = status

    def message(self) -> Message:
        return Message(
            content='fourth!',
            items=(
                Button(label='back!', callback=self.back_button),
                *tuple(self.button(i) for i in range(5)),
            ),
            disable_items=True,
        )

    def back_button(self, _: Interaction) -> Result:
        return Result.next_model(model=ThirdModel(self.status[:-1]))

    def button(self, state: int) -> Button:
        def inner(_: Interaction) -> Result:
            return Result.next_model(model=FinishModel((*self.status, state)))

        return Button(label=f'{state + 1}', callback=inner, row=1)


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
async def basic(interaction: Interaction) -> None:
    await Controller(StartModel()).invoke(interaction)


client.run(getenv('TOKEN', ''))
