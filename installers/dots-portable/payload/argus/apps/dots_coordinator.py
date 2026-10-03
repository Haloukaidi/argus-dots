"""Finite authorized native-host handoffs; never a daemon or agent API."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..adapters.dots_backend import MAX_PAYLOAD_BYTES, DotsBridgeError
from ..adapters.dots_coordinator import DotsCoordinator
from ..adapters.dots_file_transport import FileDotsTransport


def main(argv: list[str] | None = None, *, role_mode: bool = False, bounded_mode: bool = False,
         supervised_mode: bool = False) -> int:
    bounded_mode = bounded_mode or supervised_mode
    role_mode = role_mode or bounded_mode
    parser = argparse.ArgumentParser(description=(
        "Journal an explicitly authorized finite native-coordinator task list. "
        "The host, not this process, calls real native tools. No execution isolation is implied."))
    parser.add_argument("--bridge-dir", type=Path, required=True)
    if supervised_mode:
        parser.add_argument("--profile-file", type=Path, required=True,
                            help="explicit host-authored supervised profile JSON; never auto-discovered")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="parent enrolls exact requests or an explicit bounded producer scope")
    if bounded_mode:
        create.add_argument("--producer", required=True)
        create.add_argument("--project-root", required=True, type=Path)
        create.add_argument("--mission", required=True)
        create.add_argument("--roles", nargs="+", required=True)
        create.add_argument("--max-requests", type=int, required=True)
        close = commands.add_parser("close-producer", help="authorized producer seals admission; accepted work still drains")
        close.add_argument("session_id")
        close.add_argument("--producer", required=True)
        close.add_argument("--project-root", required=True)
    else:
        create.add_argument("request_ids", nargs="+")
    create.add_argument("--parent", required=True)
    create.add_argument("--coordinator", required=True)
    create.add_argument("--max-concurrency", type=int, default=1)
    create.add_argument("--lifetime", type=float, default=600)
    status = commands.add_parser("status", help="inspect without dispatch or takeover")
    status.add_argument("session_id")
    handoff = commands.add_parser("handoff", help="parent replaces a stopped coordinator; never redispatches")
    handoff.add_argument("session_id")
    handoff.add_argument("--parent", required=True)
    handoff.add_argument("--previous-generation", type=int, required=True)
    handoff.add_argument("--coordinator", required=True)
    names = ("next", "bind", "record", "stop", "abandon", "heartbeat") if supervised_mode else (
        "next", "bind", "record", "stop", "abandon")
    for name in names:
        command = commands.add_parser(name)
        command.add_argument("session_id")
        command.add_argument("--owner", required=True, help="actual native coordinator task_name")
        command.add_argument("--generation", type=int, required=True)
        if name == "heartbeat":
            command.add_argument("--lease-seconds", type=int,
                                 help="explicit override; omitted preserves the selected supervised lease duration")
        if name in {"bind", "record", "abandon"}:
            command.add_argument("request_id")
        if name in {"bind", "record"}:
            command.add_argument("--worker-id", required=True, help="actual ID returned/observed by native tools")
        if name == "bind":
            command.add_argument("--worker-task", required=True, help="actual canonical child task_name")
        if name == "record":
            command.add_argument("kind", choices=("completed", "failed", "cancelled"))
            command.add_argument("--worker-status", required=True, choices=("completed", "failed", "interrupted", "idle"))
            command.add_argument("--text", help="actual result; omit to read stdin")
        if name == "abandon":
            command.add_argument("--spawn-status", choices=("not_created",), required=True)
            command.add_argument("--text", help="actual native tool failure that confirms no worker was created; omit for stdin")
    if role_mode:
        commands.choices["record"].add_argument("--turn-request-id", required=True)
        tool = commands.add_parser("request-tool", help="explicit native tool action or retry/read its same saved reply")
        tool.add_argument("session_id")
        tool.add_argument("request_id")
        tool.add_argument("call_id")
        tool.add_argument("name")
        tool.add_argument("--owner", required=True)
        tool.add_argument("--generation", type=int, required=True)
        tool.add_argument("--worker-id", required=True)
        tool.add_argument("--arguments-json", help="omit to read one JSON argument object from stdin")
    args = parser.parse_args(argv)
    try:
        if supervised_mode:
            import os

            from ..adapters.dots_supervised import (
                SupervisedDotsRoleHost,
                SupervisedRoleFileDotsTransport,
            )
            from ..core.dots_profile import SupervisedDotsProfile

            if not args.profile_file.is_absolute():
                raise DotsBridgeError("profile-file must be an explicit absolute path")
            descriptor = os.open(args.profile_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                import stat
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise DotsBridgeError("profile-file must be a regular file")
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    data = stream.read(MAX_PAYLOAD_BYTES + 1)
                if len(data) > MAX_PAYLOAD_BYTES:
                    raise DotsBridgeError("profile-file exceeds the bounded read size")
                profile = SupervisedDotsProfile.from_dict(json.loads(data))
            finally:
                os.close(descriptor)
            host = SupervisedDotsRoleHost(SupervisedRoleFileDotsTransport(args.bridge_dir, profile=profile))
        elif bounded_mode:
            from ..adapters.dots_admission import BoundedDotsRoleHost, BoundedRoleFileDotsTransport

            host = BoundedDotsRoleHost(BoundedRoleFileDotsTransport(args.bridge_dir))
        elif role_mode:
            from ..adapters.dots_role_host import DotsRoleHost, RoleFileDotsTransport

            host = DotsRoleHost(RoleFileDotsTransport(args.bridge_dir))
        else:
            host = DotsCoordinator(FileDotsTransport(args.bridge_dir))
        if args.command == "create":
            kwargs = {"parent_task": args.parent, "coordinator_task": args.coordinator,
                      "max_concurrency": args.max_concurrency, "lifetime_seconds": args.lifetime}
            if bounded_mode:
                result = host.create(producer_id=args.producer, project_root=args.project_root,
                                     mission_id=args.mission, allowed_roles=args.roles,
                                     max_requests=args.max_requests, **kwargs)
            else:
                result = host.create(args.request_ids, **kwargs)
        elif args.command == "close-producer":
            result = host.close_producer(args.session_id, producer_id=args.producer,
                                         project_root=args.project_root)
        elif args.command == "status":
            result = host.status(args.session_id)
        elif args.command == "handoff":
            result = host.handoff(args.session_id, parent_task=args.parent,
                                  previous_generation=args.previous_generation, coordinator_task=args.coordinator)
        else:
            kwargs = {"coordinator_task": args.owner, "generation": args.generation}
            if args.command == "heartbeat":
                kwargs["lease_seconds"] = args.lease_seconds
            if args.command == "request-tool":
                raw = args.arguments_json if args.arguments_json is not None else sys.stdin.read(MAX_PAYLOAD_BYTES + 1)
                if len(raw.encode()) > MAX_PAYLOAD_BYTES:
                    raise DotsBridgeError("tool arguments exceed the 1 MiB safety limit")
                arguments = json.loads(raw)
                result = host.request_tool(args.session_id, args.request_id, call_id=args.call_id,
                                           name=args.name, arguments=arguments, worker_id=args.worker_id, **kwargs)
            elif args.command == "bind":
                result = host.bind(args.session_id, args.request_id, worker_id=args.worker_id,
                                   worker_task=args.worker_task, **kwargs)
            elif args.command in {"record", "abandon"}:
                text = args.text if args.text is not None else sys.stdin.read(MAX_PAYLOAD_BYTES + 1)
                if args.command == "record":
                    if role_mode:
                        kwargs["turn_request_id"] = args.turn_request_id
                    result = host.record(args.session_id, args.request_id, worker_id=args.worker_id,
                                         worker_status=args.worker_status, kind=args.kind, text=text, **kwargs)
                else:
                    result = host.abandon(args.session_id, args.request_id, spawn_status=args.spawn_status, text=text, **kwargs)
            else:
                result = getattr(host, args.command)(args.session_id, **kwargs)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (DotsBridgeError, OSError, ValueError) as exc:
        print("dots coordinator blocked: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
