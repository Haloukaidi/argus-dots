"""Launch the normal Web entry with one explicit bounded native-host binding.

This launcher neither creates a host session nor dispatches native tools. The
live authorized coordinator must already exist and renew its own lease.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..adapters.dots_host_binding import require_configured_dots_runtime
from ..core.dots_host_binding import HOST_CONFIG_ENV
from ..core.runtime_backend import RuntimeBackendUnavailable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-config", type=Path, required=True,
                        help="explicit non-secret supervised-approx-v1 binding JSON")
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8799)
    args = parser.parse_args(argv)
    config = args.host_config
    if not config.is_absolute():
        parser.error("--host-config must be absolute")
    if not 1 <= args.web_port <= 65535:
        parser.error("--web-port must be between 1 and 65535")
    updates = {HOST_CONFIG_ENV: str(config), "ARGUS_SKILL_RUNNER_BACKEND": "dots"}
    previous = {name: os.environ.get(name) for name in updates}
    os.environ.update(updates)
    try:
        try:
            binding = require_configured_dots_runtime("dots")
        except RuntimeBackendUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 3
        if binding is None:
            print("dots host-required: explicit supervised binding is missing", file=sys.stderr)
            return 3
        print("dots execution profile: " + binding.execution_profile.name
              + "; supervised approximate; live native coordinator required", file=sys.stderr)
        # The existing Web entry owns normal pairing and service startup.
        # Child daemons inherit only this bounded non-secret config locator.
        from .cli import main as argus_main

        return argus_main(["--web", "--web-host", args.web_host,
                           "--web-port", str(args.web_port)])
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())
