from __future__ import annotations

import asyncio
from dataclasses import fields
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, assert_type
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import ChannelType, Client, Interaction, TextStyle, ui
from discord.app_commands.namespace import ResolveKey
from discord.ext import flow
from discord.ext.flow import (
    ActionRow,
    Button,
    ChannelSelect,
    Container,
    FlowTimeoutError,
    MentionableSelect,
    ModelBase,
    Result,
    RoleSelect,
    TextDisplay,
    UserSelect,
    item,
    modal,
)
from discord.ext.flow.controller import Controller, _get_controller
from discord.ext.flow.external_task import ExternalTaskLifeTime
from discord.ext.flow.item import Select as ItemSelect, TextDisplay as ItemTextDisplay
from discord.ext.flow.modal import _InnerModal

if TYPE_CHECKING:
    from discord import Attachment, Member, Role, User
    from discord.app_commands import AppCommandChannel, AppCommandThread


def _finish(_: Interaction[Client]) -> Result:
    return Result.finish_flow()


def _modal_config(*, title: str = 'Survey') -> modal.ModalConfig:
    return modal.ModalConfig(title, custom_id='survey')


def _inner_modal(
    *items: flow.ModalItemType,
    callback: modal.ModalCallback = _finish,
    title: str = 'Survey',
    controller: Controller | None = None,
) -> _InnerModal:
    return _InnerModal(_modal_config(title=title), items, callback, controller=controller)


def _component_payload(component_type: int, custom_id: str, **values: object) -> list[dict[str, object]]:
    return [{'type': 18, 'component': {'type': component_type, 'custom_id': custom_id, **values}}]


def _interaction() -> Interaction[Client]:
    return MagicMock(spec=Interaction)


def _send_modal_interaction(send_modal: AsyncMock) -> Interaction[Client]:
    """Represent the private Interaction.response descriptor only at the mock boundary."""
    return MagicMock(spec=Interaction, **{'response.send_modal': send_modal})


def _inner_modal_with_invalid_item(item: object) -> _InnerModal:
    """Build an invalid item tuple only to verify the runtime top-level rejection boundary."""
    return _InnerModal(
        _modal_config(),
        (item,),  # type: ignore[arg-type, reportArgumentType]  # Deliberately invalid runtime boundary.
        _finish,
    )


def _invalid_label(component: object) -> flow.Label:
    """Build an invalid Label only to verify the runtime child rejection boundary."""
    return flow.Label(text='Invalid', component=component)  # type: ignore[arg-type, reportArgumentType]


def test_root_exports_item_and_modal_names() -> None:
    """The root namespace exposes item definitions and modal operations."""
    item_names = (
        'ChannelSelect',
        'Checkbox',
        'CheckboxGroup',
        'CheckboxGroupOption',
        'FileUpload',
        'Label',
        'MentionableSelect',
        'ModalInputItem',
        'ModalInputType',
        'ModalItem',
        'ModalItemType',
        'ModalValue',
        'RadioGroup',
        'RadioGroupOption',
        'RoleSelect',
        'Select',
        'SelectOption',
        'TextDisplay',
        'TextInput',
        'UserSelect',
    )
    modal_names = ('ModalCallback', 'ModalConfig', 'send_modal')
    public_names = item_names + modal_names
    root_namespace = vars(flow)
    for name in item_names:
        assert root_namespace[name] is vars(item)[name]
    for name in modal_names:
        assert root_namespace[name] is vars(modal)[name]

    assert flow.TextDisplay is ItemTextDisplay is TextDisplay
    assert flow.Select is ItemSelect
    assert flow.UserSelect is UserSelect
    assert flow.RoleSelect is RoleSelect
    assert flow.MentionableSelect is MentionableSelect
    assert flow.ChannelSelect is ChannelSelect
    assert flow.ModalConfig is modal.ModalConfig
    assert flow.send_modal is modal.send_modal
    root_all = tuple(name for name in root_namespace.get('__all__', ()) if isinstance(name, str))
    assert set(public_names) <= set(root_all)
    for removed_name in (
        'Modal',
        'ModalLabel',
        'ModalSelect',
        'ModalTextInput',
    ):
        assert not hasattr(flow, removed_name)


def test_modal_config_keeps_form_metadata_separate_from_items() -> None:
    """Items belong to an individual send/inner modal, never reusable form metadata."""
    config = modal.ModalConfig('Survey', 30.0, 'survey')

    assert (config.title, config.timeout, config.custom_id) == ('Survey', 30.0, 'survey')
    assert tuple(field.name for field in fields(config)) == ('title', 'timeout', 'custom_id')
    assert not hasattr(config, 'items')


@pytest.mark.asyncio
async def test_modal_serializes_formal_v2_top_levels_and_preserves_identifiers() -> None:
    """TextDisplay and Label use the v2.7.1 modal payload, not a message LayoutView payload."""
    items = (
        flow.TextDisplay('Read this first.', id=10),
        flow.Label(
            text='Name',
            description='Shown publicly',
            id=11,
            component=flow.TextInput(
                custom_id='name',
                style=TextStyle.paragraph,
                placeholder='Your name',
                default='Ada',
                required=False,
                min_length=1,
                max_length=100,
                id=12,
            ).field(),
        ),
        flow.Label(
            text='Topic',
            id=13,
            component=flow.Select(
                custom_id='topic',
                options=(flow.SelectOption(label='Bug', value='bug'),),
                min_values=0,
                max_values=1,
                disabled=True,
                id=14,
            ).field(),
        ),
    )
    inner_modal = _InnerModal(_modal_config(), items, _finish)

    assert inner_modal.to_dict() == {
        'custom_id': 'survey',
        'title': 'Survey',
        'components': [
            {'type': 10, 'content': 'Read this first.', 'id': 10},
            {
                'type': 18,
                'label': 'Name',
                'description': 'Shown publicly',
                'id': 11,
                'component': {
                    'type': 4,
                    'style': 2,
                    'label': None,
                    'custom_id': 'name',
                    'placeholder': 'Your name',
                    'value': 'Ada',
                    'required': False,
                    'min_length': 1,
                    'max_length': 100,
                    'id': 12,
                },
            },
            {
                'type': 18,
                'label': 'Topic',
                'id': 13,
                'component': {
                    'type': 3,
                    'custom_id': 'topic',
                    'min_values': 0,
                    'max_values': 1,
                    'disabled': True,
                    'required': True,
                    'id': 14,
                    'options': [{'label': 'Bug', 'value': 'bug', 'default': False}],
                },
            },
        ],
    }


@pytest.mark.parametrize(
    ('config', 'ui_type', 'expected'),
    [
        (flow.TextInput(custom_id='text'), ui.TextInput, {'required': True}),
        (
            flow.Select(custom_id='select', options=(flow.SelectOption(label='One'),)),
            ui.Select,
            {'required': True, 'min_values': 1, 'max_values': 1},
        ),
        (flow.UserSelect(custom_id='user'), ui.UserSelect, {'required': False}),
        (flow.RoleSelect(custom_id='role'), ui.RoleSelect, {'required': False}),
        (flow.MentionableSelect(custom_id='mention'), ui.MentionableSelect, {'required': False}),
        (
            flow.ChannelSelect(custom_id='channel', channel_types=(ChannelType.text,)),
            ui.ChannelSelect,
            {'required': False, 'channel_types': [ChannelType.text]},
        ),
        (
            flow.FileUpload(custom_id='file', min_values=0, max_values=3),
            ui.FileUpload,
            {'required': True, 'min_values': 0, 'max_values': 3},
        ),
        (
            flow.RadioGroup(custom_id='radio', options=(flow.RadioGroupOption(label='One'),)),
            ui.RadioGroup,
            {'required': True},
        ),
        (
            flow.CheckboxGroup(
                custom_id='checks',
                min_values=0,
                max_values=3,
                options=(flow.CheckboxGroupOption(label='One'),),
            ),
            ui.CheckboxGroup,
            {'required': True, 'min_values': 0, 'max_values': 3},
        ),
        (flow.Checkbox(custom_id='checkbox', default=True), ui.Checkbox, {'default': True}),
    ],
)
@pytest.mark.asyncio
async def test_every_formal_label_child_is_constructed_with_v271_defaults(
    config: flow.ModalInputType,
    ui_type: type[ui.Item[Any]],
    expected: dict[str, object],
) -> None:
    """All ten Label child types and their v2.7.1 required defaults are preserved."""
    field = config.field()
    inner_modal = _inner_modal(flow.Label(text='Field', component=field))
    label = inner_modal.children[0]

    assert isinstance(label, ui.Label)
    assert isinstance(label.component, ui_type)
    for attribute, value in expected.items():
        assert getattr(label.component, attribute) == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('config', 'component_type', 'payload', 'expected'),
    [
        (flow.TextInput(custom_id='text'), 4, {'value': 'hello'}, 'hello'),
        (
            flow.Select(custom_id='select', options=(flow.SelectOption(label='One'),)),
            3,
            {'values': ['option']},
            ['option'],
        ),
        (flow.UserSelect(custom_id='user'), 5, {'values': ['42']}, ['42']),
        (flow.RoleSelect(custom_id='role'), 6, {'values': ['43']}, ['43']),
        (flow.MentionableSelect(custom_id='mention'), 7, {'values': ['44']}, ['44']),
        (flow.ChannelSelect(custom_id='channel'), 8, {'values': ['45']}, ['45']),
        (flow.FileUpload(custom_id='file'), 19, {'values': ['46']}, []),
        (
            flow.RadioGroup(custom_id='radio', options=(flow.RadioGroupOption(label='One'),)),
            21,
            {'value': 'one'},
            'one',
        ),
        (
            flow.CheckboxGroup(custom_id='checks', options=(flow.CheckboxGroupOption(label='One'),)),
            22,
            {'values': ['one', 'two']},
            ['one', 'two'],
        ),
        (flow.Checkbox(custom_id='checked'), 23, {'value': True}, True),
    ],
)
async def test_label_submit_payloads_restore_every_formal_value(
    config: flow.ModalInputType,
    component_type: int,
    payload: dict[str, object],
    expected: object,
) -> None:
    """Submit handling walks Label children and preserves raw-ID fallback semantics."""
    received: list[object] = []
    field = config.field()

    def callback(_: Interaction[Client]) -> Result:
        received.append(field.value)
        return Result.finish_flow()

    assert config.custom_id is not None
    inner_modal = _inner_modal(flow.Label(text='Field', component=field), callback=callback)
    interaction = _interaction()
    await inner_modal._scheduled_task(
        interaction,
        _component_payload(component_type, config.custom_id, **payload),  # type: ignore[arg-type, reportArgumentType]  # discord.py keeps submit payload TypedDicts private.
        {},
    )

    assert received == [expected]
    result = await inner_modal._wait()
    assert result._is_end
    assert result._interaction is interaction
    assert inner_modal.is_finished()


@pytest.mark.asyncio
async def test_entity_and_file_values_use_resolved_objects_but_keep_missing_file_empty() -> None:
    """Entity selects fall back to IDs, while FileUpload only exposes resolved attachments."""
    entity = object()
    attachment: Attachment = MagicMock()
    received: list[tuple[object, ...]] = []
    user = flow.UserSelect(custom_id='user').field()
    files = flow.FileUpload(custom_id='file').field()

    def callback(_: Interaction[Client]) -> Result:
        received.append((user.value, files.value))
        return Result.finish_flow()

    inner_modal = _inner_modal(
        flow.Label(text='User', component=user),
        flow.Label(text='File', component=files),
        callback=callback,
    )
    await inner_modal._scheduled_task(
        _interaction(),
        [
            {'type': 18, 'component': {'type': 5, 'custom_id': 'user', 'values': ['42']}},
            {'type': 18, 'component': {'type': 19, 'custom_id': 'file', 'values': ['43']}},
        ],
        {
            ResolveKey('42', 6): entity,
            ResolveKey('43', 11): attachment,
        },
    )

    assert received == [([entity], [attachment])]


@pytest.mark.asyncio
async def test_text_display_does_not_produce_a_value_and_label_rows_only_order_rendering() -> None:
    """Display items are ignored by callbacks; Label rows, not child rows, order top levels."""
    received: list[tuple[object, ...]] = []
    last = flow.TextInput(custom_id='last', row=0).field()
    first = flow.Checkbox(custom_id='first').field()
    implicit = flow.Select(custom_id='implicit', options=()).field()

    def callback(_: Interaction[Client]) -> Result:
        received.append((last.value, first.value, implicit.value))
        return Result.finish_flow()

    inner_modal = _inner_modal(
        flow.Label(text='Last', row=4, component=last),
        flow.TextDisplay('Read this.'),
        flow.Label(text='First', row=1, component=first),
        flow.Label(text='Implicit', component=implicit),
        callback=callback,
    )

    rendered = inner_modal.to_dict()['components']
    assert [component.get('label', component.get('content')) for component in rendered] == [
        'Read this.',
        'Implicit',
        'First',
        'Last',
    ]
    payload: list[dict[str, object]] = [
        {'type': 10, 'content': 'Read this.'},
        {'type': 18, 'component': {'type': 4, 'custom_id': 'last', 'value': 'L'}},
        {'type': 18, 'component': {'type': 23, 'custom_id': 'first', 'value': True}},
        {'type': 18, 'component': {'type': 3, 'custom_id': 'implicit', 'values': ['I']}},
    ]
    await inner_modal._scheduled_task(
        _interaction(),
        payload,  # type: ignore[arg-type, reportArgumentType]  # discord.py keeps submit payload TypedDicts private.
        {},
    )

    assert received == [('L', True, ['I'])]


@pytest.mark.asyncio
async def test_modal_enforces_five_top_level_items() -> None:
    """discord.py's five top-level component limit applies equally to display-only modals."""
    with pytest.raises(ValueError, match='maximum number of children exceeded'):
        _inner_modal(*(flow.TextDisplay(str(index)) for index in range(6)))


@pytest.mark.parametrize(
    'item',
    [
        Button().on(callback=lambda _: Result.finish_flow()),
        ActionRow(items=()),
        Container(items=()),
        flow.TextInput(),
        flow.Select(options=()),
        flow.Select(options=()).field(),
    ],
)
@pytest.mark.asyncio
async def test_modal_rejects_non_formal_top_level_items(item: object) -> None:
    """Message layouts and naked selects must not leak into the formal modal item union."""
    with pytest.raises(TypeError, match=r'valid modal top-level'):
        _inner_modal_with_invalid_item(item)


@pytest.mark.asyncio
async def test_modal_rejects_non_formal_label_child() -> None:
    """Label may contain exactly one of the ten documented form input components."""
    with pytest.raises(TypeError, match='Button is not a valid modal input'):
        _inner_modal(_invalid_label(Button().on(callback=lambda _: Result.finish_flow())))
    with pytest.raises(AttributeError, match="'TextInput' object has no attribute 'item'"):
        _inner_modal(_invalid_label(flow.TextInput()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'binding',
    [
        flow.Select().on(callback=lambda _interaction, _values: Result.finish_flow()),
        flow.UserSelect().on(callback=lambda _interaction, _values: Result.finish_flow()),
        flow.RoleSelect().on(callback=lambda _interaction, _values: Result.finish_flow()),
        flow.MentionableSelect().on(callback=lambda _interaction, _values: Result.finish_flow()),
        flow.ChannelSelect().on(callback=lambda _interaction, _values: Result.finish_flow()),
    ],
)
async def test_modal_rejects_callback_bound_selects(binding: object) -> None:
    """Modal Labels deliberately accept field bindings, never message callback bindings."""
    with pytest.raises(AttributeError, match="'InteractiveItem' object has no attribute '_bind'"):
        _inner_modal(_invalid_label(binding))


@pytest.mark.asyncio
async def test_modal_rejects_reused_item() -> None:
    """A field cannot bind to more than one concrete discord.py item."""
    reused = flow.TextInput().field()
    with pytest.raises(ValueError, match='item instances cannot be reused'):
        _inner_modal(
            flow.Label(text='First', component=reused),
            flow.Label(text='Second', component=reused),
        )


@pytest.mark.asyncio
async def test_modal_item_value_requires_submit_and_item_cannot_be_rebound() -> None:
    """A field exposes only submitted data and belongs to exactly one concrete modal."""
    item = flow.TextInput(custom_id='text').field()

    with pytest.raises(RuntimeError, match='has not been submitted'):
        _ = item.value

    _inner_modal(flow.Label(text='Text', component=item))
    with pytest.raises(RuntimeError, match='has not been submitted'):
        _ = item.value
    with pytest.raises(ValueError, match='item instances cannot be reused'):
        _inner_modal(flow.Label(text='Text again', component=item))


@pytest.mark.asyncio
async def test_modal_accepts_unique_explicit_ids_and_generates_distinct_input_custom_ids() -> None:
    """Separated items retain both explicit numeric identities and d.py-generated route IDs."""
    first = flow.TextInput(id=2).field()
    second = flow.Checkbox(id=4).field()
    inner_modal = _inner_modal(
        flow.TextDisplay('Read this.', id=1),
        flow.Label(text='First', id=3, component=first),
        flow.Label(text='Second', id=5, component=second),
    )

    payload = inner_modal.to_dict()['components']
    assert [item['id'] for item in payload] == [1, 3, 5]
    assert first.item.custom_id is None
    assert second.item.custom_id is None
    assert payload[1]['component']['custom_id'] != payload[2]['component']['custom_id']


@pytest.mark.asyncio
async def test_legacy_top_level_text_input_remains_an_action_row_and_returns_a_value() -> None:
    """The only supported legacy layout is one top-level TextInput per implicit ActionRow."""
    received: list[object] = []
    legacy = flow.TextInput(label='Legacy', custom_id='legacy').field()

    def callback(_: Interaction[Client]) -> Result:
        received.append(legacy.value)
        return Result.finish_flow()

    inner_modal = _inner_modal(legacy, callback=callback)

    assert inner_modal.to_dict()['components'] == [
        {
            'type': 1,
            'components': [
                {
                    'type': 4,
                    'style': 1,
                    'label': 'Legacy',
                    'custom_id': 'legacy',
                    'required': True,
                }
            ],
        }
    ]
    await inner_modal._scheduled_task(
        _interaction(),
        [{'type': 1, 'components': [{'type': 4, 'custom_id': 'legacy', 'value': 'old'}]}],
        {},
    )
    assert received == ['old']


@pytest.mark.asyncio
async def test_callback_failure_timeout_and_repeated_submit_settle_the_waiter() -> None:
    """A modal callback cannot leave its external result pending or run twice."""
    error = RuntimeError('callback failed')
    failed_item = flow.TextInput(custom_id='text').field()
    failed = _inner_modal(
        flow.Label(text='Text', component=failed_item),
        callback=lambda _interaction: (_ for _ in ()).throw(error),
    )
    await failed._scheduled_task(_interaction(), [], {})
    with pytest.raises(RuntimeError, match='callback failed'):
        await failed._wait()
    assert failed.is_finished()
    assert failed_item.value == ''

    timed_out = _inner_modal(flow.TextDisplay('No input'))
    await timed_out.on_timeout()
    with pytest.raises(FlowTimeoutError, match='modal timed out'):
        await timed_out._wait()
    assert timed_out.is_finished()

    calls = 0

    def callback(_: Interaction[Client]) -> Result:
        nonlocal calls
        calls += 1
        return Result.finish_flow()

    repeated = _inner_modal(
        flow.Label(text='Text', component=flow.TextInput(custom_id='text').field()),
        callback=callback,
    )
    payload = _component_payload(4, 'text', value='once')
    await repeated._scheduled_task(
        _interaction(),
        payload,  # type: ignore[arg-type, reportArgumentType]  # discord.py keeps submit payload TypedDicts private.
        {},
    )
    await repeated._scheduled_task(
        _interaction(),
        payload,  # type: ignore[arg-type, reportArgumentType]  # discord.py keeps submit payload TypedDicts private.
        {},
    )
    assert calls == 1


@pytest.mark.asyncio
async def test_modal_submit_callback_failure_does_not_use_discord_default_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Flow-owned modal failures reach the waiter without duplicate discord.py logging."""
    error = RuntimeError('callback failed')
    inner_modal = _inner_modal(
        flow.TextDisplay('Submit'),
        callback=lambda _interaction: (_ for _ in ()).throw(error),
    )

    with caplog.at_level('ERROR'):
        await inner_modal._scheduled_task(_interaction(), [], {})
        with pytest.raises(RuntimeError, match='callback failed'):
            await inner_modal._wait()

    assert 'Ignoring exception in modal' not in caplog.text


@pytest.mark.asyncio
async def test_modal_timeout_does_not_cancel_a_submit_callback_in_progress() -> None:
    """A timeout racing an accepted submit preserves the submit callback result."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def callback(_: Interaction[Client]) -> Result:
        started.set()
        await release.wait()
        return Result.finish_flow()

    inner_modal = _inner_modal(flow.TextDisplay('Submit'), callback=callback)
    interaction = _interaction()
    submit = asyncio.create_task(inner_modal.on_submit(interaction))
    await started.wait()

    await inner_modal.on_timeout()
    assert not inner_modal.fut.done()

    release.set()
    await submit
    result = await inner_modal._wait()
    assert result._is_end
    assert result._interaction is interaction


@pytest.mark.asyncio
async def test_modal_submit_uses_controller_context_and_completes_result_interaction() -> None:
    """Modal submit callbacks receive the active controller and implicit result interaction."""
    controller = Controller(ModelBase())
    received: list[Controller] = []

    def callback(interaction: Interaction[Client]) -> Result:
        received.append(_get_controller())
        assert interaction is not None
        return Result.finish_flow()

    response = SimpleNamespace(send_modal=AsyncMock())
    send_interaction = _send_modal_interaction(response.send_modal)
    task = await modal.send_modal(
        callback,
        send_interaction,
        modal.ModalConfig(title='Context'),
        (flow.TextDisplay('Submit'),),
        controller=controller,
    )
    sent = response.send_modal.await_args.args[0]
    submit_interaction = _interaction()

    await sent.on_submit(submit_interaction)
    result = await task.task

    assert received == [controller]
    assert result._interaction is submit_interaction


@pytest.mark.asyncio
async def test_cancelling_wait_stops_an_unsubmitted_modal() -> None:
    """Cancellation of the external waiter releases discord.py's modal view resources."""
    inner_modal = _inner_modal(flow.TextDisplay('No input'))
    wait = asyncio.create_task(inner_modal._wait())
    await asyncio.sleep(0)

    wait.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wait

    assert inner_modal.is_finished()
    assert inner_modal.fut.cancelled()


@pytest.mark.asyncio
async def test_send_modal_sends_once_and_registers_a_model_lifetime_task() -> None:
    """modal.send_modal acknowledges the interaction before registering the waiter with the controller."""
    response = SimpleNamespace(send_modal=AsyncMock())
    interaction = _send_modal_interaction(response.send_modal)
    controller = Controller(ModelBase())

    task = await modal.send_modal(
        _finish,
        interaction,
        modal.ModalConfig(title='Send'),
        (flow.TextDisplay('Notice'),),
        controller=controller,
    )

    response.send_modal.assert_awaited_once()
    sent = response.send_modal.await_args.args[0]
    assert isinstance(sent, _InnerModal)
    assert sent.title == 'Send'
    assert task.task in controller._tasks
    assert task._lifetime is ExternalTaskLifeTime.MODEL
    task.cancel()
    await asyncio.gather(task.task, return_exceptions=True)


@pytest.mark.asyncio
async def test_send_modal_resolves_the_controller_before_acknowledging() -> None:
    """A missing active controller fails without consuming the one available interaction response."""
    response = SimpleNamespace(send_modal=AsyncMock())
    interaction = _send_modal_interaction(response.send_modal)

    with pytest.raises(RuntimeError, match='inside flow'):
        await modal.send_modal(_finish, interaction, modal.ModalConfig(title='Unmanaged'), ())

    response.send_modal.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_modal_response_failure_does_not_register_an_external_task() -> None:
    """A failed interaction acknowledgement cannot leave an unreachable modal waiter behind."""
    failure = RuntimeError('response failed')
    response = SimpleNamespace(send_modal=AsyncMock(side_effect=failure))
    interaction = _send_modal_interaction(response.send_modal)
    controller = Controller(ModelBase())

    with pytest.raises(RuntimeError, match='response failed'):
        await modal.send_modal(
            _finish,
            interaction,
            modal.ModalConfig(title='Failed response'),
            (flow.TextDisplay('Notice'),),
            controller=controller,
        )

    assert not controller._tasks


@pytest.mark.asyncio
async def test_send_modal_registration_failure_stops_the_already_sent_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-send task-registration failure does not leave a live modal without a flow owner."""
    failure = RuntimeError('task registration failed')
    response = SimpleNamespace(send_modal=AsyncMock())
    interaction = _send_modal_interaction(response.send_modal)
    controller = Controller(ModelBase())

    def fail_registration(*_: object, **__: object) -> None:
        raise failure

    monkeypatch.setattr(controller, 'create_external_result', fail_registration)
    with pytest.raises(RuntimeError, match='task registration failed'):
        await modal.send_modal(
            _finish,
            interaction,
            modal.ModalConfig(title='Registration failure'),
            (flow.TextDisplay('Notice'),),
            controller=controller,
        )

    sent = response.send_modal.await_args.args[0]
    assert isinstance(sent, _InnerModal)
    assert sent.is_finished()


if TYPE_CHECKING:

    def _check_modal_config_types() -> None:  # type: ignore[reportUnusedFunction]
        config = flow.ModalConfig('Survey')

        assert_type(config, modal.ModalConfig)
        assert_type(config.title, str)
        assert_type(config.timeout, float | None)
        assert_type(config.custom_id, str | None)

    def _check_modal_value_types() -> None:  # type: ignore[reportUnusedFunction]
        text = flow.TextInput().field()
        select = flow.Select().field()
        user = flow.UserSelect().field()
        role = flow.RoleSelect().field()
        mentionable = flow.MentionableSelect().field()
        channel = flow.ChannelSelect().field()
        files = flow.FileUpload().field()
        radio = flow.RadioGroup().field()
        checks = flow.CheckboxGroup().field()
        checkbox = flow.Checkbox().field()

        assert_type(text, flow.ModalItem[flow.TextInput, str])
        assert_type(text.value, str)
        assert_type(select.value, list[str])
        assert_type(user.value, list[Member | User | str])
        assert_type(role.value, list[Role | str])
        assert_type(mentionable.value, list[Member | User | Role | str])
        assert_type(channel.value, list[AppCommandChannel | AppCommandThread | str])
        assert_type(files.value, list[Attachment])
        assert_type(radio.value, str | None)
        assert_type(checks.value, list[str])
        assert_type(checkbox.value, bool)
