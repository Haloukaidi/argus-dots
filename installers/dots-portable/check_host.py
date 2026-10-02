#!/usr/bin/env python3
"""Fail-closed receiving-Dot tool INVENTORY check; never creates native capability.

The receiving Dot must supply its current actual tool names, not copy an example.
Passing this check is not execution evidence. An authorized real finite probe is
still required on every new host. Native tools cannot be discovered by a local
Python process; no public native-agent API is invented here.
"""
import argparse
import json
from pathlib import Path
import sys

REQUIRED = {"collaboration.spawn_agent", "collaboration.send_message", "collaboration.followup_task", "collaboration.list_agents", "collaboration.interrupt_agent"}
SHELL = {"exec_command", "functions.exec_command", "tools.exec_command"}


def check(names):
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("Expected a JSON array of actual current tool names")
    names = set(names)
    missing = sorted(REQUIRED - names)
    if not names.intersection(SHELL):
        missing.append("an actual exec_command shell tool")
    return {"inventory_check": "failed" if missing else "passed", "missing": missing, "native_execution_verified": False, "production_reviewer_isolation_verified": False, "next_step": "stop_and_report_missing_tools" if missing else "run_one_explicitly_authorized_finite_native_probe"}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tools-file", required=True, type=Path, help="JSON array written from the receiving Dot's actual current tool inventory")
    a = p.parse_args(argv)
    try:
        result = check(json.loads(a.tools_file.read_text(encoding="utf-8")))
        print(json.dumps(result, sort_keys=True))
        return 2 if result["missing"] else 0
    except (OSError, ValueError) as e:
        print("host preflight: " + str(e), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
