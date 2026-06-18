"""Native bridge task dispatch tool.

Routes bridge tasks to Fedor (VPS) and the 93rd Mac mini without shell
approval by calling the Python bridge helper directly.
"""

from __future__ import annotations

from typing import Any

from agent.bridge_bootstrap import send_bridge_task

_TOOL_NAME = "bridge_send_task"
_TOOLSET = "hermes-cli"


def _handle_bridge_send_task(
    agent_name: str,
    task: str,
    deliver: str = "origin",
    timeout: float = 10.0,
    **_: Any,
) -> dict[str, Any]:
    return send_bridge_task(
        agent_name,
        task,
        deliver=deliver,
        timeout=float(timeout),
    )


try:
    from tools.registry import registry

    registry.register(
        name=_TOOL_NAME,
        toolset=_TOOLSET,
        schema={
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Target bridge agent name or alias, e.g. Fedor or 93.",
                },
                "task": {
                    "type": "string",
                    "description": "Task to send to the bridge agent.",
                },
                "deliver": {
                    "type": "string",
                    "description": "Delivery target for the reply. Defaults to origin.",
                    "default": "origin",
                },
                "timeout": {
                    "type": "number",
                    "description": "HTTP timeout in seconds.",
                    "default": 10.0,
                },
            },
            "required": ["agent_name", "task"],
            "additionalProperties": False,
        },
        handler=_handle_bridge_send_task,
        description="Send a task to Fedor or the 93rd Mac mini via the bridge without shell approval.",
        is_async=False,
    )
except Exception:
    pass
