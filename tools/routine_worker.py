"""Route bounded routine work to GPT-5.4-mini.

The tool is native (registered in tools.registry) and policy-gated. It exists so
Hermes can keep the main agent on GPT-5.5 while offloading small, low-risk tasks
without relying on prompt-only markdown guidance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.auxiliary_client import call_llm
from policy.routine_policy import should_use_routine_worker

_TOOLSET = "hermes-routine-worker"
_TOOL_NAME = "routine_worker"
_ROUTINE_PROVIDER = "custom:neurogate"
_ROUTINE_MODEL = "gpt-5.4-mini"


@dataclass(frozen=True)
class RoutineDecision:
    allowed: bool
    reason: str


def _extract_response_text(response: Any) -> str:
    choice = getattr(getattr(response, "choices", [None])[0], "message", None)
    if choice is None:
        return str(response)
    return str(getattr(choice, "content", str(choice)) or "")


def _decide(task: str, context: str = "") -> RoutineDecision:
    if should_use_routine_worker(task, context):
        return RoutineDecision(True, "allowed")
    return RoutineDecision(False, "Routine worker rejected by policy")


def routine_worker(task: str, context: str = "") -> str:
    """Run a small routine task on GPT-5.4-mini after a local policy check."""

    task = str(task or "").strip()
    context = str(context or "").strip()
    decision = _decide(task, context)
    if not decision.allowed:
        return decision.reason

    prompt = (
        "You are Hermes' routine worker running on GPT-5.4-mini.\n"
        "Handle only the bounded routine task below. Be concise.\n"
        "Do not perform external side effects; return text only.\n\n"
    )
    if context:
        prompt += f"Context:\n{context}\n\n"
    prompt += f"Task:\n{task}"

    response = call_llm(
        provider=_ROUTINE_PROVIDER,
        messages=[{"role": "user", "content": prompt}],
        model=_ROUTINE_MODEL,
    )
    return _extract_response_text(response)


def _handle_routine_worker(task: str, context: str = "", **_: Any) -> str:
    return routine_worker(task=task, context=context)


try:
    from tools.registry import registry

    registry.register(
        name=_TOOL_NAME,
        toolset=_TOOLSET,
        schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Small bounded routine task to run on GPT-5.4-mini.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional compact context for the routine task.",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        },
        handler=_handle_routine_worker,
        description=(
            "Run a small, low-risk routine task on GPT-5.4-mini after native "
            "policy checks. Complex or high-risk work is rejected."
        ),
        is_async=False,
    )
except Exception:
    pass
