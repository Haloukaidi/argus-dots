# Explicit supervised approximate native roles

`supervised-approx-v1` is a separate, opt-in execution profile for the `dots`
runtime. It keeps the original Argus Manager/Planner/Engineer/Reviewer/Curator
orchestrator, role prompts, gateway and typed `ReviewActions.dispatch`. It does
not call a provider CLI or replace the workflow with another scheduler.

The strict `dots` profile and protocol 1/2/3 remain unchanged. An approximate
transport alone does not opt a `DotsBackend` into approximate execution. Both
the backend and host-bound launcher must explicitly select the matching profile.
An arbitrary project file, prompt or backend name cannot enable it.

## What is approximate

- Native workers still share the host's tools and filesystem permissions.
  `sandbox_mode`, `disable_tools`, work/read directories, skill paths, search,
  `force_safe_mode` and role behavior are instructions, not OS enforcement
- `capabilities.options` stays empty. Typed reviewer actions and same-role
  native continuation retain their existing supported transport semantics
- `dangerous_yolo` and `full_auto` never broaden authority or become native
  flags. Legacy dangerous-yolo requests remain visible as ignored, without
  privilege expansion. Soft/stalled idle thresholds and callback presence are
  also recorded as unsupported behavior; no callback or executable object is
  forwarded. The original hard idle/deadline/cancellation checks still run
- Strong `isolate_workdir`, native `output_schema`, arbitrary CLI arguments,
  extensions, credentials and unknown options remain unsupported and fail
  before publication. This profile is not a blanket override of missing controls
- The host must already have authority for the task and any consequential
  action. No workflow flag, queue field, model output or profile grants it

Start with a fresh scoped project copy containing only the inputs needed for
the trial, without secrets. Inspect its resulting changes before promoting
anything to a source checkout. Copies, staged commits, hashes and snapshots
reduce accidents or detect changes; they cannot prevent a native worker from
using its actual broader permissions. Shared-user queue identity is not
cryptographic authentication and is not resistant to a malicious native worker.

## Exact model negotiation and declared effort approximation

The host supplies a catalog of exact currently supported native tool arguments.
No model IDs are aliased or silently replaced. The empty model key describes
supported effort arguments when the model is inherited; it does not identify
that underlying model. Requested model/effort, actual dispatch arguments and
unknown observed model/effort are separate fields.

```python
from argus.core.dots_profile import SupervisedDotsProfile

profile = SupervisedDotsProfile(
    model_efforts=(("", ("xhigh",)),),
    # Optional explicit approximation when this host requires xhigh:
    effort_overrides=(("low", "xhigh"), ("high", "xhigh")),
    default_effort_override="xhigh",  # Explicitly map requested effort=None too.
    read_roots=(),
    report_roots=(),
)
```

The catalog above is an example declaration, not a platform-support probe.
Verify the actual native tool catalog and higher-priority host instructions
first. With no override mapping, effort must be honored exactly or rejected.
With the explicit mapping, a low/high request is retained unchanged in the
request journal, while dispatch says `xhigh` and resolution says
`explicit-profile-override`. It must never be reported as an actual low/high
execution. An unsupported model or an effort with neither exact support nor an
explicit mapping is rejected. Native followup may only retain the existing
worker's actual dispatch configuration; it cannot change model/effort.
`default_effort_override` is off by default. If explicitly selected, a request
whose original effort is `None` retains that fact while its dispatch arguments
contain the declared target. This is also reported as an explicit profile
override, never as evidence that the platform's unknown default was xhigh.

## Finite host lifecycle and producer binding

Protocol 4 reuses bounded admission with one immutable producer, canonical
project, workflow identity, allowed roles, total-call limit, concurrency limit
and expiration. The session's `mission_id` is the authorized workflow identity;
each request keeps its original nullable/upstream-created `mission_id`.
Same-worker continuation still requires the same original role and mission.

External read/skill paths must be explicitly declared in `read_roots`; external
report/receipt paths must be declared in `report_roots`. Defaults allow only the
bound project. Roots are validated live before new admission. Missing roots do
not invalidate old journals or prevent cancellation and exact receipt recovery.

```python
from argus.adapters.dots_supervised import (
    SupervisedDotsRoleHost, SupervisedRoleFileDotsTransport,
)

host_transport = SupervisedRoleFileDotsTransport(QUEUE, profile=profile)
host = SupervisedDotsRoleHost(host_transport)
session = host.create(
    parent_task=ACTUAL_PARENT, coordinator_task=ACTUAL_COORDINATOR,
    producer_id=APPROVED_PRODUCER, project_root=PROJECT_COPY,
    mission_id=APPROVED_WORKFLOW, allowed_roles=[
        "manager", "planner", "engineer", "reviewer", "curator",
    ], max_requests=32, max_concurrency=1, lifetime_seconds=1800,
)
# Actual active coordinator only; object construction/create never asserts this.
host.heartbeat(session["session_id"], coordinator_task=ACTUAL_COORDINATOR, generation=1)
transport = SupervisedRoleFileDotsTransport(
    QUEUE, profile=profile, session_id=session["session_id"],
    producer_id=APPROVED_PRODUCER, project_root=PROJECT_COPY,
)
```

The live coordinator renews a short lease through `heartbeat` or `next`.
Readiness requires a fresh assertion, matching scope/profile and available
budget. It reports recent host participation, not proof a worker is executing.
An absent/stale host is `host-required`. Neither Python nor Web can call native
tools, wake a host, grant access, automatically restart it or extend its finite
authorization. A lease timeout does not fabricate a stopped-worker observation.
Existing result/cancellation settlement retains the original bounded semantics.

Exact enrolled retries retain their original digest/owner receipt after closure,
lease expiry, final-call exhaustion or launcher-config replacement. They never
reopen admission, dispatch another worker or accept changed request bytes.

## Native coordinator commands

Serialize `profile.to_dict()` to a host-authored profile file, then explicitly use
the protocol-4 entry point. It supports the original bounded `create`, `next`,
`bind`, `record`, `request-tool`, `status`, `stop`, `handoff`, `abandon` and
`close-producer` commands plus `heartbeat`:

```sh
python -m argus.apps.dots_supervised --profile-file /absolute/profile.json \
  --bridge-dir /absolute/queue heartbeat SESSION \
  --owner ACTUAL_COORDINATOR --generation 1
```

`next` returns the original request, exact native dispatch arguments, profile and
option dispositions, and `worker_prompt`. Preserve that prompt and the current
turn marker. Use real native spawn/followup, bind the actual returned worker,
and provide the real request-tool context after binding. The host must record
only the actual current result and actual terminal observation. Unknown spawn,
continuation or tool-dispatch outcomes require reconciliation, never a retry
that might execute twice. The CLI itself dispatches nothing.

## Reviewer evidence and typed report writes

The supervised Reviewer snapshots all explicitly bound candidate/evidence
roots before submission. Snapshots are bounded and fail closed on unreadable,
linked, escaping or over-budget input. There are no broad ignored directories.
The original `ReviewFileStore` owns the only report/receipt exception, exposed
as call-bound typed `read_review`/`write_review` tools. Actual verdicts still go
through the original `ReviewActions.dispatch`; prose and JSON-looking answers
never become approval. The original Docker requirement for isolated review
commands is unchanged; there is no unsandboxed subprocess fallback.

After the actual terminal response, the host revalidates candidate snapshots
and its in-memory report evidence before returning gateway success. A candidate
change or forged report/receipt fails even if a typed approve action was already
requested. This is post-action detection, not prevention or a tamper-proof
ledger. Unrelated host writes inside protected roots can conservatively fail a
review. Fix the declared inputs/layout rather than broadly excluding evidence.
In particular, a concurrent Manager supervision update during a repair round
can invalidate protected session evidence. This conservative rejection is a
known approximate-profile limitation; it is not permission to ignore telemetry
files, forge a review, or claim that the interrupted round was accepted.

## Verification claims

### Native accounting is unavailable

The native tool contract currently supplies no provider token/cost telemetry.
Missing usage flags stay false, the observed model remains unknown, and no
tokens or prices are invented. Native calls currently do not create per-call
`UsageLedger` records. The real supervisor therefore reports an empty accounting
ledger with `cost_usd: null`, not a measured zero bill. The current Web top bar
hides an absent spend badge and the result summary omits that null cost.

An empty ledger's record count is not a count of actual native role invocations.
Use the bounded host's real request/worker journal for those invocation facts.
Token/dollar accounting and budget-equivalence claims are unavailable for this
profile; its finite request, admission lifetime and existing cancellation
contracts remain separate. This limitation must accompany native acceptance
results until real provider telemetry is supported.

### Test scope

Deterministic tests cover explicit opt-in, unchanged strict behavior, original
request preservation, model/effort negotiation, scoped paths, short host leases,
same-role continuation, receipt recovery, typed approvals and post-terminal
evidence checks. These are not native acceptance or unattended-service evidence.
A separate supervised native trial must verify the actual Web sender, original
role sequence, current worker identities, typed review tool, consumed results
and original output artifact before claiming that path works.

The [2026-10-03 supervised native record](dots-supervised-native-validation.md)
now reports one bounded normal Web API software mission, plus separate original
Planner continuation and Curator callback probes. It preserves earlier failed
trials and distinguishes that evidence from hard isolation, browser-UI success,
background teammate operation and complete research delivery.
