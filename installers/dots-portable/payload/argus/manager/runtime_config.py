"""Warm Manager runner configuration ownership, shared by front doors."""
from __future__ import annotations

from typing import Any


def refresh_manager_runtime_config(state: dict[str, Any]) -> None:
    """Revalidate borrowed warm runners after backend/model/effort changes."""
    from ..core.knobs import resolve_role_backend
    from ..core.runtime_backend import normalize_runtime_backend, runtime_configuration_key

    key = runtime_configuration_key()
    selected = normalize_runtime_backend(resolve_role_backend(
        "manager", default=str(state.get("backend") or "codex"),
    ))
    previous = state.get("manager_runtime_config")
    if ((previous is not None and key != previous)
            or (state.get("backend") is not None and selected != state.get("backend"))):
        from .config_intent import _invalidate_manager_runner

        _invalidate_manager_runner(state, backend=selected)
    state["manager_runtime_config"] = key
    state["backend"] = selected
