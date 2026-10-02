"""Explicit dummy-review action-channel probe, not a production Reviewer run."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..adapters.dots_backend import DotsBackend
from ..adapters.dots_role_host import RoleFileDotsTransport
from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec
from ..reviewer.tools import ReviewActions

PROBE_PROMPT = (
    "This is an explicitly authorized dummy-review tool-channel probe, not a production review. "
    "Use the host's actual request-tool CLI (not final prose) to call the bound approve_review action "
    "with review set to the empty string and read the schema-failure reply. Then use a NEW call ID "
    "to call approve_review with review set to 'Native action-channel probe completed', "
    "and read its actual success reply. Do not act on any real project or other data. "
    "Only after both real replies, return TOOL_OK using the host's current request correlation envelope. "
    "Wait for the host to provide actual request/session/worker identity before calling the CLI."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args(argv)
    transport = RoleFileDotsTransport(args.bridge_dir)
    backend = DotsBackend(transport, role="reviewer", timeout_seconds=args.timeout)
    actions = ReviewActions()
    observations = []

    def dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            reply = actions.dispatch(name, arguments)
        except ValueError as exc:
            observations.append({"name": name, "arguments": arguments, "schema_error": str(exc)})
            raise
        observations.append({"name": name, "arguments": arguments, "result": reply})
        return reply

    with backend.bind_role_tools(actions.tools, dispatch):
        result = run_exec(backend, prompt=PROBE_PROMPT, run_label="native-typed-dummy-review-probe",
                          options=RunnerOptions())
    print(json.dumps({"probe_only": True, "production_reviewer_isolation_verified": False,
                      "result": asdict(result), "decision": asdict(actions.decision) if actions.decision else None,
                      "dispatcher_observations": observations}, ensure_ascii=False))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
