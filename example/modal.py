from __future__ import annotations

from os import getenv

from discord import Client, Embed, Intents, Interaction, TextStyle
from discord.app_commands import CommandTree
from discord.ext.flow import (
    Button,
    ChannelSelect,
    Checkbox,
    CheckboxGroup,
    CheckboxGroupOption,
    Controller,
    FileUpload,
    InteractiveButton,
    Label,
    MentionableSelect,
    Message,
    ModalConfig,
    ModelBase,
    RadioGroup,
    RadioGroupOption,
    Result,
    RoleSelect,
    Select,
    SelectOption,
    TextDisplay,
    TextInput,
    UserSelect,
    send_modal,
)


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
                self.component_v2_inputs_button(),
                self.entity_selects_button(),
                self.preferences_button(),
                self.finish_button(),
            ),
            edit_original=True,
            disable_items=True,
            ephemeral=True,
        )

    def edit_title_button(self) -> InteractiveButton:
        async def inner(interaction: Interaction) -> Result:
            title = TextInput(default=self.embed.title).field()

            async def callback(interaction: Interaction) -> Result:
                self.embed.title = title.value
                return Result.send_message(message=self.message(), interaction=interaction)

            await send_modal(
                callback,
                interaction,
                ModalConfig(title='Edit Title'),
                (
                    Label(
                        text='Title',
                        component=title,
                    ),
                ),
            )
            return Result.continue_flow()

        return Button(label='edit title').on(callback=inner)

    def edit_description_button(self) -> InteractiveButton:
        async def inner(interaction: Interaction) -> Result:
            description = TextInput(style=TextStyle.paragraph, default=self.embed.description).field()

            async def callback(interaction: Interaction) -> Result:
                self.embed.description = description.value
                return Result.send_message(message=self.message(), interaction=interaction)

            await send_modal(
                callback,
                interaction,
                ModalConfig(title='Edit Description'),
                (
                    Label(
                        text='Description',
                        component=description,
                    ),
                ),
            )
            return Result.continue_flow()

        return Button(label='edit description').on(callback=inner)

    def edit_title_and_description_button(self) -> InteractiveButton:
        async def inner(interaction: Interaction) -> Result:
            title = TextInput(default=self.embed.title).field()
            description = TextInput(style=TextStyle.paragraph, default=self.embed.description).field()

            async def callback(interaction: Interaction) -> Result:
                self.embed.title = title.value
                self.embed.description = description.value
                return Result.send_message(message=self.message(), interaction=interaction)

            await send_modal(
                callback,
                interaction,
                ModalConfig(title='Edit Title and Description'),
                (
                    Label(
                        text='Title',
                        component=title,
                    ),
                    Label(
                        text='Description',
                        component=description,
                    ),
                ),
            )
            return Result.continue_flow()

        return Button(label='edit title and description').on(callback=inner)

    def component_v2_inputs_button(self) -> InteractiveButton:
        """Show TextDisplay plus the common native Modal V2 input types."""

        async def inner(interaction: Interaction) -> Result:
            topic = Select(
                custom_id='topic',
                options=(
                    SelectOption(label='Bug', value='bug'),
                    SelectOption(label='Feedback', value='feedback'),
                ),
            ).field()
            details = TextInput(
                custom_id='details',
                style=TextStyle.paragraph,
                required=False,
            ).field()
            files = FileUpload(
                custom_id='files',
                required=False,
                min_values=0,
                max_values=3,
            ).field()
            confirmed = Checkbox(custom_id='confirmed').field()

            async def callback(interaction: Interaction) -> Result:
                selected_topic = topic.value
                entered_details = details.value
                uploaded_files = files.value
                is_confirmed = confirmed.value
                self.embed.title = entered_details or self.embed.title
                self.embed.description = (
                    f'topic={selected_topic!r}; files={len(uploaded_files)}; confirmed={is_confirmed}\n'
                    f'{entered_details}'
                )
                return Result.send_message(message=self.message(), interaction=interaction)

            await send_modal(
                callback,
                interaction,
                ModalConfig(title='Component V2 modal inputs'),
                (
                    TextDisplay('Choose a topic, describe it, and optionally attach files.'),
                    Label(
                        text='Topic',
                        component=topic,
                    ),
                    Label(
                        text='Details',
                        description='Do not include secrets.',
                        component=details,
                    ),
                    Label(
                        text='Files',
                        component=files,
                    ),
                    Label(
                        text='I confirm the details are safe to share',
                        component=confirmed,
                    ),
                ),
            )
            return Result.continue_flow()

        return Button(label='component v2 inputs').on(callback=inner)

    def entity_selects_button(self) -> InteractiveButton:
        """Show every entity Select type that v2.7.1 permits inside a Modal Label."""

        async def inner(interaction: Interaction) -> Result:
            users = UserSelect(custom_id='users').field()
            roles = RoleSelect(custom_id='roles').field()
            mentionables = MentionableSelect(custom_id='mentionables').field()
            channels = ChannelSelect(custom_id='channels').field()

            async def callback(interaction: Interaction) -> Result:
                selected_users = users.value
                selected_roles = roles.value
                selected_mentionables = mentionables.value
                selected_channels = channels.value
                self.embed.description = (
                    f'users={len(selected_users)}, roles={len(selected_roles)}, '
                    f'mentionables={len(selected_mentionables)}, channels={len(selected_channels)}'
                )
                return Result.send_message(message=self.message(), interaction=interaction)

            await send_modal(
                callback,
                interaction,
                ModalConfig(title='Entity selects'),
                (
                    Label(text='Users', component=users),
                    Label(text='Roles', component=roles),
                    Label(
                        text='People or roles',
                        component=mentionables,
                    ),
                    Label(text='Channels', component=channels),
                ),
            )
            return Result.continue_flow()

        return Button(label='entity selects').on(callback=inner)

    def preferences_button(self) -> InteractiveButton:
        """Show the radio and checkbox-group Modal V2 inputs."""

        async def inner(interaction: Interaction) -> Result:
            priority = RadioGroup(
                custom_id='priority',
                options=(
                    RadioGroupOption(label='Low', value='low'),
                    RadioGroupOption(label='High', value='high'),
                ),
            ).field()
            categories = CheckboxGroup(
                custom_id='categories',
                min_values=0,
                max_values=2,
                options=(
                    CheckboxGroupOption(label='Documentation', value='docs'),
                    CheckboxGroupOption(label='Support', value='support'),
                ),
            ).field()

            async def callback(interaction: Interaction) -> Result:
                self.embed.description = f'priority={priority.value!r}; categories={categories.value!r}'
                return Result.send_message(message=self.message(), interaction=interaction)

            await send_modal(
                callback,
                interaction,
                ModalConfig(title='Preferences'),
                (
                    TextDisplay('These inputs are valid only as children of Modal Labels.'),
                    Label(
                        text='Priority',
                        component=priority,
                    ),
                    Label(
                        text='Categories',
                        component=categories,
                    ),
                ),
            )
            return Result.continue_flow()

        return Button(label='preferences').on(callback=inner)

    def finish_button(self) -> InteractiveButton:
        async def inner(_: Interaction) -> Result:
            return Result.send_message(message=Message(embeds=[self.embed]))

        return Button(label='finish').on(callback=inner)


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
