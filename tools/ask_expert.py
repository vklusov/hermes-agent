"""Native advisory expert tool backed by AnyModel Sonnet."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Iterable

from agent.auxiliary_client import call_llm
from context.expert_context import build_expert_context
from policy.expert_policy import should_call_expert
from quota.expert_quota import can_call_expert, register_expert_call
from tools.registry import registry

EXPERT_PROVIDER = "custom:anymodel"
EXPERT_MODEL = "claude-sonnet-5"
EXPERT_ROUTE_LABEL = f"{EXPERT_PROVIDER}/{EXPERT_MODEL}"


def _load_expert_logger():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "logging" / "expert_logger.py"
    spec = importlib.util.spec_from_file_location("hermes_expert_logger", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load expert logger from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


log_expert_call = _load_expert_logger().log_expert_call


def _usage_value(usage: Any, name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        value = usage.get(name) or 0
    else:
        value = getattr(usage, name, 0) or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content is not None:
            return str(content)
    output_text = getattr(response, "output_text", None)
    if output_text is not None:
        return str(output_text)
    return str(response)


def _skip(reason: str, *, context_length: int = 0, summary_length: int = 0) -> str:
    log_expert_call(
        reason=reason,
        allowed=False,
        context_length=context_length,
        summary_length=summary_length,
        request_tokens=0,
        response_tokens=0,
        policy_result="rejected" if reason == "policy rejected" else "not_evaluated",
        quota_result="rejected" if reason == "quota exceeded" else "not_evaluated",
        provider=EXPERT_PROVIDER,
        model=EXPERT_MODEL,
    )
    return json.dumps({"status": "skipped", "reason": reason}, ensure_ascii=False)


def ask_expert(task: str, conversation_history: Iterable[object] | None = None) -> str:
    """Ask Sonnet for advisory-only technical review through the explicit route."""

    context_summary = build_expert_context(conversation_history or [], task)
    context_length = len(context_summary)
    summary_length = len(context_summary)

    if not should_call_expert(task, context_summary):
        return _skip("policy rejected", context_length=context_length, summary_length=summary_length)
    if not can_call_expert():
        return _skip("quota exceeded", context_length=context_length, summary_length=summary_length)

    messages = [
        {
            "role": "system",
            "content": (
                "You are an advisory expert reviewer. Return concise, grounded guidance. "
                "Do not authorize external actions; the operator must verify locally."
            ),
        },
        {
            "role": "user",
            "content": context_summary,
        },
    ]
    response = call_llm(
        task="ask_expert",
        provider=EXPERT_PROVIDER,
        model=EXPERT_MODEL,
        messages=messages,
        temperature=0.2,
        max_tokens=1800,
        route_info={"tool": "ask_expert", "route": EXPERT_ROUTE_LABEL},
    )
    register_expert_call()
    usage = getattr(response, "usage", None)
    log_expert_call(
        reason="expert_advisory",
        allowed=True,
        context_length=context_length,
        summary_length=summary_length,
        request_tokens=_usage_value(usage, "prompt_tokens"),
        response_tokens=_usage_value(usage, "completion_tokens"),
        policy_result="accepted",
        quota_result="accepted",
        provider=EXPERT_PROVIDER,
        model=EXPERT_MODEL,
    )
    return _response_text(response)


def _handle_ask_expert(args: dict[str, Any], **_kwargs: Any) -> str:
    task = args.get("task") if isinstance(args, dict) else None
    if not isinstance(task, str) or not task.strip():
        return json.dumps(
            {"status": "error", "reason": "missing required string field: task"},
            ensure_ascii=False,
        )
    return ask_expert(task)


ASK_EXPERT_SCHEMA = {
    "name": "ask_expert",
    "description": (
        "Ask the configured expert model for advisory-only technical review. "
        "Routes explicitly to custom:anymodel / claude-sonnet-5."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Specific technical or architectural question for expert review.",
            }
        },
        "required": ["task"],
    },
}

registry.register(
    name="ask_expert",
    toolset="hermes-ask-expert",
    schema=ASK_EXPERT_SCHEMA,
    handler=_handle_ask_expert,
    description="Ask AnyModel Sonnet for advisory-only expert review.",
    emoji="",
)
