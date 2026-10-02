"""Explicit v2 host CLI. v1 coordinator sessions are not upgraded or adopted."""
from __future__ import annotations

from .dots_coordinator import main as coordinator_main


def main(argv: list[str] | None = None) -> int:
    return coordinator_main(argv, role_mode=True)


if __name__ == "__main__":
    raise SystemExit(main())
