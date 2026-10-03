"""Explicit protocol-4 host journal commands; this process never calls native tools."""
from __future__ import annotations

from .dots_coordinator import main as coordinator_main


def main(argv: list[str] | None = None) -> int:
    return coordinator_main(argv, supervised_mode=True)


if __name__ == "__main__":
    raise SystemExit(main())
