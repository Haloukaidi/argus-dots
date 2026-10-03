"""Host-memory provenance for the daemon's own POSIX log alias.

Only the original daemon setup records these descriptors after creating its
link. They are not loaded from project files, requests, or worker prose. This
is an evidence-locator contract, not a permission or same-user security boundary.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DaemonLogAlias:
    path: Path
    target: Path
    link_text: str
    identity: tuple[int, ...]
    parent_identity: tuple[int, ...]


_created_aliases: dict[Path, DaemonLogAlias] = {}


def record_created_daemon_log_alias(life_dir: Path, target: Path) -> None:
    """Record the exact link just made by daemon setup; never create a link.

    The log target can be absent until the daemon opens it. Its regular-file
    status and contents must therefore be checked by the later evidence guard.
    Unsupported shapes receive no descriptor and remain fail-closed there.
    """
    path, target = (life_dir / "daemon.log").absolute(), target.absolute()
    _created_aliases.pop(path, None)
    if os.name != "posix" or ".." in path.parts or ".." in target.parts:
        return
    try:
        parent = path.parent.lstat()
        info = path.lstat()
        text = os.readlink(path)
        if (path.parent.resolve(strict=True) != path.parent
                or not stat.S_ISDIR(parent.st_mode) or not stat.S_ISLNK(info.st_mode)
                or info.st_nlink != 1 or text != os.path.relpath(target, path.parent)):
            return
        _created_aliases[path] = DaemonLogAlias(
            path, target, text,
            (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
             info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns),
            (parent.st_dev, parent.st_ino, parent.st_mode, parent.st_uid, parent.st_gid),
        )
    except OSError:
        # The original alias operation is best-effort during daemon startup.
        # Failure to record provenance grants nothing to subsequent reviews.
        return


def created_daemon_log_aliases() -> tuple[DaemonLogAlias, ...]:
    """Return immutable host-only bindings; no descriptor is serialized."""
    return tuple(_created_aliases.values())
