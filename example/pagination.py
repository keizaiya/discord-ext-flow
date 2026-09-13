from __future__ import annotations

import os
from random import randint

from discord import Client, Embed, Intents, Interaction
from discord.app_commands import CommandTree
from discord.ext.flow import Button, InteractiveButton, Message, ModelBase, Paginator, Result, paginator, run_flow


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
            items=tuple(self.value_button(i) for i in msgs),
            disable_items=True,
        )

    def value_button(self, state: int) -> InteractiveButton:
        async def callback(interaction: Interaction) -> Result:
            print(state)
            await interaction.response.defer()
            return Result.finish_flow()

        return Button(label=str(state)).on(callback=callback)


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


@client.tree.command(name='pagination')
async def pagination(interaction: Interaction) -> None:
    await run_flow(Pagination(randint(1, 100)), interaction)


client.run(os.environ['DISCORD_TOKEN'])
