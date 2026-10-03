"""Concrete supervised host composition; kernel code owns only its input contract."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from ..core.dots_host_binding import (
    DotsHostBinding,
    absolute_host_directory,
    dots_host_unavailable,
    read_dots_host_config,
)
from ..core.runtime_backend import (
    RuntimeBackendUnavailable,
    require_dots_runtime,
    selected_dots_roles,
)


def resolve_dots_host_binding(*, project_root: Path | str | None = None,
                              env: Mapping[str, str] | None = None) -> DotsHostBinding | None:
    """Load only an explicitly configured, current bounded receiving session.

    The execution profile is opt-in only here or as an explicit in-process
    argument. Never infer it from a transport's class or a project file.
    """
    env_map = os.environ if env is None else env
    try:
        loaded = read_dots_host_config(env_map)
        if loaded is None:
            return None
        from ..core.dots_profile import SupervisedDotsProfile
        from .dots_backend import validate_request_id
        from .dots_supervised import SupervisedRoleFileDotsTransport

        path, value = loaded
        validate_request_id(value["session_id"])
        producer = value["producer_id"]
        if not isinstance(producer, str) or not producer.strip() or len(producer) > 128:
            raise ValueError("producer_id must be nonempty text of at most 128 characters")
        bridge_dir = absolute_host_directory(value["bridge_dir"], "bridge_dir")
        bound_root = absolute_host_directory(value["project_root"], "project_root").resolve(strict=True)
        if project_root is not None:
            requested = Path(project_root).resolve(strict=True)
            if requested != bound_root:
                raise ValueError("requested project is outside the explicit host binding")
        profile = SupervisedDotsProfile.from_dict(value["execution_profile"])

        class ConfiguredSupervisedTransport(SupervisedRoleFileDotsTransport):
            def assert_admission_current(self) -> None:
                # Cached daemon adapters stay on their original session. A
                # replaced/removed launcher binding requires reconstruction,
                # never borrowing new worker IDs or silently keeping old scope.
                current = read_dots_host_config(env_map)
                if current is None or current[0] != path or current[1] != value:
                    raise ValueError("host-required: launcher binding changed; reconstruct the scoped runner")
                super().assert_admission_current()

        transport = ConfiguredSupervisedTransport(
            bridge_dir, profile=profile, session_id=value["session_id"],
            producer_id=value["producer_id"], project_root=bound_root,
        )
        transport.assert_ready()
        require_dots_runtime("dots", transport=transport, execution_profile=profile, env=env_map)
        identity = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return DotsHostBinding(transport, profile, identity, path,
                               request_timeout_seconds=value.get("request_timeout_seconds", 300))
    except RuntimeBackendUnavailable:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise dots_host_unavailable(str(exc)) from exc


def require_configured_dots_runtime(backend: str | None = None, *,
                                    project_root: Path | str | None = None,
                                    env: Mapping[str, str] | None = None) -> DotsHostBinding | None:
    """Shared Web/daemon preflight; ordinary backends remain untouched."""
    if not selected_dots_roles(backend, env=env):
        return None
    binding = resolve_dots_host_binding(project_root=project_root, env=env)
    if binding is None:
        require_dots_runtime(backend, env=env)  # unchanged strict refusal
    else:
        # Preserve the caller's original default and role overrides. Resolving
        # a native binding must not turn an explicitly mixed pipeline into dots.
        require_dots_runtime(backend, transport=binding.transport,
                             execution_profile=binding.execution_profile, env=env)
        from ..core.backend_readiness import resolve_backend_profile

        if resolve_backend_profile("dots", env=env).auth_mode != "native_host":
            raise RuntimeBackendUnavailable({"auth_mode": "supervised dots requires native_host; remove the conflicting CLI/API authentication setting"})
    return binding


def require_project_dots_runtime(sid: str, *, global_root: Path | str,
                                 state_dir: Path | str) -> DotsHostBinding | None:
    """Resolve the existing canonical workdir without migration/state writes."""
    if not selected_dots_roles():
        return None
    from ..core.session import read_session_meta, resolve_session_workdir

    try:
        meta = read_session_meta(Path(global_root), sid)
        workdir = resolve_session_workdir(meta, state_dir=state_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        raise dots_host_unavailable(f"project workdir cannot be verified: {exc}") from exc
    return require_configured_dots_runtime(project_root=workdir)


def check_configured_backend_readiness(backend=None, auth_mode=None, **kwargs):
    """Compose optional host readiness before calling the provider-neutral gate."""
    from ..core.backend_readiness import (
        BackendReadiness,
        ReadinessProblem,
        check_backend_readiness,
        resolve_backend_profile,
    )

    env = kwargs.get("env")
    binding = None
    if selected_dots_roles(backend, env=env):
        try:
            binding = require_configured_dots_runtime(
                backend, project_root=kwargs.get("dots_project_root"), env=env,
            )
        except RuntimeBackendUnavailable as exc:
            report = BackendReadiness(profile=resolve_backend_profile(backend, auth_mode, env=env))
            report.problems.extend(ReadinessProblem(
                f"dots {role}", detail,
                "Attach the explicitly authorized native host and renew its bounded session lease; strict dots still requires enforced controls.",
            ) for role, detail in exc.problems.items())
            return report
    if binding is None:
        return check_backend_readiness(backend, auth_mode, **kwargs)
    return check_backend_readiness(backend, auth_mode, dots_binding=binding, **kwargs)
