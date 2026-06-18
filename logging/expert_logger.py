"""JSONL logging for GPT-5.5 expert calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from hermes_constants import get_hermes_home

DEFAULT_LOG_PATH = get_hermes_home() / "logs" / "expert_calls.log"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_expert_call(
    *,
    reason: str,
    allowed: bool,
    context_length: int,
    summary_length: int,
    request_tokens: int,
    response_tokens: int,
    policy_result: str,
    quota_result: str,
    model: str = "gpt-5.5",
) -> dict[str, Any]:
    entry = {
        "timestamp": _utc_now(),
        "reason": reason,
        "allowed": allowed,
        "context_length": context_length,
        "summary_length": summary_length,
        "request_tokens": request_tokens,
        "response_tokens": response_tokens,
        "policy_result": policy_result,
        "quota_result": quota_result,
    }
    DEFAULT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry
