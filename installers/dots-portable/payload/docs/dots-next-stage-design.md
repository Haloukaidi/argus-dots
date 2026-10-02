# Native dots role host: historical design rationale

This is the historical plan and its invariants, not a statement that the entire
v2 feature set is still unimplemented. Same-role continuation, typed role-tool
feedback, pending-result recovery and bounded cancellation acknowledgement are
now implemented. Actual finite-coordinator, two-turn native continuation and
typed action/reply probes passed; see [native evidence](dots-native-validation.md).
Current operation is documented in [dots-role-host.md](dots-role-host.md).
The production Reviewer isolation and provider-usage dependencies below remain
unresolved. Do not reinterpret or upgrade an active session in place.

## 1. Stable role sessions and sequential native continuations

The existing adapter already has the essential consumer contract:
`RunnerResult.thread_id` comes from the accepted actual worker ID, and an
explicit `resume_thread_id` requires the same worker on acceptance. Existing
`auto` session policy remains fresh. A separate opt-in receiving transport
should implement actual continuation before it advertises `resume=True`.

Persist a role-session registry keyed by the actual worker ID, binding:

- Parent task and native worker canonical task path
- Argus role and mission ID (or explicit isolated probe scope)
- Last admitted request, continuation ordinal, and terminal observation
- Current active request and durable spawn/followup intent, if any
- Coordinator ownership generation; changing hosts must not change the worker

Admission of a continuation must validate the exact enrolled request digest,
role, mission, actual worker identity, and prior terminal outcome. Only one
request may own a role session at a time, even across separate coordinator
batches. The initial worker must be genuinely completed/idle and available for
followup. Do not route a resumed request to a new worker under the old ID.

Before calling `collaboration.followup_task`, persist the continuation intent
and ordinal. Call the actual existing canonical path, supply the unchanged
Argus prompt plus request identity for correlation, and record the tool's real
observation. Native results must be associated with that specific new turn;
status `completed` alone is ambiguous because the prior turn was completed too.
A lost followup response must hold the session for reconciliation, not trigger
another followup. Cancellation stops the current native turn, never silently
replaces it. A failed or unavailable worker makes explicit resume fail closed.

The current coordinator prohibits reusing worker IDs across requests and rejects
resume. Preserve that v1 behavior. Implement the new mode as a separately
selected host protocol/transport with an explicit persisted version; do not
retroactively reinterpret already-enrolled v1 sessions.

### Finite authorization for dependent turns

An arbitrary new file in a queue must not become executable. For initial tests,
enroll each exact continuation request after its real predecessor ID is known,
under the parent's already-authorized finite probe script. Longer Argus
workflows need an explicit bounded producer contract: named role/mission steps,
allowed predecessor relationship and operation (`spawn` or `followup`), maximum
turns, deadline, and owner. The authorization must come from the host/user task,
not the request prompt. Private queue location alone is not provenance.

Do not enable unlimited enrollment, wildcard prompts, indefinite role workers,
or automatic continuation of unrelated sessions in order to avoid parent work.
The coordinator can manage the bounded protocol once; the actual authorization
scope still belongs to the user/parent.

### Acceptance criteria

1. Real native worker handles two authorized calls via spawn then followup
2. The same actual worker ID and canonical task path are returned both times
3. Cross-role, cross-mission, concurrent, stale and unknown-worker resumes fail
4. Crash before/after followup never duplicates execution; ambiguity remains held
5. Cancellation of the second turn is acknowledged only after native stop proof
6. Existing v1 tests and ordinary Argus provider/session tests remain unchanged

## 2. Actual typed role-tool calls and result feedback

`DotsBackend` already checks accepted/bound role context, dispatches explicit
`tool_call` events through the existing callback, and calls
`transport.tool_result(request_id, sequence, reply)`. The missing file-host
transport must supply that request/reply channel; native final prose must
remain only prose, including JSON-like text and the word `approve_review`.

A viable explicit host operation is a request-scoped CLI command invoked as an
actual native execution tool action:

`request-tool SESSION REQUEST CALL_ID TOOL_NAME --arguments-json ...`

This is a design name, not an implemented command. It should validate the
session generation, exact request, actual bound worker identity, reviewer role,
active call context, advertised tool name/schema and size bounds. Persist a
unique call ID and canonical arguments before publishing the typed `tool_call`
event. The backend remains responsible for invoking the original
`ReviewActions.dispatch`; the coordinator never implements a parallel approval
state machine or upgrades a reviewer's final message into a tool call.

The producer writes a private request/sequence-bound `tool-result` record.
The worker's command reads that same reply, or the coordinator delivers it to
the same worker when the native turn model requires a continuation. Preserve
structured schema/busy/error feedback; a rejected argument is not an approval.
No bearer credential, internal API, Python callable, or callback port needs to
be copied into the native worker prompt.

Tool-call lifecycle must be distinct from model-turn completion:

- No typed call before acceptance, after cancellation, or after role context exit
- No finish while a required tool result is pending or outcome uncertain
- Exact duplicate call IDs return only their saved result; different arguments
  under the same ID fail closed
- A crash after dispatch intent but before result persistence is uncertain,
  especially for validation commands with side effects; never blindly dispatch
  again to manufacture exactly-once behavior
- Cancellation suppresses late tool dispatch and late approval, and reports
  any in-flight tool whose stop/outcome is not established

The journal still cannot independently authenticate native identities against
same-OS-user malicious code. Its role/tool claims must remain honest trusted
host assertions, not a security boundary. This feature may advertise only
`role_tools=True` once real typed calls and feedback are verified. It does not
supply sandbox, tools-disabled, tool-whitelist or read-only capabilities.

### Acceptance criteria

1. A real native tool invocation reaches the existing dispatcher and receives
   its actual structured response during the same request
2. A bad argument receives schema failure; an allowed corrected call works
3. A final prose/JSON approval never creates an action or decision
4. Wrong worker/role/call, unknown tool and late calls all fail closed
5. Lost replies and duplicate IDs do not repeat an uncertain dispatcher action
6. A role-tagged probe is clearly distinguished from a production Reviewer run

## 3. Reviewer boundary: a remaining platform dependency

The existing Reviewer requests `sandbox_mode="read-only"` in
`argus/reviewer/_core.py`, in addition to call-bound review tools. Current native
subagents inherit broad platform tools. Restricting their prompt, placing their
input in a snapshot, or giving them only a broker URL in the prompt does not
actually remove other tools or filesystem access. Running one command inside a
sandbox also does not constrain that native agent's other commands.

Therefore neither role continuation nor typed role tools enables the full
Reviewer. Keep `sandbox_mode:read-only` unadvertised and fail closed. Do not
remove the existing capability request merely to make the workflow proceed.

Production choices require a genuine boundary:

- A supported native execution mode with enforceable filesystem/tool controls
- An already-supported configured Argus CLI backend with real read-only
  enforcement for Reviewer while other appropriate roles use dots
- A separately supported model/tool execution service whose entire execution
  surface is controlled by the read-only broker, not just one optional tool

The second route is an explicit mixed backend architecture, not silent fallback.
It requires a configured/authorized provider and accurate declaration of which
roles ran where. Do not discover or copy hidden credentials to supply it.

## 4. Small, independent follow-on improvements

A bounded producer-side cancellation-acknowledgement wait can inspect an actual
cancelled terminal event before returning. This reduces false uncertainty when
the live host stops promptly, but must time out honestly when the host is absent.
Keep cancellation observation separate from late result admission.

A host recovery view can include a validated pending final-result journal so a
replacement coordinator can re-publish the exact saved observation without a
new native turn. This improves recovery without granting role-session resume.

Provider usage and quota controls remain separate. Native identity and elapsed
coordination time are not evidence of token usage or model billing.

## Suggested implementation order

1. Complete the current real finite-coordinator success and cancellation probes
2. Add validated pending-result recovery and bounded cancellation observation
3. Add versioned role-session continuation, with one real two-turn native probe
4. Add the typed tool channel and test a request-scoped action/feedback probe
5. Run the full five-role workflow only after each role's actual capabilities
   are supplied; disclose any configured mixed-provider route

Each stage should preserve v1 behavior, have positive and negative tests, and
pass an independent real native probe before capability flags are enabled.
