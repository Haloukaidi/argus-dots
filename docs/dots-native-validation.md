# Native dots validation evidence

The native observations below are historical acceptance records, not a claim
that the complete native five-role workflow is now supported. The subsequent
expanded source regression and fault tests are documented separately in the
[workflow report](workflow-validation-2026-10-02.zh-CN.md).

Protocol-4 supervised observations have their own
[2026-10-03 record](dots-supervised-native-validation.md); they do not retroactively
change the outcomes or scope of the historical probes below.

## Finite coordinator acceptance, 2026-10-02

A separate real native coordinator handled an explicitly authorized three-task
batch. The parent enrolled the batch and later requested stop; the coordinator
performed the actual child spawning, binding, result handling and interruption.
This summary omits internal worker paths and task identities.

- Arithmetic probe: actual native answer `391`, gateway exit 0
- Text probe: actual native answer `SUGRA`, but session stop won the publication
  race; late success was rejected and the gateway returned exit 130
- Cancellation probe: actual native interrupt reported previous status running;
  a subsequent native status observation confirmed interrupted; gateway exit 130
- All three requests reached terminal events and were consumed

The cancellation probe encountered an unavailable clock wait tool; it therefore
does not prove interruption of a long timer. It does prove interruption of a
real active native worker, with no OS background process created. Native status
and observation were used, not mock worker IDs or Python-generated output.

This acceptance covers the finite v1 coordinator, actual worker provenance,
late-result rejection and active native turn cancellation. It does not prove
read-only enforcement, full five-role production execution, role continuation,
or typed action feedback; those require their own acceptance runs.

The source v1 increment passed 109 focused dots tests and a related regression
selection of 470 tests, with 16 explicitly gated local Docker tests skipped.
An independent isolated review also passed 19 concurrency/recovery tests. These
are protocol/regression evidence distinct from the native results above.

## Same-role native continuation, 2026-10-02

A separate real v2 coordinator performed exactly one native spawn and one
followup to that same worker. In the first turn the worker was asked to retain
a short phrase and returned `READY`. The second turn did not repeat the phrase
and the worker returned `copper maple`.

Both producer calls returned exit 0, the same actual thread identity and no
fatal error. Both requests were completed and consumed. Each real native result
used its current request correlation envelope, which was stripped from the
original Argus payload. The parent enrolled the two requests; the coordinator
performed native spawn/followup, binding and result forwarding.

This verifies one real same-role continuation with retained context. It does
not establish unlimited session durability or isolated Reviewer execution.

## Final combined regression scope, 2026-10-02

The original 102-file regression selection was rerun with all three added test
files (coordinator, cancellation acknowledgement and v2 role host), for 105
files total. Final frozen code passed 1742 tests; 16 explicitly gated local
Docker probes were skipped. Runtime was 60.44 seconds. The 196 focused dots
tests are included in this total, not an additional disjoint count.

An execution-environment reset interrupted earlier attempts. Those incomplete
attempts are not counted as passed or failed tests. The final run completed
with exit code 0 after the environment recovered. Ruff, compilation and diff
whitespace checks passed separately. The related 557-test selection was an
earlier narrower check and is superseded for scope by this combined run.

## Native typed action/reply acceptance, 2026-10-02

A third, fresh, separately enrolled probe completed through a real native
coordinator and worker. The worker itself invoked the request-tool CLI using
its actual execution tool. The producer result was independently inspected.
No internal worker identities or raw runtime logs are included here.

- The empty `review` argument reached original `ReviewActions.dispatch` and
  returned the real schema error `'' should be non-empty`
- A new call ID with `review` set to `Native action-channel probe completed`
  returned the actual reply `{"recorded": "approve_review"}`
- Dispatcher observations contain exactly those two calls
- The dummy review decision was `done`; the native final response used its
  current request envelope and the original Argus payload was `TOOL_OK`
- The producer exited 0 with no fatal error and `tool_activity_observed=true`
- The result explicitly states `probe_only=true` and
  `production_reviewer_isolation_verified=false`
- All token/usage presence flags remained false; no usage or billing was inferred

The preceding two attempts were not counted as complete acceptance. The first
producer's execution session became unrecoverable after an executor-key change;
the last persisted evidence contained a rejected bad-argument call and an
undispatched good-argument call. The worker/request were safely stopped without
replaying that uncertain attempt. The second execution session could not be
recovered; no native child/tool action was admitted and its request was stopped
or abandoned. A `ps` listing in another execution command's PID namespace cannot
prove either producer was dead; process absence was not used as terminal proof.
The successful third attempt used a new queue, session, request and worker.

This proves one real typed action/reply roundtrip with validation feedback and
original dispatcher authority. It remains an explicitly authorized dummy-review
channel probe, not proof of a production read-only Reviewer or a complete
five-role production workflow.
