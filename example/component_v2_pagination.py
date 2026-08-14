from __future__ import annotations

from os import getenv
from random import randint

from discord import Client, Intents, Interaction
from discord.app_commands import CommandTree
from discord.ext.flow import (
    ActionRow,
    Button,
    ComponentV2Message,
    ComponentV2Paginator,
    Container,
    Controller,
    ModelBase,
    PaginatorControls,
    Result,
    TextDisplay,
    paginator,
)


class Pagination(ModelBase):
    count: int

    def __init__(self, count: int) -> None:
        self.count = count

    @paginator
    def message(self) -> ComponentV2Paginator[int]:
        return ComponentV2Paginator(self.message_builder, values=tuple(range(1, self.count + 1)))

    def message_builder(
        self,
        msgs: tuple[int, ...],
        current: int,
        max_page: int,
        controls: PaginatorControls,
    ) -> ComponentV2Message:
        item_rows = tuple(
            ActionRow(items=tuple(self.button_callback(state) for state in msgs[offset : offset + 5]))
            for offset in range(0, len(msgs), 5)
        )
        return ComponentV2Message(
            items=(
                Container(
                    items=(
                        TextDisplay(f'# {current}/{max_page}\n' + '\n'.join(str(state) for state in msgs)),
                        *item_rows,
                        ActionRow(items=controls),
                    ),
                ),
            ),
            disable_items=True,
        )

    def button_callback(self, state: int) -> Button:
        async def callback(interaction: Interaction) -> Result:
            print(state)
            await interaction.response.defer()
            return Result.finish_flow()

        return Button(label=str(state), callback=callback)


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


@client.tree.command(name='component-v2-pagination')
async def component_v2_pagination(interaction: Interaction) -> None:
    await Controller(Pagination(randint(1, 100))).invoke(interaction)


client.run(getenv('TOKEN', ''))
