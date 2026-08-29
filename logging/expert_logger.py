"""JSONL audit logging for native ask_expert calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from hermes_constants import get_hermes_home


def _log_path():
    return get_hermes_home() / "logs" / "expert_calls.log"


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
    provider: str,
    model: str,
) -> dict[str, Any]:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "allowed": allowed,
        "context_length": context_length,
        "summary_length": summary_length,
        "request_tokens": request_tokens,
        "response_tokens": response_tokens,
        "policy_result": policy_result,
        "quota_result": quota_result,
        "provider": provider,
        "model": model,
    }
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry
