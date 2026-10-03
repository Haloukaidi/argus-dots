# Explicit supervised approximate Web binding

The runtime backend remains `dots`. Strict `dots` readiness and per-call
capability refusal are unchanged. A distinct, explicit execution profile,
`supervised-approx-v1`, can bind the normal Argus Web front door and its daemon
children to one bounded native-host session. This does not create a provider
CLI, substitute the upstream scheduler, or grant native sandbox capabilities.

## Host-owned setup

An authorized live native coordinator first creates a protocol-4 session with
`SupervisedDotsRoleHost`. The immutable session bounds include the project,
producer, workflow scope, roles, request limit, concurrency, lifetime and exact
execution profile. The original nullable Manager mission ID and later upstream
mission IDs remain call identities; the workflow scope does not rewrite them.

The host explicitly asserts its active session with `heartbeat(...)` and renews
that short lease while servicing the session. Creating a session or saving the
JSON file below does not attach or wake a coordinator. A current lease is a
trusted host assertion of availability, not cryptographic authentication, proof
of a running native worker, or protection against another process with the
same OS user. Actual native tool calls and observed results still belong to
the native coordinator.

Save a nonsensitive launcher configuration in an explicit absolute file path:

```json
{
  "version": 1,
  "bridge_dir": "/absolute/private/bridge",
  "session_id": "32-lowercase-hex-session-id",
  "producer_id": "authorized-web-producer",
  "project_root": "/absolute/authorized/project",
  "request_timeout_seconds": 300,
  "execution_profile": "replace with the exact profile.to_dict() object"
}
```

Use the exact `SupervisedDotsProfile.to_dict()` object used to create the
session. The strings above are descriptive placeholders, not a runnable
configuration. Unknown fields, duplicate keys, wrong versions, relative file
paths, final symlinks, nonregular files and files larger than 16 KiB are
rejected. Do not store tokens, passwords, native credentials, or importable
Python objects in this file. It is a locator and immutable scope snapshot, not
a credential or independent authorization.

`request_timeout_seconds` is optional and defaults to 300 for existing files.
It must be a finite JSON number greater than zero and at most 3600; booleans,
strings and null are rejected before transport construction or admission. The
host-owned value reaches all five role backends through both the prebound Web
Manager and detached-daemon factory. No project file, prompt or new CLI flag
selects this budget.

This is the requested total call budget, including preparation, queue waiting
and native dispatch waiting. Each new request records an immutable effective
`expires_at = min(call_started_at + request_timeout_seconds, session_expires_at)`.
The backend's `request_timeout_seconds` capability diagnostic retains the
requested budget; the request journal's `created_at` and `expires_at` show the
effective budget. For example, requesting 900 seconds with 400 seconds left in
the authorized session permits at most those remaining 400 seconds. Readiness
reports the immutable `session_expires_at` separately from the renewable host
lease's `expires_at`. Renewing that lease does not extend either the session or
a request. The separate hard-idle watchdog and stop/cancellation rules remain
unchanged and can end a call earlier.

### Explicit host lease duration

The protocol-4 execution profile may additionally declare
`lease_duration_seconds` as an integer from 1 to 90. This selects an immutable
renewal duration for that profile/session. It belongs inside the exact
`execution_profile` object, not at the top level of the launcher configuration.
For example, the host can create `SupervisedDotsProfile(...,
lease_duration_seconds=90)` and use its unchanged `to_dict()` output for both
session creation and launcher binding. A profile-only edit cannot change an
existing session's selection; it fails the binding match and invalidates warm
runners.

An omitted field keeps the legacy profile encoding, 30-second initial default
and explicitly requested `heartbeat(..., lease_seconds=1..60)` support. An
explicit profile duration governs every omitted-argument `heartbeat()` and
`next()` renewal; a conflicting explicit heartbeat override is rejected.
For legacy profiles, an explicit selection is remembered in the lease record
for the same owner/generation, including renewal after the previous lease
expires. Older lease records without that metadata renew at the conservative
30-second default. An authorized handoff also resets a legacy selection to 30
unless the new owner explicitly chooses another legacy duration; an explicit
profile selection survives handoff. The supervised CLI's omitted
`--lease-seconds` follows the same rules.

Every lease is capped at the original session expiry. Stopped or expired hosts
cannot renew; `next()` still permits cancellation/reconciliation without a
pulse, and status/result settlement remains available. The declaration does
not start a timer, prove native liveness, extend a request, or change strict
`dots`/protocol-3 behavior or role permissions.

## Normal Web entry

```sh
python -m argus.apps.dots_web \
  --host-config /absolute/path/to/host-binding.json \
  --web-host 127.0.0.1 --web-port 8799
```

The thin launcher performs readiness, explicitly selects backend `dots`, then
calls the existing `argus --web` entry. Existing pairing, Web routing,
Manager/Planner/Engineer/Reviewer/Curator orchestration, ReviewActions and
upstream scheduling remain in place. It sets `ARGUS_DOTS_HOST_CONFIG` only in
the launch environment; daemon children inherit the same bounded locator. It
does not persist global backend settings, replace model choices, create native
agents, or claim that a detached Python process can use conversation tools.

The project selected in Web must have the exact configured execution workdir.
Existing project/session metadata remains authoritative. An objective supplied
while creating a project requires that explicit workdir; an automatically
created new workspace is not silently enrolled. Empty idle project creation
remains available, but work outside the binding will be rejected.

Readiness and role labels display `supervised-approx-v1` and its approximate
status. The profile describes accepted advisory limitations separately from
real capabilities; there is no enforced OS read-only, model-only, or native
tool-whitelist claim. Model and effort overrides must be in the host's exact
supported catalog; unsupported choices fail rather than being dropped.

## Losing or changing the host

Missing, stale, expired, closed, wrong-project, wrong-profile or mismatched
sessions report `host-required` before new Web transcripts/tasks or daemon
launch. Every reused Manager runner is revalidated. Changing the locator or
its session/profile/timeout content invalidates cached role runners, thread IDs and
plan previews. Ordinary strict `dots` with no configured binding keeps its
original native-transport refusal and never falls back to Codex or another CLI.

An active request remains subject to bounded timeout, cancellation and
reconciliation rules. A renewal is not authority to replay an uncertain spawn
or continuation. A timeout-only configuration edit applies to newly constructed
runners; it never extends an existing request or prevents its exact receipt
recovery and cancellation/result settlement. Stop and terminal observations remain host-owned. After the
host disappears, restore the authorized live coordinator and its current
session lease before retrying an unsubmitted Web request. Do not assume an
already submitted native turn can be safely repeated.

## Validation boundary

Deterministic fixture tests verify composition, scope/readiness refusals,
cache invalidation, strict-path compatibility and normal Web/daemon routing.
They do not prove native execution, model selection, isolation or a completed
research mission. Native acceptance requires observed role workers and the
existing Reviewer decision path, reported separately from fixture results.

A separately recorded [native acceptance](dots-supervised-native-validation.md)
passed the normal Web API and detached-daemon software path. Browser navigation,
unattended operation and a complete research mission remain separate claims.
For external baseline evidence, use the existing host-owned
`ARGUS_SKILL_REVIEWER_READ_DIRS` JSON array as well as authorized profile roots;
read authorization alone does not add a location to the Reviewer snapshot.
