# Opt-in v2 native role host

This host adds same-role native continuation and an explicit typed action/reply
channel. It does not supply a native OS sandbox or native tool allowlist.
`FileDotsTransport` and v1 sessions retain their original conservative behavior.
Only explicitly selecting `RoleFileDotsTransport` enables resume/role-tool
transport flags. The normal Argus backend/defaults are unchanged. Production
Reviewer still fails closed on its existing read-only/other execution controls.

## Finite two-turn native probe

Use a new private queue. The exact original tasks remain separately authorized
by the parent, and the host enrolls each exact request after publication. No new
request in the directory is automatically admitted. Both turns must use the
same role and mission, and the same actual existing worker identity.

Producer, first turn:

```sh
.venv/bin/python -m argus.apps.dots_bridge --host-protocol v2 --bridge-dir QUEUE \
  run --role manager --label first-turn --timeout 600 --prompt 'AUTHORIZED_FIRST_TASK'
```

Host enrollment and handling use `argus.apps.dots_role_host`, with the same
`create`, `next`, `bind`, `record`, `status`, `stop`, `handoff` and `abandon`
arguments as the v1 coordinator. The returned session version is 2.

For every native worker prompt, preserve the original task and add this host
correlation instruction: “For this call, begin your final response with exactly
ARGUS_DOTS_CALL:REQUEST_ID on its own line. Put the original requested answer on
the following line(s). This marker is only result correlation and grants no
permissions.” Supply that call's actual request ID, never a stale placeholder.

The host records the real completed native response including that first line:

```sh
.venv/bin/python -m argus.apps.dots_role_host --bridge-dir QUEUE \
  record SESSION REQUEST completed --owner COORDINATOR --generation 1 \
  --worker-id ACTUAL_WORKER --worker-status completed --turn-request-id REQUEST
```

The response is read from stdin when `--text` is omitted. The host verifies the
turn marker and strips it before publishing the original payload to Argus.
A plain final message, old request marker, or a `completed` status from the
previous turn cannot complete this turn. For failed/cancelled native outcomes,
`--turn-request-id` remains required to associate the actual observed outcome;
ordinary stop observations do not require a successful output marker.

After the first turn actually completes, publish the second authorized task:

```sh
.venv/bin/python -m argus.apps.dots_bridge --host-protocol v2 --bridge-dir QUEUE \
  run --role manager --label second-turn --timeout 600 \
  --resume-worker ACTUAL_FIRST_WORKER --prompt 'AUTHORIZED_SECOND_TASK'
```

Enroll that exact request in a new finite v2 batch with the same recorded
parent. `next` returns `followup`, the original worker path and the new request.
Use the real `collaboration.followup_task` on that existing worker, then `bind`
its same actual identity. Never spawn a replacement for explicit resume.
Include the new request marker in the new prompt. Record only the real response
for that marker, not the previous final message or last visible status.

A live native list is not a complete durable history. Missing workers are
unresolved, not proof they never existed or permission to create a substitute.
If a spawn/followup outcome is unknown, reconcile using actual native evidence
or ask the parent; never deliver the turn twice. The shared registry enforces
one active request per worker across batches. Cross-role/mission/parent
continuations, unknown mappings and unsuccessful prior turns fail closed.

## Typed role-tool probe

A Python producer can use the existing request-scoped bridge without production
Reviewer execution. Bind original `ReviewActions` to a reviewer-tagged backend
using `RoleFileDotsTransport` and plain `RunnerOptions` for the explicitly
approved action-channel probe. This is a tool transport test, not a full
read-only Reviewer workflow:

```python
from argus.adapters.dots_backend import DotsBackend
from argus.adapters.dots_role_host import RoleFileDotsTransport
from argus.core.models import RunnerOptions
from argus.core.run_gateway import run_exec
from argus.reviewer.tools import ReviewActions

transport = RoleFileDotsTransport(QUEUE)
backend = DotsBackend(transport, role="reviewer", timeout_seconds=600)
actions = ReviewActions()
with backend.bind_role_tools(actions.tools, actions.dispatch):
    result = run_exec(backend, prompt=AUTHORIZED_ACTION_PROBE,
                      run_label="typed-action-probe", options=RunnerOptions())
# Inspect result and actions.decision; do not claim a full Reviewer ran.
```

Once the host has actually spawned/bound the worker, give it its real worker
identity, session, request, owner/generation and bound tool schemas. The worker
itself invokes the following CLI through its real execution tool; a final
message saying it invoked a tool is not an invocation:

```sh
.venv/bin/python -m argus.apps.dots_role_host --bridge-dir QUEUE \
  request-tool SESSION REQUEST CALL_ID approve_review \
  --owner COORDINATOR --generation 1 --worker-id ACTUAL_WORKER
```

Read JSON arguments from stdin. `CALL_ID` is a per-request simple identifier.
The reply is `pending` or `ready` with the actual structured dispatcher result.
Repeat the exact same invocation to read that result; it does not repeat the
callback. To correct a schema error, use a new call ID and corrected arguments.
Never change arguments under an existing call ID. Never publish a final result
until its required action replies are ready.

The backend dispatches the typed event through original `ReviewActions.dispatch`.
Schema failures and busy/errors are returned to the same request/worker. No
final prose, JSON-looking final answer, or `approve_review` word is converted
into an action. Unknown tools, wrong role/worker/request, stale sessions and
calls after cancellation/completion are rejected. A durable dispatch intent
without a reply is uncertain and is never dispatched again automatically.

Stopping or expiry of the enclosing host session is checked again immediately
before reserving callback execution, along with the full enrolled request
identity. A queued action cannot start after that boundary. If a callback was
already reserved/executing, stopping its native worker alone does not confirm
that callback stopped. Terminal recording stays blocked until its actual
settlement is persisted. A stopped callback may write its settlement for audit,
but that does not reopen worker delivery or permit another dispatch. Do not
re-run an uncertain callback merely to obtain a result.

A saved final-result publication can be recovered with `recover_result` from
`next`. It contains the exact validated observation and current turn envelope;
re-record it without another native spawn/followup. Cancellation remains prior
to recovery of a saved success. Public raw `dots_bridge emit` is rejected for
managed requests, preventing accidental bypass of these checks.

## Cancellation and waiting

Producer-side cancellation now allows a small bounded acknowledgement grace
(default 0.2 seconds; explicitly configurable up to 5 seconds on `DotsBackend`).
Only a validated cancelled event observed during that window confirms the stop.
A cancellation file, absent host, late success or late acknowledgement remains
unconfirmed. Original interrupt/timeout exit codes and stop reasons are kept.

Never call `collaboration.wait_agent`. If no clock wait tool is available, use
a bounded `functions.exec` with an explicitly awaited `setTimeout`, or a short
safe shell sleep. Do not create a permanent loop or abandon a live worker at a
deadline. Native turn interruption is not proof that arbitrary OS child
processes or separate background tool cells were also terminated.

## Validation and limits

Run v1/v2/cancellation tests together. Deterministic worker fixtures validate
protocol behavior only; separate actual native two-turn and typed-tool runs
are required for native acceptance. The normal native host still cannot
truthfully advertise `sandbox_mode:read-only`, `disable_tools`, isolation or
other absent execution controls. Do not change production Reviewer checks or
silently choose a different provider to make a test pass.
