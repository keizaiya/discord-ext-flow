from __future__ import annotations

from typing import TYPE_CHECKING

from discord.utils import maybe_coroutine

from .util import sender
from .view import _View

if TYPE_CHECKING:
    from typing import Self

    from discord import Client, Interaction, Message as Message_
    from discord.abc import Messageable

    from .model import Message, ModelBase

__all__ = ('Controller',)


class Controller:
    """Flow controller.

    This class is responsible for sending messages and waiting for interactions.

    Args:
        initial_model (ModelBase): Initial model. This model will be sent first.
    """

    model: ModelBase

    def __init__(self, initial_model: ModelBase) -> None:
        self.model = initial_model

    def copy(self) -> Self:
        """Returns a copy of this controller.

        Returns:
            Controller: Copied controller.
        """
        return self.__class__(self.model)

    async def invoke(self, messageable: Messageable | Interaction[Client]) -> None:
        """Invoke flow.

        Args:
            messageable (Messageable | Interaction): Messageable or interaction to send first message.
        """
        model_or_msg: ModelBase = self.model
        while True:
            if (ret := await self._send(model_or_msg, messageable)) is None:
                break
            model_or_msg, messageable = ret

    async def _send(
        self,
        model: ModelBase,
        messageable: Messageable | Interaction[Client],
    ) -> tuple[ModelBase, Interaction[Client]] | None:
        await maybe_coroutine(model.before_invoke)
        msg = await maybe_coroutine(model.message)

        if msg.items is None:
            await self._interaction_send(messageable, msg, None)
            await maybe_coroutine(model.after_invoke)
            return None

        view = _View(await maybe_coroutine(model.view_config), msg.items)
        message = await self._interaction_send(messageable, msg, view)
        await view.wait()
        if msg.disable_items:
            for child in view.children:
                child.disabled = True  # type: ignore[reportGeneralTypeIssues, attr-defined]
            await message.edit(view=view)
        await maybe_coroutine(model.after_invoke)
        return view.result

    async def _interaction_send(
        self, messageable: Messageable | Interaction[Client], message: Message, view: _View | None
    ) -> Message_:
        kwargs = message._to_dict()
        if view is not None:
            kwargs['view'] = view

        return await sender(messageable, **kwargs)
