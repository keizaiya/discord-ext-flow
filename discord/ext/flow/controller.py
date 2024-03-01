from __future__ import annotations

from typing import TYPE_CHECKING

from discord.utils import maybe_coroutine

from .util import into_edit_kwargs, send_helper
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
        initial_model (ModelBase): Initial model. This model will be used first.
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

    async def invoke(self, messageable: Messageable | Interaction[Client], message: Message_ | None = None) -> None:
        """Invoke flow.

        Args:
            messageable (Messageable | Interaction): Messageable or interaction to send first message.
            message (discord.Message | None): The first target for editing if edit_original is True. Defaults to None.
        """
        model_or_msg: ModelBase = self.model
        while True:
            if (ret := await self._send(model_or_msg, messageable, message)) is None:
                break
            model_or_msg, messageable, message = ret

    async def _send(
        self,
        model: ModelBase,
        messageable: Messageable | Interaction[Client],
        edit_target: Message_ | None,
    ) -> tuple[ModelBase, Interaction[Client], Message_] | None:
        await maybe_coroutine(model.before_invoke)
        msg = await maybe_coroutine(model.message)

        if msg.items is None:
            await self._send_helper(messageable, msg, None, edit_target)
            await maybe_coroutine(model.after_invoke)
            return None

        view = _View(await maybe_coroutine(model.view_config), msg.items)
        message = await self._send_helper(messageable, msg, view, edit_target)

        await view.wait()
        if msg.disable_items:
            for child in view.children:
                child.disabled = True  # type: ignore[reportGeneralTypeIssues, attr-defined]
            await message.edit(view=view)

        await maybe_coroutine(model.after_invoke)

        return None if view.result is None else (*view.result, message)

    async def _send_helper(
        self,
        messageable: Messageable | Interaction[Client],
        message: Message,
        view: _View | None,
        edit_target: Message_ | None,
    ) -> Message_:
        kwargs = message._to_dict()
        if view is not None:
            kwargs['view'] = view

        if message.edit_original and edit_target is not None:
            return await edit_target.edit(**into_edit_kwargs(**kwargs))
        return await send_helper(messageable, **kwargs)
