from __future__ import annotations

from asyncio import FIRST_COMPLETED, gather, wait
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, TypeVar, overload

from discord import Interaction, Message

from .result import _ResultTypeEnum

if TYPE_CHECKING:
    from asyncio import Future, Task
    from collections.abc import Callable, Iterable, Sequence
    from typing import Self, TypeVar

    from discord import AllowedMentions, Attachment, Client, Embed, File
    from discord.abc import Messageable
    from discord.ui import View

    from .model import Message as MessageData, MessageKwargs, ModelBase
    from .result import Result
    from .view import _View

    T = TypeVar('T')
    U = TypeVar('U')
    V = TypeVar('V')
    Fut_contra = TypeVar('Fut_contra', contravariant=True, bound=Future)  # type: ignore[reportMissingTypeArgument, type-arg]


def unwrap_or(value: T | None, default: U) -> T | U:
    """Return value if value is not None, otherwise return default."""
    if value is None:
        return default
    return value


def map_or(value: T | None, default: U, func: Callable[[T], V]) -> V | U:
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
    ) -> Self:
        """Message.edit, InteractionMessage.edit or WebhookMessage.edit."""
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
    messageable: Messageable | Interaction[Client],
    message: MessageData,
    view: _View | None,
    edit_target: _Editable | None,
) -> _Editable:
    """Helper function to send message. use messageable or interaction."""
    kwargs = message._to_dict()
    if view is not None:
        kwargs['view'] = view

    # if edit
    if message.edit_original:
        if isinstance(messageable, Interaction) and not messageable.response.is_done():
            if messageable.message is not None:  # Interaction.message is not None -> can edit
                await messageable.response.edit_message(**into_edit_kwargs(kwargs))
                return await messageable.original_response()  # type: ignore[reportReturnType, return-value]
        elif edit_target is not None:
            return await edit_target.edit(**into_edit_kwargs(kwargs))
        # fallback to send message

    # if send
    msg: Message
    delete_after = kwargs.get('delete_after', None)
    ephemeral = kwargs.get('ephemeral', False)
    kwargs = into_send_kwargs(kwargs)
    if isinstance(messageable, Interaction):
        if messageable.response.is_done():
            msg = await messageable.followup.send(wait=True, ephemeral=ephemeral, **kwargs)
        else:
            await messageable.response.send_message(ephemeral=ephemeral, **kwargs)
            msg = await messageable.original_response()

        if delete_after is not None:
            await msg.delete(delay=delete_after)
    else:
        # type-ignore: can pass None to delete_after
        msg = await messageable.send(delete_after=delete_after, **kwargs)  # type: ignore[reportArgumentType, arg-type]
    # type-ignore: return type is Message, InteractionMessage or WebhookMessage, which are also _Editable
    return msg  # type: ignore[reportReturnType, return-value]


@overload
async def wait_first_result(fs: Iterable[Task[T]]) -> tuple[set[Task[T]], set[Task[T]], set[Task[T]]]: ...
@overload
async def wait_first_result(fs: Iterable[Future[T]]) -> tuple[set[Future[T]], set[Future[T]], set[Future[T]]]: ...
async def wait_first_result(fs: Iterable[Fut_contra]) -> tuple[set[Fut_contra], set[Fut_contra], set[Fut_contra]]:
    """Waits for the first future/task in the iterable `fs` to complete.

    Returns as soon as at least one task completes successfully.
    If all tasks raise exceptions, returns the set of tasks that raised exceptions,
    and empty sets for done and pending tasks.

    Args:
        fs (Iterable[Fut_contra]): Iterable of futures or tasks.

    Returns:
        tuple[set[Fut_contra], set[Fut_contra], set[Fut_contra]]: A tuple containing:
            - A set of tasks that completed successfully. (done)
            - A set of tasks that raised exceptions. (exceptions)
            - A set of tasks that are still pending. (pending)
    """
    pending_tasks: set[Fut_contra] = set(fs)
    exceptions: set[Fut_contra] = set()
    done_tasks: set[Fut_contra] = set()

    while pending_tasks:
        done, pending = await wait(pending_tasks, return_when=FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except Exception:  # noqa: BLE001
                exceptions.add(task)  # type: ignore[reportUnknownArgumentType]
            else:
                done_tasks.add(task)  # type: ignore[reportUnknownArgumentType]
        if done_tasks:
            return done_tasks, exceptions, pending
        pending_tasks = pending

    return set(), exceptions, set()


async def exec_result(view: _View, result: Result) -> tuple[ModelBase, Interaction[Client] | Messageable] | None:
    """Exec result.

    Args:
        view (_View): View to set result.
        result (Result): Result to exec.

    Raises:
        ValueError: `result._interaction` is None.
        RuntimeError: If `result._type` is CONTINUE or FINISH, `messageable` is an Interaction,
            and its response has not been acknowledged (e.g., `defer()` or `send_message()`).

    Returns:
        tuple[ModelBase, Interaction[Client] | Messageable] | None: If the result indicates a model transition,
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
            await send_helper(messageable, msg, view, None)
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
