# Bounded upstream producer admission (opt-in protocol 3)

This transport lets an already-authorized, live native coordinator receive
later calls from one Argus producer without asking the parent to enroll each
new request UUID. Argus still selects every role, prompt, task transition,
review decision and continuation. This does not add another workflow scheduler
or another Argus role. Python publishes execution requests and journals results;
only the online native coordinator calls real native tools.

The concrete improvement over v2 is the dependent-call case: the original
gateway finishes and consumes turn one; upstream then creates turn two, whose
exact identity did not exist at initial enrollment. The bound transport admits
that call into the original finite session, and the native coordinator can
perform its existing spawn/followup/bind/record workflow without a root relay.

## Authorization and capability boundaries

The parent creates a new explicit scope for one producer, canonical project
root, nonempty mission ID, exact role allowlist, maximum total calls, maximum
concurrency and immutable expiration. Bounds remain at most 64 calls, 5 active
workers and 3600 seconds. Reserve actual native capacity for the parent and
coordinator; the configured limit does not reserve platform slots.

All request-scoped operations on the bound producer object check its durable
admission owner, including reads, cancellation, consumption and tool replies.
The bound producer cannot emit native host events. An unbound host retains the
explicit host APIs for separately authorized observation and cancellation.

This is a trusted-host scope, not cryptographic producer authentication or a
boundary against malicious processes running as the same OS user. Producer and
project identifiers are not secrets or execution permissions. In particular,
the project binding does not isolate the worker's filesystem. The parent must
already be authorized for the task category, submitted data, tools and any
consequential actions. Neither queue text nor a claimed producer ID grants that
authority. Do not use this interface to watch arbitrary directories or admit
unrelated work.

Protocol 3 inherits v2 capabilities unchanged: same-role continuation and the
typed role-tool channel only. Execution options remain unsupported, explicit
model/effort requests remain rejected, and the original gateway capability
checks remain in force. `automatic_dispatch` remains false in the backend
report: a Python transport still cannot call native tools or guarantee a live
host. A full production Manager/Planner/Engineer/Reviewer/Curator call requiring
missing controls must fail before dispatch. Do not remove controls, advertise
prompt-based isolation, or silently fall back to a different provider.

## Create one scope

Use a fresh private queue and the existing project interpreter. Start the
actual native coordinator and obtain its real canonical task path before
creating the scope. The coordinator initially waits for the session identity.
The example paths, identities and authorization bounds must be replaced with
the current approved values.

```sh
python -m argus.apps.dots_admission --bridge-dir /private/queue create \
  --parent /root --coordinator /root/coordinate_argus \
  --producer argus-authorized-run --project-root /authorized/project \
  --mission authorized-mission --roles manager engineer \
  --max-requests 8 --max-concurrency 1 --lifetime 900
```

The returned protocol-3 session begins with no requests. An empty session is
`wait`, not `done`, while producer admission is open. No files already in the
queue are discovered or admitted. v1/v2 sessions cannot be upgraded or adopted
as protocol 3.

## Bind the original upstream producer

```python
from argus.adapters.dots_admission import BoundedRoleFileDotsTransport
from argus.apps._runtime_construction import build_dots_life_runner

transport = BoundedRoleFileDotsTransport(
    QUEUE,
    session_id=ACTUAL_SESSION_ID,
    producer_id="argus-authorized-run",
    project_root="/authorized/project",
)
runner = build_dots_life_runner(existing_args, transport=transport)
# Drive the existing upstream runtime. Do not rewrite its role flow or options.
# Its calls must carry the existing ARGUS_PLUGIN_PARENT_MISSION_ID value
# matching this session. Missing/different mission identity fails closed.
# Unsupported original execution requirements still fail before submission.
```

The existing `DotsBackend` calls `transport.submit(request)`. The bound
transport validates producer/project identity, mission, role, execution
capabilities, deadline and remaining total capacity. Under the bridge-root lock
it reserves that exact request ID for this session/producer/project/mission and
complete request digest, then publishes the original request unchanged and
enrolls it. This is the explicit receipt channel; no
queue glob, separate watcher or arbitrary-file execution is involved.

The producer chooses when its work is finished. After its final gateway result
has been consumed, call `transport.close_producer()` (also available as the
`close-producer SESSION --producer ID --project-root PATH` host command).
Closing is idempotent and seals new admission without cancelling accepted
work. Use the original `stop` command to cancel rather than finish normally.

## Native host commands

Use `python -m argus.apps.dots_admission` instead of `dots_role_host` for this
session. `next`, `bind`, `record`, `request-tool`, `status`, `stop`, `handoff` and
`abandon` retain their original v2 arguments and meanings, including the current
turn marker and actual observed worker identities. See
[v2 operations](dots-role-host.md) and [delivery priority](HOST-PRIORITY.zh-CN.md).

The coordinator handles each newly admitted call with the real native tools.
It must promptly record an available current final result, respect earlier
stop/expiry, and verify actual producer consumption separately from terminal
publication. A new request is never permission for extra roles or actions.
The host does not infer Argus transitions from worker prose.

`next` reports `done` only after all admitted requests have terminal events and
producer admission is closed, stopped or expired. `done` is not evidence that
all calls succeeded or that every producer consumed its result. While open and
empty, wait within the existing session/request deadlines; do not exit merely
because the previous call finished. Do not extend the session to avoid a
timeout or continue with a new authorization scope automatically.

## Continuation, cancellation and recovery

- A continuation must reference a verified worker from this exact bounded
  producer session, with the same original role/mission/parent and a confirmed
  completed prior turn. A second pending turn for that worker is rejected
- Existing registry reservation and claim-before-spawn/followup prevent
  uncertain dispatch from becoming an automatic retry. Preserve exact current
  result envelopes and the original call-bound `ReviewActions.dispatch`
- Producer close, submit, coordinator claims and stop serialize through the
  same root lock. Session expiration is unchanged; it may precede an individual
  request timeout and still wins. No new request extends either deadline
- Stop and expiry reject new admission. Existing `next` cancels undispatched
  requests and asks the host to stop mapped workers or reconcile unknown
  dispatch. A stop record is not proof that the worker stopped
- Worker stop alone does not settle an in-flight role tool. The original v2
  pending-tool/settlement gates still apply in protocol 3
- An exact repeat of an already-enrolled submission returns its original
  receipt, even after closure. It neither reopens the request nor starts a turn.
  Changed bytes under the same ID fail closed
- Durable owner reservation precedes request publication and enrollment under
  the root lock. Cross-session UUID collisions are rejected even before native
  claim. v1/v2 hosts and raw event emission cannot take a reserved request
- If publication
  fails before enrollment, no coordinator can discover or execute that orphan.
  An explicit retry of the identical request can recover an intact, unhandled
  publication only when its reservation names the same session. Unowned raw
  files and conflicting, closed, claimed, handled or identity-incomplete records
  are rejected. The bound producer may still cancel its own reserved orphan
  after uncertain submission. There is no automatic gateway retry or new UUID
  retry
- If enrollment was committed but its response was lost, an exact retry returns
  the saved receipt. If the gateway already cancelled the request, cancellation
  still wins. A new request cannot revive the uncertain old native turn
- Reconstructing the host object restores bookkeeping only. Parent-directed
  `handoff` retains scope, original deadline and actual worker mappings while
  revoking the prior generation. It requires the same real native stop/status
  verification as v2; a missing worker list entry is not proof of no execution

There is no daemon, public receiving API, native wakeup, automatic restart or
durability guarantee for the coordinator itself. When the native host is gone,
Python may persist a bounded request but cannot execute it. Original producer
timeouts and honest unconfirmed-cancellation reporting remain applicable.

## Verification scope

The separately completed real two-turn native acceptance is summarized in
[bounded admission validation](dots-admission-native-validation.md). Its
observations remain distinct from the fixture tests below.

```sh
python -m pytest tests/test_dots_backend.py tests/test_dots_file_transport.py \
  tests/test_dots_coordinator.py tests/test_dots_cancellation_ack.py \
  tests/test_dots_role_host.py tests/test_dots_admission.py \
  tests/test_workflow_dots_lifecycle.py tests/test_workflow_dots_model_defaults.py
```

Tests use explicitly named fixture workers. They include the original gateway
performing two dependent calls, concurrent duplicate receipts/claims, scope and
capability rejection, unrelated queue files, empty/open/closed sessions,
publication/enrollment failure boundaries, frozen bytes, same-worker resume,
generation handoff, cross-session reservation races, bound-operation isolation,
legacy-protocol rejection and typed role-tool feedback. They do not prove native
execution, execution isolation, full-role production compatibility, real-time
handoff latency or unattended operation.

A separate real native acceptance should use two authorized text probe calls:
the producer creates the second only after consuming the first, the same
coordinator admits it without a parent relay, and the original worker handles
its explicit continuation. Require current markers, actual worker identity,
both producer exits/consumed states and no unlisted admission. Native trials
and formal research are not started by installing this module.
