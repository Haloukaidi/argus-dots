# Bounded producer admission validation, 2026-10-02

## Result

One real two-turn bounded native transport acceptance passed on implementation
commit `ded6dec6deac5b820053a02103ccc0a7f98f754a`. The parent configured the scope
once. A dedicated live native coordinator received both requests through the
same protocol-3 session, with no parent forwarding of the second request ID,
prompt or output.

This is execution-transport and same-role continuation evidence. It is not a
complete production Manager or five-role research workflow.

## Actual native observations

- One dedicated native coordinator and one native role worker were used
- Exactly one real native spawn and one real followup used the same
  platform-returned worker identity
- The first manager-tagged text probe asked the worker to retain a phrase and
  returned `READY`
- Only after the original Argus gateway consumed the first result did the
  producer create the second request; the bound transport admitted it without
  another parent enrollment
- The second prompt did not repeat the phrase; the same worker returned
  `silver orchard`
- Both current native finals carried their correct request markers. The host
  verified and stripped those markers before publishing the original payloads
- Both original `run_gateway` calls returned exit code 0 and their requests
  were completed and consumed
- The producer closed admission, the host reported `done` with two terminal
  requests, and no role-tool calls or pending results remained
- An additional raw, unlisted request in the same queue was untouched: no
  admission reservation, native claim, events or closed record
- The producer process, coordinator and role worker all finished. No native
  task was restarted or replaced, and no second trial was used to obtain this
  result

The coordinator recorded each actual current final before doing progress
reporting or evidence analysis. Native worker identifiers and raw runtime
evidence are intentionally omitted from this public summary.

## Fixed bounds and timing

The scope was declared before execution: one producer/project/mission, only
the manager role, maximum two calls, one active worker, a 900-second session
and 300 seconds per gateway call. These bounds were not extended during the
trial.

Gateway durations were approximately 84.850 seconds and 59.831 seconds. They
include host coordination and handoff; they are not model inference timings.
No independent native-final arrival timestamps were available, so this trial
does not certify a final-to-record latency target. All usage-presence flags
remained false; token usage and cost are unknown.

## Deterministic regression and independent audit

Before the native trial, 80 new admission tests passed. They are included in
the 306-test dots and workflow regression selection, which also passed and was
independently rerun. An independent 43-test architecture invariant selection
passed separately. Ruff, Python compilation and diff whitespace checks passed.

Independent audit exposed and the implementation fixed three scope gaps before
native execution:

1. Different sessions could adopt the same unclaimed request. A durable owner
   reservation now precedes publication and is rechecked during dispatch
2. A bound producer could invoke inherited request operations on another
   session. Reads, cancellation, consumption and typed-tool operations now
   verify ownership; cleanup of the producer's own reserved orphan remains
   possible
3. A legacy host could enroll a protocol-3-reserved request. Legacy enrollment
   and dispatch, and raw event emission, now reject those reservations

Collision races, uncertain reservation/publication/enrollment outcomes,
cross-session operations, legacy-protocol rejection, unchanged execution
capability gates, cancellation, resume and generation handoff have deterministic
tests. Fixture success does not replace actual native evidence.

## Remaining boundaries

- The trial requested simple text output with default execution options. A
  prompt asking for no tools is not an enforced tools-disabled capability
- Native execution options remain empty. Original read-only, tools-disabled,
  isolation and other required controls still fail closed; they were not
  removed or advertised as supported
- Real native cancellation races, crash recovery and longer/multi-role
  operation were not exercised by this two-turn trial
- No formal research, complete production role workflow or UI-to-production
  provider acceptance was performed
- The scope and journals are trusted-host bookkeeping, not authentication
  against malicious same-OS-user processes
- A live native coordinator is still required. Persistent journals do not
  supply a public receiving API, permanent watcher, automatic native wakeup or
  restart after the coordinator ends

See [operation and recovery](dots-admission.md) for the explicit opt-in API and
its relationship to the unchanged upstream Argus workflow.
