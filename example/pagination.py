from __future__ import annotations

from os import getenv
from random import randint
from typing import TYPE_CHECKING

from discord import Client, Embed, Intents, Interaction
from discord.app_commands import CommandTree
from discord.ext.flow import Button, Controller, Message, ModelBase, Paginator, paginator

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Pagination(ModelBase):
    count: int

    def __init__(self, count: int) -> None:
        self.count = count

    @paginator
    def message(self) -> Paginator[int]:
        return Paginator(self.message_builder, values=tuple(range(1, self.count + 1)))

    def message_builder(self, msgs: tuple[int, ...], current: int, max_page: int) -> Message:
        return Message(
            embeds=[Embed(title=f'{current}/{max_page}', description='\n'.join(str(i) for i in msgs))],
            items=tuple(Button(label=f'{i}', callback=self.button_callback(i)) for i in msgs),
            disable_items=True,
        )

    def button_callback(self, state: int) -> Callable[[Interaction[Client]], Awaitable[bool]]:
        async def callback(interaction: Interaction[Client]) -> bool:
            print(state)
            await interaction.response.defer()
            return False

        return callback


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
    await Controller(Pagination(randint(1, 100))).invoke(interaction)


client.run(getenv('TOKEN', ''))
