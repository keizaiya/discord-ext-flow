# discord-ext-flow
This library extends discord.ui, providing UI control based on state flows.

It supports a cycle that includes displaying the initial state, handling interactions, generating new states, and displaying these new states.  
Examples can be found in example directory.

Flow callbacks and external result coroutines should normally catch expected
errors themselves and return a `Result` describing whether the flow should
continue or finish. As a final fallback, a model may define
`on_error(ExceptionGroup[Exception])`, and `Controller` accepts an `on_error`
callback for errors not handled by a model. The model handler is preferred for
errors from that model's UI callbacks and view timeout; the controller handler
receives the remaining UI errors, external task errors, and timeouts.

Handlers receive an `ExceptionGroup` and return `None`. The group contains the
original exceptions, or `FlowTimeoutError` for a view timeout. No UI item or
interaction is supplied, and a timeout handler does not restart the expired
view. A handler is intended for reporting and final fallback cleanup; it does
not replace handling expected errors inside the callback or external operation.
