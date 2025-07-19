from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord import Interaction

    from .model import Message, ModelBase


__all__ = ('Result',)


class _ResultTypeEnum(Enum):
    MESSAGE = auto()
    MODEL = auto()
    CONTINUE = auto()
    FINISH = auto()


@dataclass(frozen=True)
class Result:
    """You shouldn't construct this directly. use other classmethod instead."""

    _type: _ResultTypeEnum
    _model: ModelBase | None = None
    _message: Message | None = None
    _interaction: Interaction | None = None
    _is_end: bool = False

    @classmethod
    def send_message(cls, message: Message, interaction: Interaction | None = None) -> Result:
        """Send message and same model.

        Args:
            message (Message): message to send.
            interaction (Interaction, optional): new interaction to send message. Defaults to None.
        """
        return Result(_type=_ResultTypeEnum.MESSAGE, _message=message, _interaction=interaction)

    @classmethod
    def next_model(cls, model: ModelBase, interaction: Interaction | None = None) -> Result:
        """Send message and next flow.

        Args:
            model (ModelBase): next model.
            interaction (Interaction, optional): new interaction to send message. Defaults to None.
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
