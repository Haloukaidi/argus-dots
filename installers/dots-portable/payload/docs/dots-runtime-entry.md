# Explicit dots runtime selection

`dots` is now a runtime selection, separate from the nine agent-CLI backends.
The default remains unchanged. Dots runtime dispatch/readiness never resolves a
CLI executable, imports credentials, silently selects another provider, or grants
capabilities. Explicit dots setup reports native-host unavailability before the
ordinary CLI installation/login flow.

- `--backend dots`, the shared backend knob, and role displays retain the dots
  identity. A pipeline mixing dots and CLI role overrides fails explicitly.
- The common `build_life_runner` factory routes an explicit dots pipeline to
  the existing five-role `build_dots_life_runner` factory when the caller
  supplies `args.dots_transport`, a host-owned in-process dependency. It is not
  loaded from arbitrary project configuration and does not cross a daemon
  subprocess boundary by pretending that a Python object is persistent.
- Ordinary Web/CLI processes currently have no supported native receiving
  service. Readiness therefore reports **unavailable**. It does not probe or
  log in to a CLI. Web message endpoints return HTTP 503 before transcript
  writes or Manager dispatch; the existing client error path preserves its
  draft. Daemon start refuses before spawning. Empty idle project creation is
  still available and does not create a research task.
- A supplied transport must declare the baseline controls requested by all five
  roles. This does not assert that every original CLI implements every request
  in exactly the same way. Existing per-call gates remain authoritative for
  conditional controls, model/effort choices, resume and typed role actions.
- Cached Web runners are revalidated against public backend/model/effort settings
  before reuse. Changing settings invalidates warm clients, prior thread IDs and
  cached plan previews; helper routes cannot reuse an older CLI after dots is
  selected.
- Model and reasoning-effort values are not removed or rewritten to make a
  transport accept them. In particular, the current v2 role host still rejects
  non-default model/effort and has no execution-control capabilities. Its resume
  and typed-action support does not make a full workflow ready.

No new public receiving API, native OS sandbox, native tool whitelist, hosted
credential, provider-accounting guarantee or unattended service is implemented
here. Bounded dynamic admission is a separate concern; it cannot manufacture
role execution guarantees. Readiness and fixture tests are not native acceptance
of a complete research workflow.

Targeted checks: `python -m pytest tests/test_dots_runtime_routing.py -q`.
The capable host in that file is a deterministic fixture, not a claim about the
current native platform. CLI constructors/resolvers are trapped in the routing
checks so a fallback fails the test immediately.
