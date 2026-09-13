"""Internal ownership of a flow's delivery context and displayed message."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager, suppress
from typing import TYPE_CHECKING, TypedDict

from discord import (
    DiscordException,
    File,
    Interaction,
    InteractionResponseType,
    InteractionType,
    Message as DiscordMessage,
    MessageFlags,
    PartialMessage,
    ui,
)
from discord.webhook.async_ import async_context

from .model import ComponentV2Message, LegacyMessage

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

    from discord import AllowedMentions, Attachment, Embed, Poll
    from discord.abc import Messageable
    from discord.ui import LayoutView, View

    from .model import MessageKwargs
    from .view import _ViewType

__all__ = ()


class FlowDisplay:
    """Own delivery and message replacement without managing models or tasks.

    ``message`` and ``view`` expose the current display for inspection. Only this
    class updates them; the controller owns waiting on and retiring view tasks.
    The actual Discord message is retained with its channel and visibility flags. :meth:`_send` selects the
    interaction and edit target and orders delivery attempts; stateless functions in this module adapt each
    Discord endpoint. This class is internal, does not depend on Controller, and is not part of the public API.
    """

    def __init__(self) -> None:
        self._channel: Messageable | None = None
        self._interaction: Interaction | None = None
        self._pending_interaction: Interaction | None = None
        self._completed_deferred_responses: set[Interaction] = set()
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

    async def _deferred_original_is_loading(self, interaction: Interaction) -> bool:
        """Read the current deferred original state without using discord.py's cached response."""
        # discord.py's public original_response() caches its first result, while InteractionMessage.fetch() uses the
        # bot-authenticated channel endpoint and cannot retrieve ephemeral responses. Use the same adapter route as
        # original_response() to make an uncached GET /messages/@original request, without changing that cache.
        adapter = async_context.get()
        http = interaction._state.http
        data = await adapter.get_original_interaction_response(
            application_id=interaction.application_id,
            token=interaction.token,
            session=interaction._session,
            proxy=http.proxy,
            proxy_auth=http.proxy_auth,
        )
        return bool(data.get('flags', 0) & MessageFlags.loading.flag)

    async def replace(self, message: ComponentV2Message | LegacyMessage, view: _ViewType | None) -> None:
        """Send the replacement and disable the previous message when its ID changes.

        A send failure retains the previous message and view references, though the controller has already
        stopped the previous view for replacement. The controller then ends the flow and reclaims that view's
        tasks. Once sending succeeds, the replacement becomes current even if disabling the old message fails.
        The controller can therefore reclaim view tasks using the resulting state.

        Original upload File objects stay open throughout delivery attempts and are closed when delivery
        succeeds or fails. See :func:`_upload_attempt` for how each route borrows their upload streams.
        """
        with ExitStack() as uploads:
            for file in message.files or ():
                uploads.callback(file.close)
            sent = await self._send(message, view)
        try:
            if self.message is not None and self.message.id != sent.id:
                await self._disable_items()
        finally:
            self.message, self.view = sent, view
            self._disable_on_finish = message.disable_items
            self.complete_response()

    async def _send(self, message: ComponentV2Message | LegacyMessage, view: _ViewType | None) -> DiscordMessage:  # noqa: C901, PLR0912 - Keep context selection and ordered delivery attempts together.
        """Select this operation's delivery context and try its edit/send routes.

        Return the actual Discord message without changing the active display;
        replacement owns activation and cleanup of the previous screen.

        With ``edit_original=True``, the supplied edit target takes precedence over the interaction's source
        message. If both identify the same message, try the interaction edit, then the stored message editor,
        then a new send. Without an explicit target, use the interaction's source message when present;
        otherwise send a new message. Only Discord API failures advance to another route; programming errors
        propagate. Directly editing a different explicit target does not acknowledge the interaction.

        A result's interaction takes precedence over the retained interaction. External non-ephemeral results
        use the invocation's normal Messageable when one is available. Failed non-ephemeral interaction sends
        may also use that destination. External ephemeral results require a retained interaction. Editing an
        ephemeral target keeps new-message fallback ephemeral even if the request omitted ``ephemeral=True``;
        an expired token never changes ephemeral delivery to a normal channel send.

        Each route receives fresh File wrappers over the original streams. Replacement keeps the originals open
        until all delivery attempts finish, so endpoint cleanup cannot close a later attempt's upload content.
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
            with _upload_attempt(kwargs) as attempt:
                edit_kwargs = into_edit_kwargs(attempt, components_v2=isinstance(message, ComponentV2Message))
                edit_kwargs['view'] = view
                edited = await _try_edit_interaction_message(interaction, edit, edit_kwargs)
            if edited is not None:
                return edited
            if edit is not None:
                with suppress(DiscordException), _upload_attempt(kwargs) as attempt:
                    edit_kwargs = into_edit_kwargs(attempt, components_v2=isinstance(message, ComponentV2Message))
                    edit_kwargs['view'] = view
                    return await _edit_existing_message(edit, edit_kwargs)

        delete_after = kwargs.get('delete_after')
        if interaction is not None:
            try:
                deferred_response = interaction.response.type is InteractionResponseType.deferred_channel_message
                completes_deferred_response = False
                if (
                    deferred_response
                    and isinstance(message, ComponentV2Message)
                    and interaction not in self._completed_deferred_responses
                ):
                    completes_deferred_response = await self._deferred_original_is_loading(interaction)
                with _upload_attempt(kwargs) as attempt:
                    sent = await _send_interaction_response(
                        interaction,
                        message,
                        attempt,
                        ephemeral=ephemeral,
                        complete_deferred=completes_deferred_response,
                    )
            except DiscordException:
                if ephemeral or self._channel is None:
                    raise
            else:
                if deferred_response:
                    self._completed_deferred_responses.add(interaction)
                await _schedule_delete(sent, delete_after)
                return sent
        assert self._channel is not None, 'A messageable or interaction is required to send a flow message.'
        with _upload_attempt(kwargs) as attempt:
            return await _send_to_messageable(self._channel, attempt, delete_after=delete_after)

    async def _disable_items(self) -> None:
        """Disable controls when the displayed message opted in with ``disable_items=True``.

        Edit only the view, preserving content, embeds, and attachments. Public messages use bot authentication;
        ephemeral messages use their token editor, with a matching component interaction providing another edit
        route. Token expiry can prevent ephemeral cleanup; failures propagate after local view/task reclamation
        by the controller.
        """
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
            self._completed_deferred_responses.clear()
            self.complete_response()


@contextmanager
def _upload_attempt(kwargs: MessageKwargs) -> Generator[MessageKwargs, None, None]:
    """Rewind uploads and lend each endpoint fresh, non-owning File wrappers.

    discord.py closes its File arguments even when an API request fails. The original File objects retain stream
    ownership for the entire delivery operation; each attempt preserves their filename, spoiler and description.
    """
    attempt = kwargs.copy()
    with ExitStack() as stack:
        if 'files' in kwargs:
            files: list[File] = []
            for original in kwargs['files']:
                original.reset()
                file = File(
                    original.fp,
                    filename=original.filename,
                    spoiler=original.spoiler,
                    description=original.description,
                )
                stack.callback(file.close)
                files.append(file)
            attempt['files'] = files
        yield attempt


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
    """Use bot authentication for public messages and the token editor for ephemeral messages.

    Construct a PartialMessage from the actual channel and message ID for public messages, including those
    originally sent or edited through interactions. Edits and disabling controls then remain possible after
    interaction token expiry while the bot retains access. Ephemeral messages keep their interaction/webhook
    editor and require a valid token; a matching component interaction can provide a fresh edit route through
    :func:`_try_edit_interaction_message`.
    """
    if isinstance(editable, DiscordMessage) and not editable.flags.ephemeral:
        editable = PartialMessage(channel=editable.channel, id=editable.id)  # type: ignore[arg-type]
    return await editable.edit(**kwargs)


async def _send_interaction_response(
    interaction: Interaction,
    message: ComponentV2Message | LegacyMessage,
    kwargs: MessageKwargs,
    *,
    ephemeral: bool,
    complete_deferred: bool,
) -> DiscordMessage:
    """Send an initial response or continue an already acknowledged interaction.

    An unacknowledged interaction with a source message first defers the source update and then sends a follow-up.
    This preserves the source-message binding for previous-screen cleanup, including ephemeral screens. A Discord
    API failure while deferring still allows the remaining send routes to be attempted.
    """
    # Preserve the source-message binding so its components remain editable after sending a new screen.
    if interaction.message is not None and not interaction.response.is_done():
        with suppress(DiscordException):
            await interaction.response.defer()
    if interaction.response.is_done():
        return await _send_after_interaction_response(
            interaction,
            message,
            kwargs,
            ephemeral=ephemeral,
            complete_deferred=complete_deferred,
        )
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
    complete_deferred: bool,
) -> DiscordMessage:
    """Send through an interaction that has already been acknowledged.

    Ordinary acknowledged interactions use a follow-up. A deferred channel response is completed only while its
    original message is still loading. Component V2 content completes that response through the edit endpoint;
    Discord treats the first Legacy follow-up as the same completion. Once complete, later sends use follow-ups
    so each message's ``ephemeral`` setting is respected.
    """
    if (
        complete_deferred
        and isinstance(message, ComponentV2Message)
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
