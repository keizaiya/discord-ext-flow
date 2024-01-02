from __future__ import annotations

from typing import TYPE_CHECKING

from discord.utils import maybe_coroutine

from .view import _View

if TYPE_CHECKING:
    from typing import Self

    from discord import Client, Interaction, InteractionMessage, WebhookMessage

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

    async def invoke(self, interaction: Interaction[Client]) -> None:
        """Invoke flow.

        Args:
            interaction (Interaction): Interaction to invoke. This interaction will be used to send first message.
        """
        model_or_msg: ModelBase = self.model
        while True:
            if (ret := await self._send(model_or_msg, interaction)) is None:
                break
            model_or_msg, interaction = ret

    async def _send(
        self,
        model: ModelBase,
        interaction: Interaction[Client],
    ) -> tuple[ModelBase, Interaction[Client]] | None:
        await maybe_coroutine(model.before_invoke)
        msg = await maybe_coroutine(model.message)

        if msg.items is None:
            await self._interaction_send(interaction, msg, None)
            await maybe_coroutine(model.after_invoke)
            return None

        view = _View(await maybe_coroutine(model.view_config), msg.items)
        message = await self._interaction_send(interaction, msg, view)
        await view.wait()
        if msg.disable_items:
            for child in view.children:
                child.disabled = True  # type: ignore[reportGeneralTypeIssues, attr-defined]
            await message.edit(view=view)
        await maybe_coroutine(model.after_invoke)
        return view.result

    async def _interaction_send(
        self, interaction: Interaction[Client], message: Message, view: _View | None
    ) -> WebhookMessage | InteractionMessage:
        kwargs = message._to_dict()
        if view is not None:
            kwargs['view'] = view

        msg: WebhookMessage | InteractionMessage
        if interaction.response.is_done():
            msg = await interaction.followup.send(**kwargs, wait=True)
        else:
            await interaction.response.send_message(**kwargs)
            msg = await interaction.original_response()

        if message.delete_after is not None:
            await msg.delete(delay=message.delete_after)

        return msg
