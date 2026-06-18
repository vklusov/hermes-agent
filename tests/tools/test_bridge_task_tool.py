from __future__ import annotations

from tools.registry import registry

import tools.bridge_task_tool as bridge_tool
from toolsets import resolve_toolset


def test_bridge_send_task_is_in_hermes_cli_toolset():
    assert "bridge_send_task" in resolve_toolset("hermes-cli")


def test_bridge_send_task_handler_delegates(monkeypatch):
    seen = {}

    def fake_send_bridge_task(agent_name, task, *, deliver="origin", timeout=10.0):
        seen["agent_name"] = agent_name
        seen["task"] = task
        seen["deliver"] = deliver
        seen["timeout"] = timeout
        return {"status": "accepted", "task_id": "abc123"}

    monkeypatch.setattr(bridge_tool, "send_bridge_task", fake_send_bridge_task)

    entry = registry.get_entry("bridge_send_task")
    assert entry is not None

    result = entry.handler(
        agent_name="fedor",
        task="проверить состояние облачных серверов",
        deliver="origin",
        timeout=7,
    )

    assert seen == {
        "agent_name": "fedor",
        "task": "проверить состояние облачных серверов",
        "deliver": "origin",
        "timeout": 7.0,
    }
    assert result == {"status": "accepted", "task_id": "abc123"}
