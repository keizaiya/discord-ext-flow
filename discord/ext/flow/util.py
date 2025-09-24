from __future__ import annotations

from asyncio import FIRST_COMPLETED, gather, wait
from typing import TYPE_CHECKING, NamedTuple, Protocol, TypedDict

from discord import DiscordException, Interaction, PartialMessage

from .result import _ResultTypeEnum

if TYPE_CHECKING:
    from asyncio import Task
    from collections.abc import Callable, Iterable, Sequence
    from typing import Any

    from discord import AllowedMentions, Attachment, Embed, File
    from discord.abc import Messageable
    from discord.ui import View

    from .external_task import ExternalResultTask
    from .model import Message as MessageData, MessageKwargs, ModelBase
    from .result import Result
    from .view import _View

    type Sendable = Interaction | Messageable


def unwrap_or[T, U](value: T | None, default: U) -> T | U:
    """Return value if value is not None, otherwise return default."""
    if value is None:
        return default
    return value


def map_or[T, U, V](value: T | None, default: U, func: Callable[[T], V]) -> V | U:
    """Return func(value) if value is not None, otherwise return default."""
    if value is None:
        return default
    return func(value)


class _Editable(Protocol):
    channel: Messageable

    async def edit(
        self,
        *,
        content: str | None = None,
        embeds: Sequence[Embed] | None = None,
        attachments: Sequence[Attachment | File] | None = None,
        view: View | None = None,
        allowed_mentions: AllowedMentions | None = None,
    ) -> _Editable:
        """PartialMessage.edit, Message.edit or WebhookMessage.edit."""
        ...


class _SendHelperKWType(TypedDict, total=False):
    content: str
    tts: bool
    embeds: Sequence[Embed]
    files: Sequence[File]
    allowed_mentions: AllowedMentions
    view: View
    suppress_embeds: bool
    silent: bool


def into_send_kwargs(kwargs: MessageKwargs) -> _SendHelperKWType:
    """Convert MessageKwargs to send kwargs type."""
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
    return kw


class _EditKWType(TypedDict, total=False):
    content: str
    embeds: Sequence[Embed]
    attachments: Sequence[Attachment | File]
    allowed_mentions: AllowedMentions
    view: View


def into_edit_kwargs(kwargs: MessageKwargs) -> _EditKWType:
    """Convert MessageKwargs to Message.edit kwargs type."""
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
    return kw


async def send_helper(
    messageable: Sendable,
    message: MessageData,
    view: _View | None,
    edit: _Editable | None,
) -> _Editable:
    """Helper function to send message. use messageable or interaction."""
    kwargs = message._to_dict()
    if view is not None:
        kwargs['view'] = view
    msg: PartialMessage

    # if edit
    if message.edit_original:
        if (
            isinstance(messageable, Interaction)
            and not messageable.response.is_done()
            and messageable.message is not None
        ):  # Interaction.message is not None -> can edit
            try:
                await messageable.response.edit_message(**into_edit_kwargs(kwargs))
            except DiscordException:
                pass  # ignore. and fallback.
            else:
                interaction_msg = await messageable.original_response()
                msg = PartialMessage(channel=interaction_msg.channel, id=interaction_msg.id)
                return msg  # type: ignore[reportReturnType, return-value]
        if edit is not None:
            return await edit.edit(**into_edit_kwargs(kwargs))
        # fallback to send message

    # if send
    delete_after = kwargs.get('delete_after', None)
    ephemeral = kwargs.get('ephemeral', False)
    kwargs = into_send_kwargs(kwargs)
    if isinstance(messageable, Interaction):
        if messageable.response.is_done():
            msg = await messageable.followup.send(wait=True, ephemeral=ephemeral, **kwargs)
        else:
            await messageable.response.send_message(ephemeral=ephemeral, **kwargs)
            interaction_msg = await messageable.original_response()
            msg = PartialMessage(channel=interaction_msg.channel, id=interaction_msg.id)

        if delete_after is not None:
            await msg.delete(delay=delete_after)
    else:
        # type-ignore: can pass None to delete_after
        msg = await messageable.send(delete_after=delete_after, **kwargs)  # type: ignore[reportArgumentType, arg-type]
    # type-ignore: return type is Message, InteractionMessage or WebhookMessage, which are also _Editable
    return msg  # type: ignore[reportReturnType, return-value]


class WaitResult(NamedTuple):
    """Result of wait_first_completed_external_result_task.

    - done: A set of tasks that completed successfully.
    - base_exceptions: A set of tasks that raised exceptions(BaseException).
    - exceptions: A set of tasks that raised exceptions(Exception).
    - pending: A set of tasks that are still pending.
    """

    done: set[ExternalResultTask]
    base_exceptions: set[ExternalResultTask]
    exceptions: set[ExternalResultTask]
    pending: set[ExternalResultTask]


async def wait_first_completed_external_result_task(fs: Iterable[ExternalResultTask]) -> WaitResult:
    """Waits for the first future/task in the iterable `fs` to complete.

    Call await wait(fs, return_when=FIRST_COMPLETED) to wait for the first task to complete.
    Different from asyncio.wait, this function return split sets of tasks based on their completion status.

    Args:
        fs (Iterable[ExternalResultTask]): Iterable of futures or tasks.

    Returns:
        WaitResult: result as a named tuple.
    """
    if all(not t.done() for t in fs):
        aws = {t.task for t in fs}
        await wait(aws, return_when=FIRST_COMPLETED)

    base_exceptions: set[ExternalResultTask] = set()
    exceptions: set[ExternalResultTask] = set()
    done_tasks: set[ExternalResultTask] = set()
    pending_tasks: set[ExternalResultTask] = set()
    for task in fs:
        if not task.done():
            pending_tasks.add(task)
            continue
        try:
            task.result()
        except Exception:  # noqa: BLE001
            exceptions.add(task)
        except BaseException:  # noqa: BLE001
            base_exceptions.add(task)
        else:
            done_tasks.add(task)
    return WaitResult(done_tasks, base_exceptions, exceptions, pending_tasks)


async def exec_result(view: _View, result: Result, edit: _Editable) -> tuple[ModelBase, Sendable] | None:
    """Exec result.

    Args:
        view (_View): View to set result.
        result (Result): Result to exec.
        edit (_Editable): Target to edit.

    Raises:
        ValueError: `result._interaction` is None.
        RuntimeError: If `result._type` is CONTINUE or FINISH, `messageable` is an Interaction,
            and its response has not been acknowledged (e.g., `defer()` or `send_message()`).

    Returns:
        tuple[ModelBase, Interaction | Messageable] | None: If the result indicates a model transition,
            returns the new model and the messageable context. Otherwise, returns None.
    """
    if result._interaction is not None:
        messageable = result._interaction
    else:
        raise ValueError('result._interaction is None.')

    match result._type:
        case _ResultTypeEnum.MESSAGE:
            assert result._message is not None
            msg = result._message
            view.clear_items()
            view.set_items(msg.items or ())
            view._reset_fut()
            await send_helper(messageable, msg, view, edit)
            if not msg.items:
                view.stop()
            return None

        case _ResultTypeEnum.MODEL:
            assert result._model is not None
            view.stop()
            return (result._model, messageable)

        case _ResultTypeEnum.CONTINUE | _ResultTypeEnum.FINISH:
            if isinstance(messageable, Interaction) and not messageable.response.is_done():
                raise RuntimeError('Callback MUST consume interaction.')
            if result._is_end:
                view.stop()
            return None


async def force_cancel_tasks(tasks: Iterable[Task[Any]]) -> None:
    """Force cancel all tasks.

    Args:
        tasks (Iterable[Task[T]]): Tasks to cancel.
    """
    for task in tasks:
        task.cancel()
    await gather(*tasks, return_exceptions=True)
