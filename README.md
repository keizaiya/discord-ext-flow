# discord-ext-flow
This library extends discord.ui, providing UI control based on state flows.

It supports a cycle that includes displaying the initial state, handling interactions, generating new states, and displaying these new states.  
Examples can be found in example directory.

`Result.interaction` is optional. UI and modal callbacks automatically use the
interaction that triggered them. External results use the active flow message's
`messageable` when no interaction is supplied; an explicitly supplied
interaction takes precedence. This applies to external `send_message`,
`next_model`, `continue_flow`, and `finish_flow` results.

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

Handlers receive an `ExceptionGroup` and return `None`. The group contains the
original exceptions, or `FlowTimeoutError` for a view or modal timeout. No UI item or
interaction is supplied, and a timeout does not restart the expired view. A
handler is intended for reporting and final fallback cleanup; it does
not replace handling expected errors inside the callback or external operation.
Exceptions recovered during flow cleanup propagate to the caller of
`Controller.invoke`.
