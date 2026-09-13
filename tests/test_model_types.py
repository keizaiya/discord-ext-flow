from __future__ import annotations

# pyright: reportUnnecessaryTypeIgnoreComment=error
from typing import TYPE_CHECKING, assert_type

if TYPE_CHECKING:
    from datetime import timedelta

    from discord import Poll
    from discord.ext.flow import ComponentV2Message, TextDisplay, create_message

    def _check_create_message_v2_overload() -> None:  # type: ignore[reportUnusedFunction]
        message = create_message(items=(TextDisplay('content'),))
        assert_type(message, ComponentV2Message)

        explicit_defaults = create_message(
            items=(TextDisplay('content'),),
            content=None,
            embeds=None,
            poll=None,
            tts=False,
            suppress_embeds=False,
        )
        assert_type(explicit_defaults, ComponentV2Message)

    def _check_create_message_rejects_v2_fields() -> None:  # type: ignore[reportUnusedFunction]
        items = (TextDisplay('content'),)
        create_message(items=items, content='invalid')  # type: ignore[arg-type, call-overload, reportCallIssue]
        create_message(items=items, embeds=())  # type: ignore[arg-type, call-overload, reportCallIssue]
        create_message(items=items, poll=Poll('Question?', timedelta(hours=1)))  # type: ignore[arg-type, call-overload, reportCallIssue]
        create_message(items=items, tts=True)  # type: ignore[arg-type, call-overload, reportCallIssue]
        create_message(items=items, suppress_embeds=True)  # type: ignore[arg-type, call-overload, reportCallIssue]
