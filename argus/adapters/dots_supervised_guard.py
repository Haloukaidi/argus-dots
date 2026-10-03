"""Host-owned change detection for explicitly approximate native dot reviews.

This is NOT a filesystem sandbox or a security boundary. A native worker shares
its platform filesystem authority with the host. Snapshots detect observable
changes to the explicitly bound inputs; they cannot prevent writes or prove
that a same-user worker never changed and restored evidence between checks.
The guard never repairs, deletes, chmods, or restores candidate files.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from jsonschema import ValidationError, validate

from ..core.daemon_log_alias import DaemonLogAlias
from ..core.models import RunnerOptions

Dispatch = Callable[[str, dict[str, Any]], dict[str, Any]]


class ReviewStore(Protocol):
    """Host-injected report capability; no reviewer-layer dependency or wire data."""

    path: Path
    receipt: Path

    def read_review(self) -> str: ...
    def write_review(self, text: str) -> dict[str, str | int]: ...
    def authored_review(self) -> str | None: ...


class ReviewStoreFactory(Protocol):
    def __call__(self, *, path: str, receipt: str) -> ReviewStore: ...

# A snapshot exceeding any limit is incomplete and blocks the call. These are
# resource bounds, never permission boundaries or silent file exclusions.
SNAPSHOT_MAX_ENTRIES = 100_000
SNAPSHOT_MAX_BYTES = 512 * 1024 * 1024
SNAPSHOT_MAX_DEPTH = 64
SNAPSHOT_MAX_SECONDS = 30.0


class _SnapshotBudget:
    def __init__(self) -> None:
        self.deadline = time.monotonic() + SNAPSHOT_MAX_SECONDS
        self.entries = 0
        self.bytes = 0

    def check(self, *, entries: int = 0, size: int = 0, depth: int = 0) -> None:
        self.entries += entries
        self.bytes += size
        if (self.entries > SNAPSHOT_MAX_ENTRIES or self.bytes > SNAPSHOT_MAX_BYTES
                or depth > SNAPSHOT_MAX_DEPTH or time.monotonic() >= self.deadline):
            raise ValueError("supervised review snapshot budget exceeded; evidence coverage is incomplete")


@dataclass(frozen=True)
class _Entry:
    identity: tuple[int, ...]
    digest: str | None = None


def _identity(info: os.stat_result, *, directory: bool = False) -> tuple[int, ...]:
    stable = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
    # Directory timestamps/link counts change when the sole allowed report is
    # atomically replaced. Directory identity and the complete entry set remain
    # protected; ordinary files retain timestamps, link count, and content.
    return stable if directory else (
        *stable, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _absolute(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("supervised review paths must be absolute without parent traversal")
    return path


def _directory(path: Path) -> _Entry:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"supervised review directory cannot be a symlink or special file: {path}")
    return _Entry(_identity(info, directory=True))


def _file(path: Path, *, missing_ok: bool = False,
          budget: _SnapshotBudget | None = None) -> _Entry | None:
    budget = budget or _SnapshotBudget()
    budget.check()
    try:
        before = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError(f"supervised review requires a regular, non-linked file: {path}")
    budget.check(size=before.st_size)
    # Refuse a symlink leaf even if it is swapped after lstat. Ancestor identity
    # checks surround operations, but are detection, not race-free confinement.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if _identity(os.fstat(descriptor)) != _identity(before):
            raise ValueError(f"supervised review file changed during snapshot: {path}")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            budget.check()
            digest.update(chunk)
        if (_identity(os.fstat(descriptor)) != _identity(before)
                or _identity(path.lstat()) != _identity(before)):
            raise ValueError(f"supervised review file changed during snapshot: {path}")
        return _Entry(_identity(before), digest.hexdigest())
    finally:
        os.close(descriptor)


class SupervisedReviewGuard:
    """One call's in-memory input snapshot and host-bound report tools.

    The caller must call ``prepare`` before publishing a worker request, and
    ``verify`` after observing its actual terminal result but before returning
    success. ``protected_paths`` binds additional host-selected evidence files
    or roots, never paths parsed from worker prose. Unrelated host writes inside
    protected roots also fail verification: there are no broad log exclusions.
    An explicitly supplied alias from original daemon setup retains its own
    immutable link identity and fully protected canonical target bytes.
    """

    def __init__(self, options: RunnerOptions, *,
                 protected_paths: Iterable[str | Path] = (),
                 review_store_factory: ReviewStoreFactory | None = None,
                 host_aliases: tuple[DaemonLogAlias, ...] = ()) -> None:
        if not isinstance(options, RunnerOptions) or not options.working_dir:
            raise ValueError("supervised review requires an explicit working directory")
        roots = [
            _absolute(options.working_dir),
            *(_absolute(path) for path in options.add_dirs or []),
            *(_absolute(path) for path in options.skill_paths or []),
            *(_absolute(path) for path in protected_paths),
        ]
        self._working_dir = roots[0]
        self._store_factory = review_store_factory
        self._store: ReviewStore | None = None
        self._outputs: tuple[Path, ...] = ()
        output = options.review_output
        if output is not None:
            if (not isinstance(output, dict) or set(output) != {"path", "receipt"}
                    or any(not isinstance(value, str) or not value for value in output.values())):
                raise ValueError("supervised review requires an exact host-selected report and receipt")
            report, receipt = _absolute(output["path"]), _absolute(output["receipt"])
            if report.name != "REVIEW.md" or report.parent.name != "paper" or report == receipt:
                raise ValueError("supervised review output must be paper/REVIEW.md with a separate receipt")
            self._outputs = (report, receipt)
        self._roots = tuple(dict.fromkeys(roots))
        if self._outputs and not any(self._outputs[0].is_relative_to(root) for root in self._roots):
            raise ValueError("supervised review report is outside explicitly protected evidence roots")
        if any(root == Path(root.anchor) for root in self._roots):
            raise ValueError("supervised review evidence roots cannot be a filesystem root")
        if any(root in self._outputs for root in self._roots):
            raise ValueError("supervised review output cannot also be a protected evidence file")
        self._aliases: dict[Path, DaemonLogAlias] = {}
        for alias in host_aliases:
            if type(alias) is not DaemonLogAlias:
                raise ValueError("supervised review requires an exact host-owned alias descriptor")
            _absolute(alias.path)
            _absolute(alias.target)
            if not any(alias.path.is_relative_to(root) for root in self._roots):
                continue  # A different daemon's descriptor grants no new scope.
            if (alias.path in self._outputs or alias.target in self._outputs
                    or not any(alias.target.is_relative_to(root) for root in self._roots)):
                raise ValueError("host-owned daemon log alias target is outside protected evidence or overlaps report output")
            if alias.path in self._aliases:
                raise ValueError("duplicate host-owned daemon log alias")
            self._aliases[alias.path] = alias
        if any(alias.target in self._aliases for alias in self._aliases.values()):
            raise ValueError("host-owned daemon log alias chains are not supported")
        self._ancestors: dict[Path, _Entry] = {}
        self._baseline: dict[Path, _Entry] = {}
        self._output_state: dict[Path, _Entry | None] = {}
        self._prepared = False
        self._failure: str | None = None
        self._report_digest: str | None = None

    def _ancestor_state(self) -> dict[Path, _Entry]:
        bound = (*self._roots, *self._outputs, *(alias.target for alias in self._aliases.values()))
        paths = {parent for root in bound for parent in root.parents}
        return {path: _directory(path) for path in sorted(paths)}

    def _alias_entry(self, alias: DaemonLogAlias) -> _Entry:
        info = alias.path.lstat()
        if (not stat.S_ISLNK(info.st_mode) or _identity(info) != alias.identity
                or _directory(alias.path.parent).identity != alias.parent_identity
                or os.readlink(alias.path) != alias.link_text
                or alias.target.resolve(strict=True) != alias.target
                or alias.path.resolve(strict=True) != alias.target):
            raise ValueError("host-owned daemon log alias changed: " + str(alias.path))
        # All target parents are separately checked without following links;
        # the actual target must still be a regular nlink=1 evidence file.
        target = alias.target.lstat()
        if not stat.S_ISREG(target.st_mode) or target.st_nlink != 1:
            raise ValueError("host-owned daemon log target must remain a regular, non-linked file")
        return _Entry(_identity(info), hashlib.sha256(alias.link_text.encode()).hexdigest())

    def _snapshot(self) -> dict[Path, _Entry]:
        result: dict[Path, _Entry] = {}
        budget = _SnapshotBudget()

        def visit(path: Path, depth: int = 0) -> None:
            if path in self._outputs or path in result:
                return
            budget.check(entries=1, depth=depth)
            before = path.lstat()
            if path in self._aliases:
                alias = self._aliases[path]
                result[path] = self._alias_entry(alias)
                visit(alias.target, depth + 1)
                self._alias_entry(alias)
            elif stat.S_ISDIR(before.st_mode):
                result[path] = _Entry(_identity(before, directory=True))
                children = []
                with os.scandir(path) as entries:
                    for entry in entries:
                        budget.check()
                        if len(children) + budget.entries >= SNAPSHOT_MAX_ENTRIES:
                            raise ValueError("supervised review snapshot entry budget exceeded; coverage is incomplete")
                        children.append(Path(entry.path))
                for child in sorted(children):
                    visit(child, depth + 1)
                if _identity(path.lstat()) != _identity(before):
                    raise ValueError(f"supervised review directory changed during snapshot: {path}")
            else:
                entry = _file(path, budget=budget)
                assert entry is not None
                result[path] = entry

        for root in self._roots:
            visit(root)
        return result

    def _outputs_now(self) -> dict[Path, _Entry | None]:
        budget = _SnapshotBudget()
        return {path: _file(path, missing_ok=True, budget=budget) for path in self._outputs}

    def _record_failure(self, exc: Exception) -> None:
        self._failure = f"supervised review verification failed: {exc}"

    def prepare(self) -> None:
        """Capture protected inputs before worker publication; fail closed."""
        if self._prepared or self._failure:
            raise ValueError("supervised review guard cannot be reused")
        try:
            _directory(self._working_dir)
            self._ancestors = self._ancestor_state()
            self._output_state = self._outputs_now()
            if self._outputs:
                if not callable(self._store_factory):
                    raise ValueError("supervised review report requires the original host-bound store factory")
                self._store = self._store_factory(path=str(self._outputs[0]), receipt=str(self._outputs[1]))
                if (self._store.path, self._store.receipt) != self._outputs:
                    raise ValueError("supervised review output escaped its host-selected path")
            self._baseline = self._snapshot()
            self._check_outputs()
            self._prepared = True
        except (OSError, TypeError, ValueError) as exc:
            self._record_failure(exc)
            raise ValueError(self._failure) from exc

    def _ready(self) -> None:
        if self._failure:
            raise ValueError(self._failure)
        if not self._prepared:
            raise ValueError("supervised review snapshot has not been prepared")

    def _check_outputs(self) -> None:
        if self._ancestor_state() != self._ancestors:
            raise ValueError("supervised review input or output parent changed")
        if self._outputs_now() != self._output_state:
            raise ValueError("supervised review report or receipt changed outside its bound tool")

    def verify(self) -> None:
        """Reject changed evidence or output before accepting runner success."""
        self._ready()
        try:
            self._check_outputs()
            current = self._snapshot()
            if current != self._baseline:
                changed = sorted(
                    str(path) for path in current.keys() | self._baseline.keys()
                    if current.get(path) != self._baseline.get(path)
                )
                raise ValueError("protected review evidence changed: " + ", ".join(changed))
            self._check_outputs()
            if self._store is not None and self._report_digest is not None:
                text = self._store.authored_review()
                if text is None or hashlib.sha256(text.encode("utf-8")).hexdigest() != self._report_digest:
                    raise ValueError("supervised review report does not match its host-authored write")
        except (OSError, TypeError, ValueError) as exc:
            self._record_failure(exc)
            raise ValueError(self._failure) from exc

    def bind_tools(self, tools: list[dict[str, Any]], dispatch: Dispatch
                   ) -> tuple[list[dict[str, Any]], Dispatch]:
        """Add only typed report access; original review actions remain authority."""
        self._ready()
        if self._store is None:
            return tools, dispatch
        report_tools = [
            {
                "name": "read_review",
                "description": "Read the current host-selected paper/REVIEW.md report.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "write_review",
                "description": (
                    "Replace only the host-selected paper/REVIEW.md with your complete review. "
                    "This writes a report; submit a separate review action for your judgment."
                ),
                "inputSchema": {
                    "type": "object", "properties": {"text": {"type": "string", "minLength": 1}},
                    "required": ["text"], "additionalProperties": False,
                },
            },
        ]
        schemas = {tool["name"]: tool["inputSchema"] for tool in report_tools}
        if any(tool.get("name") in schemas for tool in tools):
            raise ValueError("supervised review report tool name already bound")

        def wrapped(name: str, payload: dict[str, Any]) -> dict[str, Any]:
            self._ready()
            if name not in schemas:
                # In particular, run_review_command retains its original Docker
                # dispatcher. No native/subprocess fallback is introduced here.
                return dispatch(name, payload)
            try:
                validate(payload, schemas[name])
            except ValidationError as exc:
                raise ValueError(exc.message) from exc
            if name == "write_review" and not payload["text"].strip():
                raise ValueError("write a nonempty natural-language review")
            try:
                self._check_outputs()
                assert self._store is not None
                if name == "read_review":
                    text = self._store.read_review()
                    self._check_outputs()
                    return {"text": text}
                from ..core.secret_guard import known_secret_values, redact_secrets_text

                expected = redact_secrets_text(payload["text"], known_values=known_secret_values())
                expected_digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()
                reply = self._store.write_review(payload["text"])
                # Keep the expected digest in host memory, independent of the
                # receipt a shared-filesystem worker could attempt to forge.
                state = self._outputs_now()
                report, receipt = self._outputs
                if state[report] is None or state[report].digest != expected_digest:
                    raise ValueError("supervised review report mismatches the host-authored text")
                expected_receipt = hashlib.sha256(json.dumps({"sha256": expected_digest}).encode()).hexdigest()
                if state[receipt] is None or state[receipt].digest != expected_receipt:
                    raise ValueError("supervised review receipt mismatches the host-authored text")
                self._output_state = state
                self._report_digest = expected_digest
                self._check_outputs()
                return reply
            except (OSError, TypeError, ValueError) as exc:
                self._record_failure(exc)
                raise ValueError(self._failure) from exc

        return [*tools, *report_tools], wrapped
