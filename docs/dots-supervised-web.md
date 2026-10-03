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
its session/profile content invalidates cached role runners, thread IDs and
plan previews. Ordinary strict `dots` with no configured binding keeps its
original native-transport refusal and never falls back to Codex or another CLI.

An active request remains subject to bounded timeout, cancellation and
reconciliation rules. A renewal is not authority to replay an uncertain spawn
or continuation. Stop and terminal observations remain host-owned. After the
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
