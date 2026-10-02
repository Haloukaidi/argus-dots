"""Explicit opt-in CLI: ``python -m argus.apps.dots_bridge --help``."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ..adapters.dots_backend import DOTS_ROLES, MAX_PAYLOAD_BYTES, DotsBackend, DotsBridgeError
from ..adapters.dots_file_transport import FileDotsTransport
from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(
        "Experimental supervised dot text-task bridge. No automatic dispatcher or public dot API. "
        "Existing Argus CLI backends/defaults are unchanged."))
    parser.add_argument("--host-protocol", choices=("v1", "v2"), default="v1", help="explicit v2 enables role continuation/tool transport only, never sandbox controls")
    parser.add_argument("--bridge-dir", type=Path, required=True, help="private local queue directory (POSIX only)")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="publish a text task and wait for an authorized online host")
    run.add_argument("--prompt", help="task text; omit to read stdin (avoids shell history)")
    run.add_argument("--role", choices=sorted(DOTS_ROLES), default="manager")
    run.add_argument("--label", default="dots-text-task")
    run.add_argument("--resume-worker", help="v2 only: actual previously bound same-role worker ID")
    run.add_argument("--model")
    run.add_argument("--reasoning-effort")
    run.add_argument("--timeout", type=float, default=300)
    commands.add_parser("capabilities", help="report the host guarantees for all five role bindings")
    inspect = commands.add_parser("inspect", help="read one request without approving or executing it")
    inspect.add_argument("request_id")
    emit = commands.add_parser("emit", help="record a real, separately authorized host event")
    emit.add_argument("request_id")
    emit.add_argument("type", choices=["accepted", "message", "completed", "failed", "cancelled"])
    emit.add_argument("--worker-id", help="actual host worker identity, only for accepted")
    emit.add_argument("--text", help="event text; message/failed/cancelled read stdin when omitted")
    args = parser.parse_args(argv)
    try:
        if args.host_protocol == "v2":
            from ..adapters.dots_role_host import RoleFileDotsTransport

            transport = RoleFileDotsTransport(args.bridge_dir)
        else:
            transport = FileDotsTransport(args.bridge_dir)
        if args.command == "capabilities":
            payload = {role: DotsBackend(transport, role=role).capability_report() for role in sorted(DOTS_ROLES)}
        elif args.command == "inspect":
            payload = transport.inspect(args.request_id)
        elif args.command == "emit":
            text = args.text
            if text is None and args.type in {"message", "failed", "cancelled"}:
                text = sys.stdin.read(MAX_PAYLOAD_BYTES + 1)
            payload = transport.emit(args.request_id, args.type, text=text, worker_id=args.worker_id)
        else:
            prompt = args.prompt if args.prompt is not None else sys.stdin.read(MAX_PAYLOAD_BYTES + 1)
            result = run_exec(DotsBackend(transport, role=args.role, timeout_seconds=args.timeout), prompt=prompt,
                              run_label=args.label, resume_thread_id=args.resume_worker, options=RunnerOptions(model=args.model,
                                                                        reasoning_effort=args.reasoning_effort))
            print(json.dumps(asdict(result), ensure_ascii=False))
            return result.exit_code
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except (DotsBridgeError, OSError, ValueError) as exc:
        print("dots bridge unavailable: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
