"""Nonsensitive launcher configuration parsing; no provider construction.

A file is a non-secret session locator, not a credential or proof that native
execution is available. The live host renews its own bounded lease. Web and
its daemon children read the same explicit locator and never start a host.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, NoReturn

from .runtime_backend import RuntimeBackendUnavailable

HOST_CONFIG_ENV = "ARGUS_DOTS_HOST_CONFIG"
_CONFIG_KEYS = frozenset({"version", "bridge_dir", "session_id", "producer_id", "project_root", "execution_profile"})
_OPTIONAL_CONFIG_KEYS = frozenset({"request_timeout_seconds"})
_MAX_CONFIG_BYTES = 16384


@dataclass(frozen=True)
class DotsHostBinding:
    transport: Any
    execution_profile: Any
    identity: str
    config_path: Path
    request_timeout_seconds: float = 300


def dots_host_unavailable(reason: str) -> RuntimeBackendUnavailable:
    detail = reason if reason.startswith("host-required:") else "host-required: " + reason
    return RuntimeBackendUnavailable({"host": detail})


def _reject_nonfinite(_value: str) -> NoReturn:
    raise ValueError("nonfinite host configuration value")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, entry in pairs:
        if key in value:
            raise ValueError("duplicate host configuration field")
        value[key] = entry
    return value


def absolute_host_directory(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty absolute directory")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute directory without traversal")
    if not path.is_dir():
        raise ValueError(f"{label} does not exist; launcher readiness creates no directories")
    return path


def read_dots_host_config(env: Mapping[str, str]) -> tuple[Path, dict[str, Any]] | None:
    locator = str(env.get(HOST_CONFIG_ENV, "") or "").strip()
    if not locator:
        return None
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("supervised file host binding requires POSIX no-follow access")
    path = Path(locator)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("host configuration must be an explicit absolute file path")
    # No credential parsing, project discovery, executable loading, or imports
    # named by configuration. Reject symlinks and special files before reading.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_CONFIG_BYTES:
            raise ValueError("host configuration must be a regular file of at most 16 KiB")
        data = os.read(fd, _MAX_CONFIG_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > _MAX_CONFIG_BYTES:
        raise ValueError("host configuration exceeds 16 KiB")
    value = json.loads(data, object_pairs_hook=_unique_object,
                       parse_constant=_reject_nonfinite)
    if (not isinstance(value, dict) or not _CONFIG_KEYS <= set(value)
            or set(value) - _CONFIG_KEYS - _OPTIONAL_CONFIG_KEYS
            or type(value["version"]) is not int or value["version"] != 1):
        raise ValueError("invalid explicit host configuration schema")
    timeout = value.get("request_timeout_seconds", 300)
    if (type(timeout) not in (int, float) or not 0 < timeout <= 3600
            or not math.isfinite(timeout)):
        raise ValueError("request_timeout_seconds must be a finite number greater than 0, at most 3600")
    return path, value


def binding_configuration_key(*, env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Public locator/content identity for warm-runner invalidation only."""
    env_map = os.environ if env is None else env
    try:
        loaded = read_dots_host_config(env_map)
        if loaded is None:
            return HOST_CONFIG_ENV, ""
        path, value = loaded
        # Only the fixed nonsensitive schema is hashed; readiness is separate.
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        return HOST_CONFIG_ENV, str(path) + ":" + digest
    except (OSError, ValueError, TypeError):
        return HOST_CONFIG_ENV, "invalid:" + str(env_map.get(HOST_CONFIG_ENV, ""))


def configured_dots_profile_name(*, env: Mapping[str, str] | None = None) -> str:
    """Show the selected approximate profile without claiming readiness."""
    try:
        loaded = read_dots_host_config(os.environ if env is None else env)
        if loaded is None:
            return ""
        from .dots_profile import SupervisedDotsProfile

        return SupervisedDotsProfile.from_dict(loaded[1]["execution_profile"]).name
    except (OSError, ValueError, TypeError):
        return "invalid supervised profile"
