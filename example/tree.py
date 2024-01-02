from __future__ import annotations

from os import getenv

from discord import Client, Intents, Interaction
from discord.app_commands import CommandTree
from discord.ext.flow import Button, Controller, Message, ModelBase

TREE_STRING = """
tree of this example
A
+- B
|  +- C
+- D
   +- E
   |  +- F
   |  +- G
   |     +- H
   +- I
      +- J
         +- K
            +- L
"""
TREE_DICT: dict[str, tuple[str, ...]] = {
    'A': ('B', 'D'),
    'B': ('C',),
    'C': (),
    'D': ('E', 'I'),
    'E': ('F', 'G'),
    'F': (),
    'G': ('H',),
    'H': (),
    'I': ('J',),
    'J': ('K',),
    'K': ('L',),
    'L': (),
}


class Model(ModelBase):
    def __init__(self, key: str) -> None:
        self.key = key

    def message(self) -> Message:
        return Message(
            content=f'{TREE_STRING if self.key == "A" else ""}\nnow: {self.key}',
            items=tuple(self.get_children(key) for key in TREE_DICT[self.key]),
            disable_items=True,
        )

    def get_children(self, key: str) -> Button:
        def children(_: Interaction[Client]) -> Model:
            return Model(key)

        return Button(label=key, callback=children)


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


@client.tree.command(name='tree')
async def tree(interaction: Interaction[Client]) -> None:
    await Controller(Model('A')).invoke(interaction)


client.run(getenv('TOKEN', ''))
