from __future__ import annotations

from typing import TYPE_CHECKING

from discord.utils import maybe_coroutine

from .util import send_helper
from .view import _View

if TYPE_CHECKING:
    from asyncio import Task
    from typing import Self

    from discord import Client, Interaction
    from discord.abc import Messageable

    from .model import ModelBase
    from .util import _Editable
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

    async def invoke(self, messageable: Messageable | Interaction[Client], message: _Editable | None = None) -> None:
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
        edit_target: _Editable | None,
    ) -> tuple[ModelBase, Interaction[Client] | Messageable, _Editable] | None:
        tasks: list[Task[None]] = []
        await maybe_coroutine(model.before_invoke)
        msg = await maybe_coroutine(model.message)

        if msg.items is None:
            await send_helper(messageable, msg, None, edit_target)
            await maybe_coroutine(model.after_invoke)
            return None

        view = _View(await maybe_coroutine(model.view_config), msg.items)
        message = await send_helper(messageable, msg, view, edit_target)

        if msg.external_result is not None:
            tasks.extend(msg.external_result._wait_result(view, message.channel))

        await view.wait()
        if msg.disable_items:
            for child in view.children:
                child.disabled = True  # type: ignore[reportGeneralTypeIssues, attr-defined]
            await message.edit(view=view)

        await maybe_coroutine(model.after_invoke)
        for task in tasks:
            task.cancel()

        return None if view.result is None else (*view.result, message)
