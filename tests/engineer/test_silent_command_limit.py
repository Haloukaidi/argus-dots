"""A command that prints nothing for the idle limit is stopped, named, and not repeated as it was."""
from __future__ import annotations

import json

from argus.agent_cli import copilot_acp
from argus.agent_cli.copilot_acp import CopilotAcpClient
from argus.engineer.round_config import SupervisedConfig
from argus.engineer.round_stop_signals import (
    fatal_error_is_idle_termination,
    idle_termination_review_decision,
    idle_termination_running_tool,
)

REASON = (
    "Forced restart after hard idle timeout (1800s without an ACP stream event; "
    "last event: tool_call; running tool: find / -name '*pdftoppm*')"
)


def test_engineer_turns_have_a_default_silence_limit(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS", raising=False)
    assert SupervisedConfig().runner_hard_idle_seconds == 1800
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_HARD_IDLE_SECONDS", "0")
    assert SupervisedConfig().runner_hard_idle_seconds == 0


def test_the_stopped_turn_names_the_command_and_says_what_to_do_instead() -> None:
    assert fatal_error_is_idle_termination(REASON)
    assert not fatal_error_is_idle_termination("Process exited with code 1 before turn completion.")
    assert idle_termination_running_tool(REASON) == "find / -name '*pdftoppm*'"
    assert idle_termination_running_tool("Forced restart after hard idle timeout (900s without a model stream event).") == ""

    decision = idle_termination_review_decision(fatal_error=REASON, exit_code=-15, streak=1, threshold=2)
    assert decision.status == "continue"
    assert "find / -name '*pdftoppm*'" in decision.reason
    assert "model service" not in decision.reason
    assert "timeout" in decision.next_action and "background job" in decision.next_action
    assert "CHECKPOINT.md" in decision.next_action


def test_acp_turn_tracks_the_tool_in_flight() -> None:
    client = CopilotAcpClient("copilot-bin")
    emitted: list[str] = []
    turn = copilot_acp._Turn("s1", None, emitted.append, allow_persistent=True)
    client._active_turn = turn

    def update(payload: dict) -> None:
        client._handle_notification("session/update", {"sessionId": "s1", "update": payload})

    assert turn.running_tool == ""
    update({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "find / -name '*pdftoppm*'",
            "kind": "execute", "status": "pending", "rawInput": {"command": "find / -name '*pdftoppm*'"}})
    assert turn.running_tool == "find / -name '*pdftoppm*'"
    update({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "in_progress"})
    assert turn.running_tool == "find / -name '*pdftoppm*'"
    update({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"})
    assert turn.running_tool == ""
    structured = [json.loads(line) for line in emitted if line.startswith("{")]
    assert [event["type"] for event in structured] == ["tool.call", "tool.result"]
