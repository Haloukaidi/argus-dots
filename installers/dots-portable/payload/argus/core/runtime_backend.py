"""Runtime backend selection, separate from the agent-CLI provider catalog.

Dots is an explicitly injected native host, never a CLI executable or an
implicit fallback. Readiness reports guarantees, not prompt instructions.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .knobs import RUNTIME_BACKENDS as RUNTIME_BACKENDS
from .knobs import normalize_runtime_backend as normalize_runtime_backend

DOTS_ROLES = ("manager", "planner", "engineer", "reviewer", "curator")
# Requested baseline controls of the existing role calls, not a claim that
# every CLI backend enforces them. Conditional requirements
# (schemas, isolation, explicit resume, etc.) remain checked on every call.
DOTS_REQUIRED_OPTIONS = {
    "manager": frozenset({"disable_tools", "working_dir", "sandbox_mode:read-only", "force_safe_mode", "skill_paths"}),
    "planner": frozenset({"working_dir", "sandbox_mode:read-only", "skill_paths"}),
    "engineer": frozenset({"working_dir", "full_auto", "skill_paths", "live_search"}),
    "reviewer": frozenset({"working_dir", "sandbox_mode:read-only", "force_safe_mode", "review_output", "skill_paths", "live_search"}),
    "curator": frozenset({"working_dir", "full_auto"}),
}


@dataclass(frozen=True)
class DotsCapabilities:
    """Receiving-host guarantees, never permissions granted by a prompt."""

    roles: frozenset[str] = frozenset(DOTS_ROLES)
    options: frozenset[str] = frozenset()
    resume: bool = False
    role_tools: bool = False


class RuntimeBackendUnavailable(RuntimeError):
    """A selected non-CLI runtime cannot safely accept the requested work."""

    def __init__(self, problems: Mapping[str, str]):
        self.problems = dict(problems)
        super().__init__("dots runtime unavailable: " + "; ".join(
            f"{role}: {reason}" for role, reason in self.problems.items()
        ))


def selected_dots_roles(backend: str | None = None, *, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return all role selections iff any role or the explicit request is dots.

    Keep the established role/env/persisted precedence. A mixed native/CLI
    pipeline is not supported by the five-role injection factory.
    """
    from .knobs import resolve_role_backend

    roles = {role: str(resolve_role_backend(
        role, env=env, default=backend or "codex",
    )).strip().lower() for role in DOTS_ROLES}
    return roles if "dots" in roles.values() or str(backend or "").strip().lower() == "dots" else {}


def dots_readiness_problems(transport: Any = None) -> dict[str, str]:
    if transport is None:
        return {"host": "no native receiving transport is configured; ordinary CLI executables cannot dispatch conversation-native tools"}
    capabilities = getattr(transport, "capabilities", None)
    if not isinstance(capabilities, DotsCapabilities):
        return {"host": "receiving transport has no valid capability contract"}
    problems = {}
    for role, required in DOTS_REQUIRED_OPTIONS.items():
        if role not in capabilities.roles:
            problems[role] = "role is not supported by the receiving host"
            continue
        missing = sorted(required - capabilities.options)
        if role == "reviewer" and not capabilities.role_tools:
            missing.append("call-bound role_tools")
        if missing:
            problems[role] = "missing enforced controls: " + ", ".join(missing)
    return problems


def require_dots_runtime(backend: str | None = None, *, transport: Any = None,
                         env: Mapping[str, str] | None = None) -> bool:
    """Preflight an explicit dots selection without submitting or creating state.

    Returns False for an ordinary pipeline. The optional transport is an
    in-process host-owned dependency, never deserialized from project config.
    An unattended CLI/Web process currently has no supported native receiver.
    """
    roles = selected_dots_roles(backend, env=env)
    if not roles:
        return False
    conflicts = {role: f"selected {name!r}; all five roles must resolve to dots"
                 for role, name in roles.items() if name != "dots"}
    if conflicts:
        raise RuntimeBackendUnavailable(conflicts)
    problems = dots_readiness_problems(transport)
    if problems:
        raise RuntimeBackendUnavailable(problems)
    return True


def runtime_configuration_key(*, env: Mapping[str, str] | None = None) -> tuple[tuple[str, str], ...]:
    """Public backend/model/effort settings that invalidate a warm role runner.

    This is a value tuple, not a credential fingerprint. Authentication files,
    API keys and provider-private state are never read.
    """
    import os

    from .knob_store import read_persisted_knobs

    persisted = read_persisted_knobs()
    env_map = os.environ if env is None else env
    names = {name for name in set(persisted) | set(env_map)
             if name.startswith("ARGUS_SKILL_") and (
                 name.endswith(("_BACKEND", "_MODEL", "_REASONING_EFFORT", "_RUNNER_BIN"))
                 or name in {"ARGUS_SKILL_FRONTDOOR_CLASSIFY_EFFORT", "ARGUS_SKILL_SKILLS_DIR"}
             )}
    return tuple((name, str(env_map.get(name, persisted.get(name, ""))))
                 for name in sorted(names))
