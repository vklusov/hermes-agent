"""Ask GPT-5.5 for rare expert-level help.

This tool is intentionally conservative: it checks policy and quota first, keeps
context compact, and never forwards full conversation history.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import util as importlib_util
from pathlib import Path
from typing import Any, Iterable

from agent.auxiliary_client import call_llm
from context.expert_context import build_expert_context
from policy.expert_policy import should_call_expert
from quota.expert_quota import can_call_expert, register_expert_call

_TOOLSET = "hermes-ask-expert"
_TOOL_NAME = "ask_expert"
_EXPERT_PROVIDER = "custom:neurogate"
_EXPERT_MODEL = "gpt-5.5"
_LOGGER_PATH = Path(__file__).resolve().parents[1] / "logging" / "expert_logger.py"


def _load_expert_logger():
    """Load project logging/expert_logger.py without shadowing stdlib logging."""

    spec = importlib_util.spec_from_file_location("hermes_expert_logger", _LOGGER_PATH)
    module = importlib_util.module_from_spec(spec) if spec and spec.loader else None
    if spec and spec.loader and module is not None:
        spec.loader.exec_module(module)
        return getattr(module, "log_expert_call")

    def _noop(**_: Any) -> None:
        return None

    return _noop


log_expert_call = _load_expert_logger()


@dataclass(frozen=True)
class ExpertDecision:
    allowed: bool
    reason: str
    policy_result: str
    quota_result: str


def _build_summary(task: str, history: Iterable[object] | None = None) -> tuple[str, str]:
    history = list(history or [])
    context = build_expert_context(history, task)
    summary = context.split("\n", 1)[0].removeprefix("Summary: ").strip()
    return context, summary


def _decide(task: str, context_summary: str) -> ExpertDecision:
    policy_ok = should_call_expert(task, context_summary)
    if not policy_ok:
        return ExpertDecision(
            allowed=False,
            reason="policy rejected",
            policy_result="rejected",
            quota_result="not_checked",
        )

    if not can_call_expert():
        return ExpertDecision(
            allowed=False,
            reason="Expert quota exceeded",
            policy_result="allowed",
            quota_result="exceeded",
        )

    return ExpertDecision(
        allowed=True,
        reason="allowed",
        policy_result="allowed",
        quota_result="within_quota",
    )


def _extract_response_text(response: Any) -> str:
    message = getattr(getattr(response, "choices", [None])[0], "message", None)
    if message is None:
        return str(response)
    return str(getattr(message, "content", str(message)) or "")


def ask_expert(task: str) -> str:
    """Route a task to GPT-5.5 only when policy and quota allow it."""

    context_text, summary = _build_summary(task, history=[])
    decision = _decide(task, summary)

    if not decision.allowed:
        log_expert_call(
            reason=decision.reason,
            allowed=False,
            context_length=len(context_text),
            summary_length=len(summary),
            request_tokens=0,
            response_tokens=0,
            policy_result=decision.policy_result,
            quota_result=decision.quota_result,
            model=_EXPERT_MODEL,
        )
        return decision.reason if decision.reason == "Expert quota exceeded" else "Expert request rejected by policy"

    prompt = (
        "You are GPT-5.5, an expert reasoning model inside Hermes.\n"
        "Answer concisely but with expert depth.\n\n"
        f"{context_text}"
    )

    response = call_llm(
        provider=_EXPERT_PROVIDER,
        messages=[{"role": "user", "content": prompt}],
        model=_EXPERT_MODEL,
    )

    result_text = _extract_response_text(response)
    register_expert_call()

    request_tokens = int(getattr(getattr(response, "usage", None), "prompt_tokens", 0) or 0)
    response_tokens = int(getattr(getattr(response, "usage", None), "completion_tokens", 0) or 0)

    log_expert_call(
        reason="expert_call",
        allowed=True,
        context_length=len(context_text),
        summary_length=len(summary),
        request_tokens=request_tokens,
        response_tokens=response_tokens,
        policy_result=decision.policy_result,
        quota_result=decision.quota_result,
        model=_EXPERT_MODEL,
    )

    return result_text


def _handle_ask_expert(task: str, **_: Any) -> str:
    return ask_expert(task)


try:
    from tools.registry import registry

    registry.register(
        name=_TOOL_NAME,
        toolset=_TOOLSET,
        schema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task to analyze with GPT-5.5."},
            },
            "required": ["task"],
            "additionalProperties": False,
        },
        handler=_handle_ask_expert,
        description="Escalate rare expert-level tasks to GPT-5.5 after policy and quota checks.",
        is_async=False,
    )
except Exception:
    # Import order may load this module before the registry is fully ready.
    pass
