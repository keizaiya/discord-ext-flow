from __future__ import annotations

from os import getenv
from typing import TYPE_CHECKING

from discord import Client, Embed, Intents, Interaction
from discord.app_commands import CommandTree
from discord.ext.flow import Button, Controller, Message, ModelBase, Paginator, paginator

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class Pagination(ModelBase):
    @paginator
    def message(self) -> Paginator[str]:
        return Paginator(self.message_builder, values=tuple(str(i) for i in range(100)))

    def message_builder(self, msgs: Sequence[str], current: int, max_page: int) -> Message:
        return Message(
            embeds=[Embed(title=f'{current}/{max_page}', description='\n'.join(msgs))],
            items=tuple(Button(label=f'{int(i)+1}', callback=self.button_callback(i)) for i in msgs),
            disable_items=True,
        )

    def button_callback(self, state: str) -> Callable[[Interaction[Client]], Message]:
        def callback(_: Interaction[Client]) -> Message:
            return Message(content=state)

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
    await Controller(Pagination()).invoke(interaction)


client.run(getenv('TOKEN', ''))
