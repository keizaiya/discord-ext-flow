from __future__ import annotations

import os

import discord
from discord.ext import commands
from discord.ext.flow import (
    ActionRow,
    Button,
    ComponentV2Message,
    Container,
    LegacyMessage,
    ModelBase,
    Result,
    TextDisplay,
    create_message,
    run_flow,
)

intents = discord.Intents.default()
# Enable Message Content Intent for this bot in the Developer Portal as well.
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)


class ComponentV2Flow(ModelBase):
    async def finish(self, interaction: discord.Interaction[discord.Client]) -> Result:
        await interaction.response.defer()
        return Result.finish_flow()

    def message(self) -> ComponentV2Message | LegacyMessage:
        return create_message(
            items=(
                Container(
                    items=(
                        TextDisplay('This text is rendered by a Component V2 layout.'),
                        ActionRow(items=(Button(label='Finish').on(callback=self.finish),)),
                    ),
                ),
            ),
        )


@bot.command()
async def component_v2(ctx: commands.Context[commands.Bot]) -> None:
    await run_flow(ComponentV2Flow(), ctx)


bot.run(os.environ['DISCORD_TOKEN'])
