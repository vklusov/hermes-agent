"""Cheap router for deciding whether to escalate to GPT-5.5 expert mode."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent.auxiliary_client import call_llm
from policy.expert_policy import should_call_expert


@dataclass(frozen=True)
class ExpertNeed:
    need_expert: bool
    reason: str


def evaluate_expert_need(task: str, context: str) -> dict[str, Any]:
    """Return a conservative expert-routing decision.

    Uses GPT-5.4 mini as a cheap router only after the local policy agrees that
    the request is plausibly expert-level. For routine tasks, the function
    returns a fast local rejection.
    """

    if not should_call_expert(task, context):
        return {"need_expert": False, "reason": "routine task"}

    prompt = (
        "Decide whether this task needs a rare GPT-5.5 expert escalation. "
        "Return strict JSON with keys need_expert (boolean) and reason (string).\n\n"
        f"Task: {task}\n"
        f"Context: {context}"
    )

    response = call_llm(
        provider="custom:neurogate",
        messages=[{"role": "user", "content": prompt}],
        model="gpt-5.4-mini",
    )

    content = getattr(getattr(response, "choices", [None])[0], "message", None)
    if content is None:
        return {"need_expert": False, "reason": "router parse failure"}

    text = getattr(content, "content", "") or ""
    try:
        data = json.loads(text)
        need = bool(data.get("need_expert", False))
        reason = str(data.get("reason", "")) or ("expert needed" if need else "routine task")
        return {"need_expert": need, "reason": reason}
    except Exception:
        lowered = text.lower()
        if "true" in lowered and "need_expert" in lowered:
            return {"need_expert": True, "reason": "router indicated expert needed"}
        return {"need_expert": False, "reason": "router parse failure"}
