from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord import Interaction

    from .model import ComponentV2Message, LegacyMessage, ModelBase


__all__ = ('Result',)


class _ResultTypeEnum(Enum):
    MESSAGE = auto()
    MODEL = auto()
    CONTINUE = auto()
    FINISH = auto()


@dataclass(frozen=True)
class Result:
    """Describe the next flow action using one of the class methods, rather than constructing this directly.

    The ``interaction`` argument to :meth:`send_message` and :meth:`next_model` is optional. UI and modal callbacks
    automatically use their triggering interaction when it is omitted; an explicit interaction takes precedence
    for that result. External results without an interaction use the controller's retained delivery context.
    Modal interactions apply to their result response and previous-screen cleanup only and do not become the
    destination for later external results. See :meth:`Controller.invoke` for destination and visibility rules.
    """

    _type: _ResultTypeEnum
    _model: ModelBase | None = None
    _message: ComponentV2Message | LegacyMessage | None = None
    _interaction: Interaction | None = None
    _is_end: bool = False

    @classmethod
    def send_message(
        cls,
        message: ComponentV2Message | LegacyMessage,
        interaction: Interaction | None = None,
    ) -> Result:
        """Send message and same model.

        Args:
            message (ComponentV2Message | LegacyMessage): message to send.
            interaction (Interaction | None): Interaction for this result; see :class:`Result` for inference rules.
        """
        return Result(_type=_ResultTypeEnum.MESSAGE, _message=message, _interaction=interaction)

    @classmethod
    def next_model(cls, model: ModelBase, interaction: Interaction | None = None) -> Result:
        """Send message and next flow.

        Model transitions are determined by ``!=``, so models must implement this comparison appropriately.

        Args:
            model (ModelBase): next model.
            interaction (Interaction | None): Interaction for this result; see :class:`Result` for inference rules.
        """
        return Result(_type=_ResultTypeEnum.MODEL, _model=model, _interaction=interaction)

    @classmethod
    def continue_flow(cls) -> Result:
        """Lib wait next interaction. you should consume interaction you got."""
        return Result(_type=_ResultTypeEnum.CONTINUE)

    @classmethod
    def finish_flow(cls) -> Result:
        """Stop flow. you should consume interaction you got."""
        return Result(_type=_ResultTypeEnum.FINISH, _is_end=True)
