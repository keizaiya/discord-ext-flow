# discord-ext-flow
This library extends discord.ui, providing UI control based on state flows.

It supports a cycle that includes displaying the initial state, handling interactions, generating new states, and displaying these new states.  
Examples can be found in example directory.

Start each flow with a fresh controller through the recommended entry point:

```python
from discord.ext.flow import run_flow

await run_flow(FirstModel(), channel, on_error=handle_error)
```

`run_flow(model, messageable, message=None, *, on_error=None)` creates a controller
and awaits its invocation. `message` supplies an existing initial edit target.
Direct `Controller` access remains available for external task registration or
subclassing. Callers must use a fresh instance for each invocation; reuse is not
checked. `Controller.copy()` has been removed; use `run_flow` or construct a new
controller for each run.

Internally, `Controller` owns model transitions, task scheduling and error
handling. `FlowDisplay` in `display.py` owns the delivery context, current
message and view, and disabling controls. Its `_send` method selects the
interaction and edit target and orders the delivery attempts; stateless functions
in the same module adapt each Discord API endpoint. It does not depend on
`Controller` or manage tasks. A failed
send retains the previous display; once sending succeeds, the new display is
retained even if disabling the previous message fails. Controller then reclaims
the retired view's tasks. `FlowDisplay` is not part of the public API.

Item and view callback-capability checks live in `item.py` and `view.py`,
respectively. `util.py` contains only the optional-value helpers shared by view
and modal construction.

`Result.interaction` is optional. UI and modal callbacks automatically use the
interaction that triggered them; an explicitly supplied interaction takes
precedence for that result. For flows started from a normal `Messageable`, the
controller retains that destination for the entire flow. External non-ephemeral
notifications use it even after UI-driven
model transitions or message replacements. External ephemeral sends and flows
started from an interaction use the retained initial or component interaction.
Modal callback interactions are used for their own result response and
associated previous-screen cleanup only; they do not become the destination for subsequent
external results. An ephemeral send requires an available interaction; internal
helpers enforce this precondition with an assertion.

The controller retains the actual Discord message, including its channel and
visibility flags. Non-ephemeral edits use a bot-authenticated `PartialMessage`
created from its channel and ID, including for messages originally sent or
edited through an interaction. They can be updated and have their controls
disabled after the interaction token expires, provided
the bot can still access and edit the message. Ephemeral messages retain their
interaction/webhook message and remain editable while the corresponding
token is valid. An acknowledged component interaction for the same message can
also provide a new edit route. Ephemeral delivery is never changed to a normal
channel send to work around an expired token.

With `edit_original=True`, the supplied edit target takes precedence over an
interaction's source message. When both identify the same message, flow first
tries the interaction edit, then the stored message editor, then a new send.
Without an edit target, the interaction's source message is used when present;
otherwise flow sends a new message. Discord API failures advance to the next
available route; programming errors propagate. Failed non-ephemeral interaction
sends may use the retained normal `Messageable` destination. A failed edit of
an ephemeral target keeps new-message fallback ephemeral, even when the edit
request omitted `ephemeral=True`. Direct editing of a different explicit target
does not acknowledge an interaction; the caller must acknowledge it first.

When sending a new message in response to an unacknowledged interaction with a
source message, flow first defers the source update and then sends a follow-up.
This preserves the interaction's source-message binding so the previous screen
can still be disabled, including ephemeral screens. If deferring fails with a
Discord API error, the remaining send routes are still attempted.

Set `disable_items=True` on each screen that should have its controls disabled
when it is replaced or the flow ends. Its default is `False`. Disabling edits
only the view; content, embeds, and attachments are preserved. Token expiry can
still prevent finalizing an ephemeral screen; such failures propagate after
local view/task cleanup. Long-lived external notifications and edits should use
non-ephemeral messages in a flow started from a normal `Messageable`.

Messages with no items (`items=None` or an empty `items=()`) are sent without a
view and finish the flow, cancelling remaining external result tasks during
cleanup. Non-empty items that do not produce callbacks still create a view but
do not themselves make the flow wait for an interaction.

Flow callbacks and external result coroutines should normally catch expected
errors themselves and return a `Result` describing whether the flow should
continue or finish. As a final fallback, a model may define
`on_error(ExceptionGroup[Exception])`, and `Controller` accepts an `on_error`
callback for errors not handled by a model. The model handler is preferred for
errors from that model's UI callbacks and view timeout; the controller handler
receives the remaining UI errors, external task errors, and timeouts.

Handlers receive an `ExceptionGroup` and may return a `Result` to recover or
transition the flow, or `None` to only report the errors. The group contains the
original exceptions, or `FlowTimeoutError` for a view or modal timeout. No UI item or
interaction is supplied. A timeout handler can replace the expired view or transition
to another model by returning the corresponding `Result`; `None` and
`Result.continue_flow()` allow the timeout to finish the flow. Results returned while
cleanup is reclaiming tasks are only notified and are not applied.
Exceptions recovered during flow cleanup propagate to the caller of
`Controller.invoke`.
