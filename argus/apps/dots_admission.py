"""Bounded producer host commands; no daemon or native receiving endpoint."""
from __future__ import annotations

from .dots_coordinator import main as coordinator_main


def main(argv: list[str] | None = None) -> int:
    return coordinator_main(argv, bounded_mode=True)


if __name__ == "__main__":
    raise SystemExit(main())
