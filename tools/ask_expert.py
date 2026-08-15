"""Advisory second-opinion routing for high-risk infrastructure decisions.

The tool never executes actions and never grants approval. It takes the
agent's decision and asks a stronger arbiter model to confirm or reject it.
The arbiter is intentionally advisory-only; the caller keeps responsibility
for deciding whether automatic execution is allowed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from agent.auxiliary_client import call_llm
from tools.registry import registry

logger = logging.getLogger(__name__)

_TOOLSET = "hermes-ask-expert"
_MAX_TASK = 2400
_MAX_ANALYSIS = 3200
_MAX_DECISION = 1000
_MAX_RESPONSE = 12000


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _redact(text: str) -> str:
    """Remove common credential-shaped values before external expert calls."""
    text = re.sub(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", text)
    text = re.sub(r"\b(sk|xai|r8|hf|ghp|glpat)-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)
    return text


def _content(response: Any) -> str:
    try:
        return str(response.choices[0].message.content or "").strip()
    except Exception:
        return str(response or "").strip()


def _json_object(text: str) -> dict[str, Any] | None:
    text = text.strip().removeprefix("```json").removesuffix("```").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except Exception:
            return None


def _arbiter_prompt(task: str, local_analysis: str, gpt_decision: str) -> str:
    return (
        "You are GPT-5.6 Sol acting as an advisory arbiter inside Hermes. "
        "You have no authority to execute commands, approve changes, or modify systems. "
        "You will receive: (1) a task description, (2) local analysis with facts, "
        "(3) a concrete decision that the local background model has already made.\n\n"
        "Your job: review whether you AGREE or DISAGREE with the proposed decision based on the facts.\n\n"
        "Return ONLY valid JSON with keys:\n"
        "- agrees (boolean): true if you agree with the proposed decision, false if you disagree\n"
        "- verdict (string): your reasoning, max 200 words\n"
        "- confidence (number 0..1): how confident you are in your assessment\n"
        "- risks (array of strings): any risks the background model may have missed\n"
        "- checks_before_change (array of strings): additional checks to perform if decision is to proceed\n"
        "- stop_conditions (array of strings): conditions that should block execution\n\n"
        f"TASK:\n{task}\n\n"
        f"LOCAL ANALYSIS:\n{local_analysis}\n\n"
        f"PROPOSED DECISION:\n{gpt_decision}\n"
    )


def ask_expert(task: str, local_analysis: str, gpt_decision: str, risk_level: str = "high", **_runtime_kwargs: Any) -> str:
    """Obtain the arbiter model's opinion on the proposed decision.

    This is deliberately advisory-only. The caller remains responsible for
    presenting the result to the user and obtaining approval before any change.
    """
    risk = str(risk_level or "").strip().lower()
    if risk not in {"high", "critical"}:
        return json.dumps({"status": "skipped", "reason": "risk_level_not_high_or_critical"}, ensure_ascii=False)

    task_text = _redact(_clip(task, _MAX_TASK))
    local_text = _redact(_clip(local_analysis, _MAX_ANALYSIS))
    decision_text = _redact(_clip(gpt_decision, _MAX_DECISION))

    try:
        arbiter_raw = _content(call_llm(
            provider="custom:cockpit-codex",
            model="gpt-5.6-sol",
            api_mode="chat_completions",
            messages=[{"role": "user", "content": _arbiter_prompt(task_text, local_text, decision_text)}],
            temperature=0,
            max_tokens=700,
        ))
    except Exception as exc:
        logger.warning("Arbiter advisory call failed: %s", exc)
        return json.dumps({"status": "unavailable", "stage": "arbiter", "error": str(exc)}, ensure_ascii=False)

    arbiter_json = _json_object(arbiter_raw)

    if arbiter_json is None:
        # Parse failure — treat as unable to confirm
        result = {
            "status": "parse_failed",
            "arbiter_raw": arbiter_raw,
            "decision": "manual_user_decision_required",
            "advisory_only": True,
            "note": "Arbiter response could not be parsed as JSON. Review manually.",
        }
        return _clip(json.dumps(result, ensure_ascii=False, indent=2), _MAX_RESPONSE)

    agrees = bool(arbiter_json.get("agrees") is True)

    if agrees:
        result = {
            "status": "agreed",
            "arbiter": arbiter_json,
            "gpt_decision": gpt_decision,
            "decision": "manual_user_decision_required",
            "advisory_only": True,
            "note": (
                "Arbiter agrees with the proposed decision. This is advisory only; "
                "the user must still approve any change or external action."
            ),
        }
    else:
        result = {
            "status": "rejected",
            "arbiter": arbiter_json,
            "gpt_decision": gpt_decision,
            "decision": "manual_user_decision_required",
            "advisory_only": True,
            "note": "Arbiter disagrees with the proposed decision. Do NOT proceed automatically. "
                    "Review the arbiter's reasoning manually.",
        }

    return _clip(json.dumps(result, ensure_ascii=False, indent=2), _MAX_RESPONSE)


registry.register(
    name="ask_expert",
    toolset=_TOOLSET,
    schema={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "High-risk decision to review."},
            "local_analysis": {"type": "string", "description": "Facts and analysis that led to the decision."},
            "gpt_decision": {"type": "string", "description": "The concrete decision that GPT has already made."},
            "risk_level": {"type": "string", "enum": ["high", "critical"], "description": "Risk gate; routine tasks are not eligible."},
        },
        "required": ["task", "local_analysis", "gpt_decision", "risk_level"],
        "additionalProperties": False,
    },
    handler=ask_expert,
    description=(
        "Advisory review for high-risk Hermes/VPS decisions: the background model makes "
        "a concrete decision, then asks the configured arbiter model to confirm or reject it."
    ),
    is_async=False,
)
