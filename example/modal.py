from __future__ import annotations

from os import getenv

from discord import Client, Embed, Intents, Interaction
from discord.app_commands import CommandTree
from discord.ext.flow import Button, Controller, Message, ModalConfig, ModalController, ModelBase, Result, TextInput


class EmbedModel(ModelBase):
    def __init__(self, title: str) -> None:
        self.embed = Embed(title=title, description='')
        self.title_modal = ModalController(
            ModalConfig(title='Edit Title'),
            (TextInput(label='title', default=self.embed.title),),
        )
        self.description_modal = ModalController(
            ModalConfig(title='Edit Description'),
            (TextInput(label='description', default=self.embed.description),),
        )
        self.title_and_description_modal = ModalController(
            ModalConfig(title='Edit Title and Description'),
            (
                TextInput(label='title', default=self.embed.title),
                TextInput(label='description', default=self.embed.description),
            ),
        )

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

    async def after_invoke(self) -> None:
        self.title_modal.stop()
        self.description_modal.stop()
        self.title_and_description_modal.stop()

    def edit_title_button(self) -> Button:
        async def inner(interaction: Interaction[Client]) -> Result:
            result = await self.title_modal.send_modal(interaction)
            assert len(result.texts) >= 1
            self.embed.title = result.texts[0]
            return Result.send_message(message=self.message(), interaction=result.interaction)

        return Button(label='edit title', callback=inner)

    def edit_description_button(self) -> Button:
        async def inner(interaction: Interaction[Client]) -> Result:
            result = await self.description_modal.send_modal(interaction)
            assert len(result.texts) >= 1
            self.embed.description = result.texts[0]
            return Result.send_message(message=self.message(), interaction=result.interaction)

        return Button(label='edit description', callback=inner)

    def edit_title_and_description_button(self) -> Button:
        async def inner(interaction: Interaction[Client]) -> Result:
            result = await self.title_and_description_modal.send_modal(interaction)
            assert len(result.texts) >= 2
            self.embed.title = result.texts[0]
            self.embed.description = result.texts[1]
            return Result.send_message(message=self.message(), interaction=result.interaction)

        return Button(label='edit title and description', callback=inner)

    def finish_button(self) -> Button:
        async def inner(_: Interaction[Client]) -> Result:
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
async def embed(interaction: Interaction[Client], title: str) -> None:
    await Controller(EmbedModel(title)).invoke(interaction)


client.run(getenv('TOKEN', ''))
