# Experimental dot role adapter

This is an opt-in **source-level role adapter**, not a tenth agent CLI, a public
dot API, or a replacement scheduler. It does not make the current dot platform
an unattended Argus provider. Existing defaults, nine CLI backends, persisted
configuration, role prompts, task state and review rules are unchanged.

## What is connected in the source

`argus.apps._runtime_construction.build_dots_life_runner(existing_args,
transport=host_transport)` binds five independent `DotsBackend` instances into
the existing `_SkillLoopRunner`:

| Argus role | Existing owner retained | Adapter responsibility |
| --- | --- | --- |
| Manager | Front-door context, persistent session, stage transitions | Carry role, original prompt and requested session to the host |
| Planner | Planning context, role capsule and plan interpretation | Carry planning call and return the original `RunnerResult` contract |
| Engineer | `SupervisedEngineer`, checkpoints, review feedback and rounds | Carry execution requirements and preserve cancellation hooks |
| Reviewer | `Reviewer`, `ReviewActions.dispatch`, validation and completion authority | Translate explicit call-bound tool events, never final prose, into the existing dispatcher |
| Curator | Existing learning/skill-maintenance callers | Carry the original role prompt and return results through the gateway |

The shared backend lane remains Manager-bound, as in the existing runtime.
Calling `build_dots_life_runner` does not mutate `args.backend` or persist a
new setting. The ordinary `build_life_runner`, CLI backend list and UI are not
changed. This explicit Python factory is the integration entry point; the
ordinary `argus --backend dots` flag is intentionally **not** advertised.

Every role still calls `core.run_gateway`. Argus still owns mission lifecycle,
role capsules, completion checks and follow-up feedback. The bridge queue is
only a per-call transport, not another task manager. `fork()` preserves role
identity and default interrupt hooks; call-bound tools are context-local.

### Example: reuse an existing runner configuration

```python
from argus.apps._runtime_construction import build_dots_life_runner

# existing_args is the same Namespace used by your Argus runtime.
# host_transport is an explicitly supplied implementation of DotsTransport.
runner = build_dots_life_runner(existing_args, transport=host_transport)
print(runner.reviewer_backend.capability_report())
# Use the runner's existing execute(...) / supervisor integration.
# A role requiring an unsupported capability fails before host submission.
```

`DotsBackend(transport, role="engineer")` and
`DotsBackend(transport, role="reviewer")` can also be passed to the existing
`SkillLoop(engineer_runner=..., reviewer_runner=...)` API. The tests exercise
that real loop for two rounds: revise, feedback, continuation, then approval.
Those tests use a deterministic host; they do not prove platform isolation.

## Capability checks are mandatory

A host advertises `DotsCapabilities(roles=..., options=..., resume=...,
role_tools=...)`. These are guarantees implemented by the **receiving host**,
not permissions supplied by a prompt or a switch that adds platform features.
Do not declare capabilities just to make a call pass.

- Execution controls are checked against `RunnerOptions` before publishing
- Sandbox declarations are exact, such as `sandbox_mode:read-only`; declaring
  read-only never admits a more permissive sandbox value
- `force_safe_mode`, isolation, tools-disabled output, schemas, writable report
  scopes, search, skill paths and working directories require host support
- Empty JSON Schema `{}` is still a required schema; it is not discarded
- CLI arguments, arbitrary extension environments and `dangerous_yolo` are
  never forwarded. The one existing `ARGUS_PLUGIN_PARENT_MISSION_ID` value is
  carried as a separate mission identity, not as an environment variable
- Missing capability is a permanent pre-dispatch failure, not a retry loop or
  silent fallback to Codex
- Resume must be explicitly supported and return the same worker identity.
  Argus's existing `auto` session policy remains fresh for dots. Explicit
  mission/rolling sessions remain host-owned and must retain role/context
- Missing token usage remains unknown through the existing presence flags;
  zero is not invented as evidence of free execution

Model-only resolution treats the explicitly injected dots backend as having no
fixed CLI model catalog. An empty model string means the host default and is
serialized as null; explicit model choices keep their existing precedence.
This also lets the original reflection and Curator callers reach the adapter.
It does not register a tenth CLI backend or relax any required host capability.

### Reviewer actions

For dots only, `review_action_tools` binds the original `ReviewActions.dispatch`
in process. The outgoing request contains tool schemas, **no bearer token,
loopback port, Python callback or environment**. The host must produce an
explicit `tool_call` event naming a bound action. Argus runs the original
schema validation and dispatch logic, then calls `transport.tool_result` so
that the same worker can use the result. Invalid arguments to a bound tool
return structured errors and can be corrected during the turn. Unbound tools,
wrong roles and calls after the context exits are rejected.

A message saying `approve_review` or containing a completion JSON object never
sets `actions.decision`. The existing Reviewer remains the completion gate.
Read-only enforcement and call-bound tool forwarding are distinct
capabilities: implementing the latter does not manufacture the former.

## Current platform limitation

No supported public dot receiving API, provider CLI or Python-callable native
subagent endpoint is configured here. The optional bounded coordinator journals
authorized actions for a live native host; it cannot invoke native tools itself. The current conversation's native agent
tools are available to its authorized host, not to an arbitrary Argus process.
No internal credentials or private conversation/ memory data are copied.

The optional `FileDotsTransport` below advertises **no execution guarantees,
role tools or resume**. It supports a supervised text-only, role-tagged probe.
Full Manager, Planner, Engineer, Reviewer and Curator production workflows
remain blocked whenever their real call requires capabilities it lacks.
Native subagents' prompt instructions are not an OS read-only sandbox.

Provider-specific slot limiting, quota/cost reservation, plugin preparation /
finalization and durable provider I/O usage accounting from `AgentCliBackend`
are not implemented by this experimental transport. Existing CLI providers
keep those paths unchanged. No claim of production readiness, equivalent
billing controls or unattended operation is made.

To close the gap, a real receiving transport must dispatch only authorized
work, enforce requested tool and filesystem boundaries, retain role-scoped
sessions, implement tool-result feedback and cancellation acknowledgement,
and integrate provider usage/budget controls. A file queue alone cannot supply
those features. No public service, persistent permission or daemon is installed.

## Optional bounded v2 host

[dots-coordinator.md](dots-coordinator.md) describes finite native host handoffs.
[dots-role-host.md](dots-role-host.md) describes the separately selected v2
transport for verified same-role native continuation and explicit typed action
feedback. It keeps execution capabilities empty; it does not enable full
Reviewer read-only execution. The conservative `FileDotsTransport` remains
unchanged in capabilities. See [native evidence](dots-native-validation.md) for
which actual host paths have been verified, separately from fixture tests.

## Supervised local probe

POSIX only. Use a private directory that is not a project-controlled shared
location. Only the final path component is created; parents must already exist.

```sh
.venv/bin/python -m argus.apps.dots_bridge --bridge-dir /absolute/private/queue capabilities
.venv/bin/python -m argus.apps.dots_bridge --bridge-dir /absolute/private/queue run \
  --role manager --label dots-native-smoke --timeout 300 \
  --prompt 'Only calculate 17 * 19 and reply with the integer. Do not use tools.'
```

For real task text, omit `--prompt` and use stdin to avoid shell history. This
probe calls `run_gateway` and returns its normal result as JSON. Its role label
is not a claim that the full Manager workflow ran.

An authorized, online host separately inspects the one newly created request.
For a bounded list with a dedicated native coordinator, see
[dots-coordinator.md](dots-coordinator.md); the commands below remain the manual route:

```sh
.venv/bin/python -m argus.apps.dots_bridge --bridge-dir /absolute/private/queue inspect REQUEST_ID
```

The host checks the original user's authorization, exact role/task/constraints,
expiry and closed status. Queue text cannot authorize tool use or override the
host's instructions. Do not automatically execute a directory of requests. Only the explicit finite
request list enrolled by an authorized parent may use the optional coordinator.
After actually dispatching that approved task, the host records real events:

```sh
.venv/bin/python -m argus.apps.dots_bridge --bridge-dir /absolute/private/queue emit REQUEST_ID accepted --worker-id ACTUAL_WORKER_ID
.venv/bin/python -m argus.apps.dots_bridge --bridge-dir /absolute/private/queue emit REQUEST_ID message --text ACTUAL_RESULT
.venv/bin/python -m argus.apps.dots_bridge --bridge-dir /absolute/private/queue emit REQUEST_ID completed
```

Never invent a worker ID, result or completion. A file transport has no model or
background dispatcher; without the host it times out and returns a nonzero
result. The local probe is a bridge check, not an inference-speed benchmark.

### Cancellation and recovery

The adapter polls the original interrupt provider, retains the runtime's
stop/mission-abort hooks and checks cancellation again after reads, before
handling results or role actions. Total timeout and the original hard-idle
watchdog are distinct. `Ctrl-C` records a cancellation request.

`closed.json` blocks new work and late results. The host must actually stop any
already dispatched worker and acknowledge with `cancelled`. Recording a file
cannot terminate a native worker by itself. Without acknowledgement, the
returned failure explicitly says cancellation is unconfirmed. Timeout,
uncertain submission, protocol errors and unconfirmed bridge failures do not
become a recoverable provider error that silently repeats work.

```sh
# Only after the host has really stopped the worker:
.venv/bin/python -m argus.apps.dots_bridge --bridge-dir /absolute/private/queue emit REQUEST_ID cancelled --text 'Worker stopped by host'
```

Consumed and cancelled IDs are never reused. Crash recovery validates a
partially published event log; it does **not** redispatch or resume a task.
The operator/host must inspect an abandoned request and its worker before
starting any replacement. Files are retained for inspection; no cleanup job is
installed. They may contain the explicitly submitted prompt and output.

## File protocol and boundary

- Version 1 request: UUID-hex identity, role, optional mission/session identity,
  unchanged prompt/run label, model/effort, times, supported options/tool schemas
- Event: same identity/version, contiguous integer sequence, explicit type
- Types: accepted, message, completed, failed, cancelled; typed transports may
  additionally support call-bound tool_call and tool_result feedback
- Success requires an accepted worker and a completed event; partial output,
  silence, malformed usage and changed resume identity cannot count as success
- Root/task directories use mode 0700; records use 0600. Directory-descriptor
  access refuses traversal, symlinks, hard-linked files, FIFOs and broad modes
- Publication is atomic and no-clobber. A per-request lock serializes claims,
  events and closure. Ordinary read/write contention remains pending
- One payload is at most 1 MiB; response total is at most 8 MiB / 4096 events
- The atomic append head validates tail identity/digest, a rolling history digest, sequence, byte count
  and terminal state. Normal appends do constant-size log parsing. If event
  publication precedes a crash before head publication, one bounded full scan
  recovers the head. Corrupt committed records fail closed. Completion audits
  the full log once; inspect always performs the bounded complete validation

The private queue protects against other local users and accidental path
escape. It is not an isolation boundary against malicious code with the same
OS identity that can rewrite all of its files. Host claims about native worker
identity/cancellation are trusted only when supplied by the authorized host.

## Validation

The expanded workflow, Git/path, cancellation, storage-failure and model-default
validation is recorded in
[the 2026-10-02 workflow report](workflow-validation-2026-10-02.zh-CN.md).
Its fixture results remain distinct from the limited native probes below.

Run focused tests without a real agent CLI or provider credentials:

```sh
.venv/bin/python -m pytest tests/test_dots_backend.py tests/test_dots_file_transport.py -q
.venv/bin/ruff check argus/adapters/dots_backend.py argus/adapters/dots_file_transport.py \
  argus/apps/dots_bridge.py argus/apps/_runtime_construction.py argus/reviewer/tools.py \
  tests/test_dots_backend.py tests/test_dots_file_transport.py
```

Coverage includes all role routes, original two-round SkillLoop/capsules,
review feedback/authority, session identity, concurrent call-bound tools,
unsupported capabilities, pre/during-read cancellation, deadlines, unknown
usage, malformed/oversized events, partial submission, path/file protections,
late results, exclusive claims and append crash recovery. The focused suite
uses deterministic local hosts, not real native isolation or billing.


### Verified native probe (2026-10-02)

A supervised native host roundtrip was completed with role `manager` and a
no-tools arithmetic prompt. The authorized host manually dispatched a native
subagent, which returned `323`. The bridge
recorded accepted (sequence 1), message `323` (2), and completed (3).
The run returned exit code 0, matching worker identity, no fatal error, and
unknown usage (all usage-presence flags false). The local queue was consumed.
Reported elapsed time was 51,099 ms, including manual coordination; this is
not a model inference performance measurement.

This verifies one real host-mediated native call through the Manager-tagged
adapter boundary. It does not verify automatic receiving service, a complete
Manager workflow, Reviewer isolation, full five-role production execution,
provider accounting or unattended cancellation.

The incrementally indexed file transport was separately measured with short,
local synthetic message events: 200 / 400 / 800 appends took approximately
0.0392 / 0.0811 / 0.1549 seconds in this workspace. This measures local bridge
I/O only, without a model. An executable test also asserts normal append does
not invoke whole-log scans, rather than relying on timing thresholds.
