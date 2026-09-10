"""Internal ownership of a flow's delivery context and displayed message."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, TypedDict

from discord import (
    DiscordException,
    Interaction,
    InteractionResponseType,
    InteractionType,
    Message as DiscordMessage,
    PartialMessage,
    ui,
)

from .model import ComponentV2Message, LegacyMessage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from discord import AllowedMentions, Attachment, Embed, File, Poll
    from discord.abc import Messageable
    from discord.ui import LayoutView, View

    from .model import MessageKwargs
    from .view import _ViewType

__all__ = ()


class FlowDisplay:
    """Own delivery and message replacement without managing models or tasks.

    ``message`` and ``view`` expose the current display for inspection. Only this
    class updates them; the controller owns waiting on and retiring view tasks.
    """

    def __init__(self) -> None:
        self._channel: Messageable | None = None
        self._interaction: Interaction | None = None
        self._pending_interaction: Interaction | None = None
        self.message: PartialMessage | None = None
        self.view: _ViewType | None = None
        self._disable_on_finish = False

    def start(self, messageable: Interaction | Messageable, message: PartialMessage | None = None) -> None:
        """Set the invocation destination and optional initial edit target."""
        if isinstance(messageable, Interaction):
            self._interaction = self._pending_interaction = messageable
        else:
            self._channel = messageable
        self.message = message

    def remember_interaction(self, interaction: Interaction) -> None:
        """Retain a UI interaction for later delivery, excluding modal submits."""
        if interaction.type is not InteractionType.modal_submit:
            self._interaction = interaction

    def begin_response(self, interaction: Interaction | None) -> None:
        """Use an accepted result's interaction through its response and old-screen cleanup."""
        self._pending_interaction = interaction
        if interaction is not None:
            self.remember_interaction(interaction)

    def complete_response(self) -> None:
        """Release the current response context while retaining the UI delivery context."""
        self._pending_interaction = None

    async def replace(self, message: ComponentV2Message | LegacyMessage, view: _ViewType | None) -> None:
        """Send the replacement and disable the previous message when its ID changes.

        A send failure leaves the current display intact. Once sending succeeds,
        the replacement becomes current even if disabling the old message fails.
        The controller can therefore reclaim view tasks using the resulting state.
        """
        sent = await self._send(message, view)
        try:
            if self.message is not None and self.message.id != sent.id:
                await self._disable_items()
        finally:
            self.message, self.view = sent, view
            self._disable_on_finish = message.disable_items
            self.complete_response()

    async def _send(  # noqa: C901 - Keep context selection and ordered delivery attempts together.
        self, message: ComponentV2Message | LegacyMessage, view: _ViewType | None
    ) -> DiscordMessage:
        """Select this operation's delivery context and try its edit/send routes.

        Return the actual Discord message without changing the active display;
        replacement owns activation and cleanup of the previous screen.
        """
        interaction = self._pending_interaction if self._pending_interaction is not None else self._interaction
        edit = self.message
        ephemeral = message.ephemeral
        if message.edit_original:
            if edit is None and interaction is not None:
                edit = interaction.message
            if isinstance(edit, DiscordMessage):
                ephemeral = ephemeral or edit.flags.ephemeral
        # External public results retain the invocation's ordinary destination.
        if self._pending_interaction is None and self._channel is not None and not ephemeral:
            interaction = None

        kwargs = message._to_dict()
        kwargs['ephemeral'] = ephemeral
        if view is not None:
            kwargs['view'] = view
        if message.edit_original:
            edit_kwargs = into_edit_kwargs(kwargs, components_v2=isinstance(message, ComponentV2Message))
            edit_kwargs['view'] = view
            edited = await _try_edit_interaction_message(interaction, edit, edit_kwargs)
            if edited is not None:
                return edited
            if edit is not None:
                with suppress(DiscordException):
                    return await _edit_existing_message(edit, edit_kwargs)

        delete_after = kwargs.get('delete_after')
        if interaction is not None:
            try:
                sent = await _send_interaction_response(interaction, message, kwargs, ephemeral=ephemeral)
            except DiscordException:
                if ephemeral or self._channel is None:
                    raise
            else:
                await _schedule_delete(sent, delete_after)
                return sent
        assert self._channel is not None, 'A messageable or interaction is required to send a flow message.'
        return await _send_to_messageable(self._channel, kwargs, delete_after=delete_after)

    async def _disable_items(self) -> None:
        if not self._disable_on_finish or self.view is None:
            return
        assert self.message is not None
        for child in self.view.walk_children():
            if isinstance(
                child,
                (ui.Button, ui.ChannelSelect, ui.MentionableSelect, ui.RoleSelect, ui.Select, ui.UserSelect),
            ):
                child.disabled = True
        try:
            await _edit_existing_message(self.message, {'view': self.view})
        except DiscordException:
            interaction = self._pending_interaction if self._pending_interaction is not None else self._interaction
            edited = await _try_edit_interaction_message(interaction, self.message, {'view': self.view})
            if edited is None:
                raise

    async def finish(self) -> None:
        """Disable the displayed controls and release display state even if the edit fails."""
        try:
            await self._disable_items()
        finally:
            self.message, self.view = None, None
            self._disable_on_finish = False
            self.complete_response()


class _SendHelperKWType(TypedDict, total=False):
    content: str
    tts: bool
    embeds: Sequence[Embed]
    files: Sequence[File]
    allowed_mentions: AllowedMentions
    view: LayoutView | View
    suppress_embeds: bool
    silent: bool
    poll: Poll


def into_send_kwargs(kwargs: MessageKwargs) -> _SendHelperKWType:
    """Build kwargs accepted by send endpoints.

    Send and edit APIs intentionally use separate converters: Discord names uploaded files differently for edits, and
    options such as TTS and silent delivery do not belong to the edit path.
    """
    kw: _SendHelperKWType = {}
    if 'content' in kwargs:
        kw['content'] = kwargs['content']
    if 'tts' in kwargs:
        kw['tts'] = kwargs['tts']
    if 'embeds' in kwargs:
        kw['embeds'] = kwargs['embeds']
    if 'files' in kwargs:
        kw['files'] = kwargs['files']
    if 'allowed_mentions' in kwargs:
        kw['allowed_mentions'] = kwargs['allowed_mentions']
    if 'view' in kwargs:
        kw['view'] = kwargs['view']
    if 'suppress_embeds' in kwargs:
        kw['suppress_embeds'] = kwargs['suppress_embeds']
    if 'silent' in kwargs:
        kw['silent'] = kwargs['silent']
    if 'poll' in kwargs:
        kw['poll'] = kwargs['poll']
    return kw


class _EditKWType(TypedDict, total=False):
    content: str | None
    embeds: Sequence[Embed]
    attachments: Sequence[Attachment | File]
    allowed_mentions: AllowedMentions
    view: LayoutView | View | None


def into_edit_kwargs(kwargs: MessageKwargs, *, components_v2: bool = False) -> _EditKWType:
    """Build kwargs accepted by message edit endpoints.

    Switching an existing legacy message to Component V2 requires clearing content and embeds in the same edit. Files
    also become the edit API's ``attachments`` argument, which is why send kwargs cannot be reused here.
    """
    kw: _EditKWType = {}
    if 'content' in kwargs:
        kw['content'] = kwargs['content']
    if 'embeds' in kwargs:
        kw['embeds'] = kwargs['embeds']
    if 'files' in kwargs:
        kw['attachments'] = kwargs['files']
    if 'allowed_mentions' in kwargs:
        kw['allowed_mentions'] = kwargs['allowed_mentions']
    if 'view' in kwargs:
        kw['view'] = kwargs['view']
    if components_v2:
        kw['content'] = None
        kw['embeds'] = ()
    return kw


async def _try_edit_interaction_message(
    interaction: Interaction | None,
    edit: PartialMessage | None,
    kwargs: _EditKWType,
) -> DiscordMessage | None:
    """Try to edit the source message through its interaction response.

    A response can only edit the intended target when its source message matches.
    Only Discord API failures fall back; programming errors propagate.
    """
    if interaction is None or interaction.message is None:
        return None
    if edit is not None and interaction.message.id != edit.id:
        return None
    try:
        if not interaction.response.is_done():
            await interaction.response.edit_message(**kwargs)
            return await interaction.original_response()
        if interaction.response.type in (
            InteractionResponseType.deferred_message_update,
            InteractionResponseType.message_update,
        ):
            return await interaction.edit_original_response(**kwargs)
    except DiscordException:
        return None
    return None


async def _edit_existing_message(editable: PartialMessage, kwargs: _EditKWType) -> DiscordMessage:
    """Use bot authentication for public messages and the token editor for ephemeral messages."""
    if isinstance(editable, DiscordMessage) and not editable.flags.ephemeral:
        editable = PartialMessage(channel=editable.channel, id=editable.id)  # type: ignore[arg-type]
    return await editable.edit(**kwargs)


async def _send_interaction_response(
    interaction: Interaction,
    message: ComponentV2Message | LegacyMessage,
    kwargs: MessageKwargs,
    *,
    ephemeral: bool,
) -> DiscordMessage:
    """Send an initial response or continue an already acknowledged interaction."""
    # Preserve the source-message binding so its components remain editable after sending a new screen.
    if interaction.message is not None and not interaction.response.is_done():
        with suppress(DiscordException):
            await interaction.response.defer()
    if interaction.response.is_done():
        return await _send_after_interaction_response(interaction, message, kwargs, ephemeral=ephemeral)
    await interaction.response.send_message(
        ephemeral=ephemeral,
        **into_send_kwargs(kwargs),  # type: ignore[reportArgumentType, arg-type]
    )
    return await interaction.original_response()


async def _send_after_interaction_response(
    interaction: Interaction,
    message: ComponentV2Message | LegacyMessage,
    kwargs: MessageKwargs,
    *,
    ephemeral: bool,
) -> DiscordMessage:
    """Send through an interaction that has already been acknowledged.

    Ordinary acknowledged interactions use a follow-up. A deferred channel response receiving Component V2 content
    must instead complete the deferred original response by editing it; this also retains the editable interaction
    message used by ephemeral responses.
    """
    if (
        isinstance(message, ComponentV2Message)
        and interaction.response.type is InteractionResponseType.deferred_channel_message
    ):
        return await interaction.edit_original_response(**into_edit_kwargs(kwargs, components_v2=True))
    return await interaction.followup.send(  # type: ignore[no-any-return]
        wait=True,
        ephemeral=ephemeral,
        **into_send_kwargs(kwargs),  # type: ignore[reportArgumentType, call-overload]
    )


async def _send_to_messageable(
    messageable: Messageable,
    kwargs: MessageKwargs,
    *,
    delete_after: float | None,
) -> DiscordMessage:
    """Send through a normal Messageable and delegate its deletion timer to discord.py."""
    assert not kwargs.get('ephemeral', False), 'Ephemeral messages require an interaction.'
    return await messageable.send(  # type: ignore[no-any-return]
        delete_after=delete_after,  # type: ignore[reportArgumentType, arg-type]
        **into_send_kwargs(kwargs),  # type: ignore[reportArgumentType, arg-type]
    )


async def _schedule_delete(message: DiscordMessage, delete_after: float | None) -> None:
    """Schedule deletion for an interaction response when requested.

    Messageable sends accept ``delete_after`` directly, while interaction response and follow-up paths require the
    timer to be attached to the returned message. Edit paths intentionally do not schedule deletion.
    """
    if delete_after is not None:
        target = message if message.flags.ephemeral else PartialMessage(channel=message.channel, id=message.id)  # type: ignore[arg-type]
        await target.delete(delay=delete_after)
