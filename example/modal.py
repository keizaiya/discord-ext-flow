from __future__ import annotations

from os import getenv

from discord import Client, Embed, Intents, Interaction
from discord.app_commands import CommandTree
from discord.ext.flow import Button, Controller, Message, ModalConfig, ModelBase, Result, TextInput, send_modal


class EmbedModel(ModelBase):
    def __init__(self, title: str) -> None:
        self.embed = Embed(title=title, description='')

    def message(self) -> Message:
        return Message(
            embeds=[self.embed],
            items=(
                self.edit_title_button(),
                self.edit_description_button(),
                self.edit_title_and_description_button(),
                self.finish_button(),
            ),
            edit_original=True,
            disable_items=True,
            ephemeral=True,
        )

    def edit_title_button(self) -> Button:
        async def callback(interaction: Interaction, texts: tuple[str]) -> Result:
            self.embed.title = texts[0]
            return Result.send_message(message=self.message(), interaction=interaction)

        async def inner(interaction: Interaction) -> Result:
            await send_modal(
                callback,
                interaction,
                ModalConfig(title='Edit Title'),
                (TextInput(label='title', default=self.embed.title),),
            )
            return Result.continue_flow()

        return Button(label='edit title', callback=inner)

    def edit_description_button(self) -> Button:
        async def callback(interaction: Interaction, texts: tuple[str]) -> Result:
            self.embed.description = texts[0]
            return Result.send_message(message=self.message(), interaction=interaction)

        async def inner(interaction: Interaction) -> Result:
            await send_modal(
                callback,
                interaction,
                ModalConfig(title='Edit Description'),
                (TextInput(label='description', default=self.embed.description),),
            )
            return Result.continue_flow()

        return Button(label='edit description', callback=inner)

    def edit_title_and_description_button(self) -> Button:
        async def callback(interaction: Interaction, texts: tuple[str, str]) -> Result:
            self.embed.title = texts[0]
            self.embed.description = texts[1]
            return Result.send_message(message=self.message(), interaction=interaction)

        async def inner(interaction: Interaction) -> Result:
            await send_modal(
                callback,
                interaction,
                ModalConfig(title='Edit Title and Description'),
                (
                    TextInput(label='title', default=self.embed.title),
                    TextInput(label='description', default=self.embed.description),
                ),
            )
            return Result.continue_flow()

        return Button(label='edit title and description', callback=inner)

    def finish_button(self) -> Button:
        async def inner(_: Interaction) -> Result:
            return Result.send_message(message=Message(embeds=[self.embed]))

        return Button(label='finish', callback=inner)


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


@client.tree.command(name='embed')
async def embed(interaction: Interaction, title: str) -> None:
    await Controller(EmbedModel(title)).invoke(interaction)


client.run(getenv('TOKEN', ''))
