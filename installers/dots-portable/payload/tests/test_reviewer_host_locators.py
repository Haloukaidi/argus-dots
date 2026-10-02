"""Read host pointers through the real Reviewer, without a provider or native host."""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.model_visible_text import sanitize_reviewer_host_path
from argus.core.role_tool_bridge import bridge_request
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer.tools import PREFIX

HEX = "a1b2c3d4" * 4


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect = socket.socket.connect

    def only_local(sock, address):
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            return connect(sock, address)
        raise AssertionError("No external network")

    def no_provider(*args, **kwargs):
        raise AssertionError("No real provider allowed")

    monkeypatch.setattr(socket.socket, "connect", only_local)
    monkeypatch.setattr(AgentCliBackend, "run_exec", no_provider)


@pytest.mark.parametrize(
    "kind",
    [
        "source-cache",
        "event-log",
        "explicit-state-root",
        "no-state-root",
        "parent-of-state-root",
        "wrong-state-root",
        "background",
        "deleted-after-render",
    ],
)
def test_actual_host_pointer_reader_and_typed_judgment(tmp_path, kind):
    project = tmp_path / (HEX if kind == "source-cache" else "project")
    project.mkdir()
    extra = {}
    state_root = None
    if kind == "source-cache":
        target = project / ".argus" / "sources" / "evidence.json"
        pattern = r"Web source cache: ([^\n]+)"
    elif kind == "background":
        target = project / HEX / "context.json"
        extra["background_context"] = "HOST_CONTEXT_PATH=" + str(target)
        pattern = r"HOST_CONTEXT_PATH=([^\n]+)"
    else:
        target = project / HEX / "events.jsonl"
        if kind in {
            "explicit-state-root",
            "no-state-root",
            "wrong-state-root",
            "parent-of-state-root",
        }:
            target = tmp_path / HEX / "events.jsonl"
        if kind == "explicit-state-root":
            state_root = target.parent
        elif kind == "wrong-state-root":
            state_root = tmp_path / "other-state"
            state_root.mkdir()
        elif kind == "parent-of-state-root":
            state_root = target.parent / "nested-state"
            state_root.mkdir(parents=True)
        extra["engineer_log_path"] = str(target)
        extra["engineer_call_id"] = "call-" + "f" * 32
        pattern = r"The event log is at `([^`]+)`"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"current":"real evidence"}\n', encoding="utf-8")
    observations = []

    def read_and_decide(prompt, options):
        assert "call-" + "f" * 32 not in prompt
        path = Path(re.search(pattern, prompt).group(1))
        if kind == "source-cache":
            path = path / "evidence.json"
        row = {"kind": kind, "actual_path": str(target), "rendered_path": str(path)}
        if kind == "deleted-after-render":
            target.unlink()  # A valid rendering-time locator does not prove later readability.
        try:
            row["content"] = path.read_text(encoding="utf-8")
            good = json.loads(row["content"]) == {"current": "real evidence"}
        except OSError as exc:
            row["error"] = type(exc).__name__
            good = False
        observations.append(row)
        name = "approve_review" if good else "request_review_decision"
        arguments = {
            "review": "The current real content was read."
            if good
            else "The current evidence cannot be read."
        }
        if not good:
            arguments["question"] = "Where is the readable current evidence?"
        reply = bridge_request(PREFIX, name, arguments, env=options.extension_env)
        assert reply["recorded"] == name
        return "Submitted the current typed review action."

    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message_factory=read_and_decide))
    result = Reviewer(backend).evaluate(
        objective="Review the actual evidence",
        round_index=1,
        session_id=None,
        main_summary="The actual evidence was generated.",
        main_error=None,
        config=ReviewerConfig(
            active_vertical="software",
            working_dir=str(project),
            vertical_state_root=str(state_root) if state_root else None,
        ),
        **extra,
    )
    success = kind in {"source-cache", "event-log", "explicit-state-root"}
    assert (result.status == "done") == success
    assert not result.backend_unavailable
    assert len(observations) == 1
    row = observations[0]
    if success:
        assert row["rendered_path"] == str(target) and "error" not in row
    else:
        assert row["error"] == "FileNotFoundError"
        if kind != "deleted-after-render":
            assert "<machine-integrity-metadata omitted>" in row["rendered_path"]
    (tmp_path / "host-pointer-evidence.json").write_text(
        json.dumps(row, indent=2), encoding="utf-8"
    )


@pytest.mark.parametrize("directory", [True, False])
def test_host_locator_requires_correct_kind_and_explicit_root(tmp_path, directory):
    path = tmp_path / (HEX + ".json")
    if directory:
        path.write_text("A regular file, not a cache directory.")
    else:
        path.mkdir()
    assert HEX not in sanitize_reviewer_host_path(
        path, allowed_roots=(tmp_path,), directory=directory
    )
    assert HEX not in sanitize_reviewer_host_path(path, allowed_roots=(), directory=directory)
    assert HEX not in sanitize_reviewer_host_path(
        Path(path.name), allowed_roots=(tmp_path,), directory=directory
    )


@pytest.mark.parametrize("directory", [True, False])
def test_host_locator_links_do_not_supply_authorized_roots(
    tmp_path, require_symlink_support, directory
):
    root = tmp_path / "project"
    root.mkdir()
    actual = tmp_path / (HEX + ".json")
    actual.mkdir() if directory else actual.write_text("Outside data.")
    linked = root / (HEX + ".json")
    linked.symlink_to(actual, target_is_directory=directory)
    rendered = sanitize_reviewer_host_path(
        linked, allowed_roots=(root,), directory=directory, resolve_display=True
    )
    assert HEX not in rendered
    assert HEX not in sanitize_reviewer_host_path(
        actual, allowed_roots=(root,), directory=directory
    )


@pytest.mark.parametrize("resolve_display", [True, False])
def test_host_locator_never_restores_known_credentials(tmp_path, monkeypatch, resolve_display):
    monkeypatch.setenv("FIXTURE_API_KEY", HEX)
    path = tmp_path / (HEX + ".json")
    path.write_text("The filename contains a dummy configured credential.")
    rendered = sanitize_reviewer_host_path(
        path, allowed_roots=(tmp_path,), resolve_display=resolve_display
    )
    assert HEX not in rendered and "<REDACTED:" in rendered
