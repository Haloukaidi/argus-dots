"""Private round checkpoints that never touch the user's branch or index."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckpointResult:
    recorded: bool
    ref: str = ""
    error: str = ""


def _run(args: list[str], cwd: Path, env: dict[str, str] | None = None):
    git_env = dict(os.environ if env is None else env)
    # Checkpoint probes classify a small set of Git diagnostics. Keep them
    # stable without changing the user's locale or any Git/index overrides.
    git_env.update({"LC_ALL": "C", "LANGUAGE": "C"})
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=git_env,
            capture_output=True,
        )
        # Text mode folds CR/CRLF inside legitimate POSIX filenames. Preserve
        # stdout path bytes, but make diagnostic text safe for event logging.
        return subprocess.CompletedProcess(
            result.args,
            result.returncode,
            stdout=result.stdout.decode("utf-8", errors="surrogateescape"),
            stderr=result.stderr.decode("utf-8", errors="replace"),
        )
    except OSError as exc:
        return subprocess.CompletedProcess(
            ["git", *args], 127, stdout="", stderr=f"could not run git: {exc}",
        )


def _ref_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "mission"


def checkpoint_round(
    workdir: Path | str,
    *,
    mission_id: str,
    round_index: int,
    message: str,
) -> CheckpointResult:
    """Snapshot the worktree under ``refs/argus/checkpoints``.

    A temporary index starts from HEAD and stages the current worktree. The real
    index and branch are never read or changed.
    """
    start = Path(workdir).expanduser().resolve()
    top_result = _run(["rev-parse", "--show-toplevel"], start)
    if top_result.returncode != 0:
        detail = top_result.stderr.strip()
        if detail.startswith((
            "fatal: not a git repository (or any of the parent directories): .git",
            "fatal: not a git repository (or any parent up to mount point ",
        )):
            return CheckpointResult(False)
        return CheckpointResult(False, error=detail or "could not locate checkpoint repository")
    top = Path(top_result.stdout.removesuffix("\n"))
    head_result = _run(["rev-parse", "--verify", "--quiet", "HEAD"], top)
    if head_result.returncode == 1 and not head_result.stderr.strip():
        # Quiet rev-parse also returns 1 without diagnostics for a broken ref.
        # An unborn branch still has a resolvable symbolic HEAD.
        symbolic = _run(["symbolic-ref", "--quiet", "HEAD"], top)
        if symbolic.returncode == 0:
            return CheckpointResult(False)
        return CheckpointResult(False, error=symbolic.stderr.strip() or "could not resolve checkpoint HEAD")
    if head_result.returncode != 0:
        return CheckpointResult(False, error=head_result.stderr.strip() or "could not read checkpoint HEAD")
    tree_result = _run(["rev-parse", "HEAD^{tree}"], top)
    if tree_result.returncode != 0:
        return CheckpointResult(False, error=tree_result.stderr.strip() or "could not read checkpoint tree")

    with tempfile.TemporaryDirectory(prefix="argus-checkpoint-") as temp:
        index = Path(temp) / "index"
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(index)
        if _run(["read-tree", "HEAD"], top, env).returncode != 0:
            return CheckpointResult(False, error="could not prepare checkpoint index")
        staged = _run(["add", "-A", "--", "."], top, env)
        if staged.returncode != 0:
            return CheckpointResult(False, error=staged.stderr.strip())
        written = _run(["write-tree"], top, env)
        if written.returncode != 0:
            return CheckpointResult(False, error=written.stderr.strip())
        tree = written.stdout.strip()
        if tree == tree_result.stdout.strip():
            return CheckpointResult(False)
        commit_env = dict(env)
        commit_env.update({
            "GIT_AUTHOR_NAME": "Argus Engineer",
            "GIT_AUTHOR_EMAIL": "engineer@argus.invalid",
            "GIT_COMMITTER_NAME": "Argus Checkpoint",
            "GIT_COMMITTER_EMAIL": "checkpoint@argus.invalid",
        })
        commit = _run(
            ["commit-tree", tree, "-p", head_result.stdout.strip(), "-m", message],
            top,
            commit_env,
        )
        if commit.returncode != 0:
            return CheckpointResult(False, error=commit.stderr.strip())
        ref = (
            f"refs/argus/checkpoints/{_ref_part(mission_id)}/"
            f"round-{max(1, int(round_index)):04d}"
        )
        updated = _run(["update-ref", ref, commit.stdout.strip()], top)
        if updated.returncode != 0:
            return CheckpointResult(False, error=updated.stderr.strip())
        return CheckpointResult(True, ref=ref)


__all__ = ["CheckpointResult", "checkpoint_round"]
