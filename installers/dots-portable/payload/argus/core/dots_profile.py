"""Explicit acceptance of approximate native roles, never an OS capability.

Only a supervising host may select this contract. Project text/config does not
select it. The native catalog is a host assertion about supported tool arguments,
not an observation of which model actually generated a result.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

PROFILE_NAME = "supervised-approx-v1"
ADVISORY_OPTIONS = frozenset({
    "working_dir", "add_dirs", "skill_paths", "sandbox_mode:read-only",
    "sandbox_mode:workspace-write", "force_safe_mode", "disable_tools",
    "live_search", "review_output", "full_auto",
})
IGNORED_OPTIONS = frozenset({
    "dangerous_yolo", "watchdog_soft_idle_seconds", "watchdog_stalled_idle_seconds",
    "inactivity_callback",
})


@dataclass(frozen=True)
class SupervisedDotsProfile:
    """Narrow, versioned operator consent to behavioral-only role controls.

``model_efforts`` lists exact native tool-supported model IDs and efforts. The
empty model ID means a host-default selection whose concrete model is unknown.
No model alias, effort fallback, CLI flag or credential is inferred.
    """

    model_efforts: tuple[tuple[str, tuple[str, ...]], ...] = ()
    read_roots: tuple[str, ...] = ()
    report_roots: tuple[str, ...] = ()
    effort_overrides: tuple[tuple[str, str], ...] = ()
    default_effort_override: str | None = None

    name = PROFILE_NAME
    advisory_options = ADVISORY_OPTIONS
    ignored_options = IGNORED_OPTIONS

    def __post_init__(self) -> None:
        if not isinstance(self.model_efforts, tuple):
            raise ValueError("native model catalog must be an immutable tuple")
        seen = set()
        for item in self.model_efforts:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("invalid native model catalog entry")
            model, efforts = item
            if (not isinstance(model, str) or model.strip() != model or model in seen
                    or not isinstance(efforts, tuple)
                    or any(not isinstance(e, str) or not e.strip() or e.strip() != e for e in efforts)
                    or len(set(efforts)) != len(efforts)):
                raise ValueError("native model/effort choices must be exact and distinct")
            seen.add(model)
        from pathlib import Path
        for roots in (self.read_roots, self.report_roots):
            if (not isinstance(roots, tuple) or any(not isinstance(root, str) or not root
                    or not Path(root).is_absolute() or ".." in Path(root).parts for root in roots)
                    or len(set(roots)) != len(roots)):
                raise ValueError("approved external roots must be explicit distinct absolute paths")
            for root in roots:
                path = Path(root)
                if path == Path(path.anchor):
                    raise ValueError("approved external roots cannot be filesystem roots")
        if (not isinstance(self.effort_overrides, tuple)
                or any(not isinstance(pair, tuple) or len(pair) != 2
                       or any(not isinstance(value, str) or not value.strip() or value.strip() != value
                              for value in pair) for pair in self.effort_overrides)
                or len({pair[0] for pair in self.effort_overrides}) != len(self.effort_overrides)):
            raise ValueError("effort overrides must explicitly map distinct requested efforts")
        if (self.default_effort_override is not None
                and (not isinstance(self.default_effort_override, str)
                     or not self.default_effort_override.strip()
                     or self.default_effort_override.strip() != self.default_effort_override)):
            raise ValueError("default effort override must be explicit nonempty text or null")

    def validate_live_roots(self) -> None:
        """New admission only; missing mounts must never block stop/settlement."""
        from pathlib import Path

        for root in (*self.read_roots, *self.report_roots):
            path = Path(root)
            if path.resolve(strict=True) != path or not path.is_dir():
                raise ValueError("approved external roots must be existing canonical directories")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": 1,
                "accepted_deficits": sorted(self.advisory_options),
                "ignored_without_privilege_expansion": sorted(self.ignored_options),
                "model_efforts": {model: list(efforts) for model, efforts in self.model_efforts},
                "read_roots": list(self.read_roots), "report_roots": list(self.report_roots),
                "reasoning_effort_overrides": dict(self.effort_overrides),
                "default_effort_override": self.default_effort_override,
                "filesystem_isolation": False, "native_tool_allowlist": False,
                "evidence_integrity": "trusted-host-detection-only"}

    @classmethod
    def from_dict(cls, value: Any) -> SupervisedDotsProfile:
        if not isinstance(value, dict) or not isinstance(value.get("model_efforts"), dict):
            raise ValueError("invalid supervised execution profile")
        catalog = value["model_efforts"]
        if any(not isinstance(k, str) or not isinstance(v, list) for k, v in catalog.items()):
            raise ValueError("invalid native model/effort catalog")
        if (not isinstance(value.get("read_roots"), list) or not isinstance(value.get("report_roots"), list)
                or type(value.get("version")) is not int
                or value.get("filesystem_isolation") is not False
                or value.get("native_tool_allowlist") is not False):
            raise ValueError("invalid explicit supervised profile metadata")
        overrides = value.get("reasoning_effort_overrides")
        if not isinstance(overrides, dict):
            raise ValueError("effort overrides must be explicitly present, even when empty")
        result = cls(tuple((model, tuple(efforts)) for model, efforts in catalog.items()),
                     tuple(value["read_roots"]), tuple(value["report_roots"]), tuple(overrides.items()),
                     value.get("default_effort_override"))
        if result.to_dict() != value:
            raise ValueError("execution profile must explicitly accept the exact versioned deficits")
        return result

    def disposition(self, name: str, value: Any) -> str | None:
        option = f"sandbox_mode:{value}" if name == "sandbox_mode" else name
        if option in self.advisory_options:
            return "advisory"
        if name in self.ignored_options:
            return "ignored-without-privilege-expansion"
        return None

    def validate_option(self, name: str, value: Any, *, serialized: bool = False) -> None:
        """Validate the narrow advisory payload without interpreting authority."""
        from pathlib import Path

        if self.disposition(name, value) is None:
            raise ValueError("supervised profile does not accept execution option: " + name)
        if name in {"force_safe_mode", "disable_tools", "live_search", "full_auto", "dangerous_yolo"}:
            valid = type(value) is bool
        elif name == "working_dir":
            valid = isinstance(value, str) and bool(value) and Path(value).is_absolute() and ".." not in Path(value).parts
        elif name in {"add_dirs", "skill_paths"}:
            valid = isinstance(value, list) and all(isinstance(v, str) and v and Path(v).is_absolute()
                                                     and ".." not in Path(v).parts for v in value)
        elif name == "review_output":
            valid = (isinstance(value, dict) and set(value) == {"path", "receipt"}
                     and all(isinstance(v, str) and v and Path(v).is_absolute()
                             and ".." not in Path(v).parts for v in value.values()))
        elif name in {"watchdog_soft_idle_seconds", "watchdog_stalled_idle_seconds"}:
            valid = type(value) is int and value >= 0
        elif name == "inactivity_callback":
            valid = value is True if serialized else callable(value)
        else:  # Exact sandbox modes were already checked by disposition().
            valid = name == "sandbox_mode"
        if not valid:
            raise ValueError("invalid supervised execution option: " + name)

    def dispatch(self, model: str | None, effort: str | None) -> dict[str, str]:
        """Only exact supported spawn arguments; omitted defaults stay unknown."""
        catalog = dict(self.model_efforts)
        if model is not None and model not in catalog:
            raise ValueError("requested native model is not in the host-supported catalog")
        dispatch_effort = (self.default_effort_override if effort is None
                           else dict(self.effort_overrides).get(effort, effort))
        if dispatch_effort is not None and dispatch_effort not in catalog.get(model or "", ()):
            raise ValueError("requested native reasoning effort is not supported for this model")
        return {**({"model": model} if model is not None else {}),
                **({"reasoning_effort": dispatch_effort} if dispatch_effort is not None else {})}

    def resolution(self, model: str | None, effort: str | None) -> dict[str, Any]:
        return {"requested_model": model, "requested_reasoning_effort": effort,
                "dispatch_arguments": self.dispatch(model, effort),
                "observed_model": None, "observed_reasoning_effort": None,
                "effort_resolution": ("explicit-profile-override"
                    if effort in dict(self.effort_overrides) or (effort is None and self.default_effort_override is not None)
                    else "exact-or-host-default"),
                "default_model_resolution": "unknown" if model is None else "explicit-request",
                "source": "host-declared-tool-support; output-model-unobserved"}


class SupervisedDotsTransport(ABC):
    """Kernel-owned explicit supervised-host port; implementations stay outside core."""

    execution_profile: SupervisedDotsProfile

    @abstractmethod
    def assert_ready(self, project_root: Any = None) -> dict[str, Any]:
        """Validate current immutable scope and bounded host availability."""
