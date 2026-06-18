"""Daily quota for GPT-5.5 expert calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hermes_constants import get_hermes_home

MAX_EXPERT_CALLS_PER_DAY = 5

_QUOTA_FILE = get_hermes_home() / "expert_quota.json"


@dataclass(frozen=True)
class QuotaStatus:
    allowed: bool
    reason: str
    count: int
    limit: int = MAX_EXPERT_CALLS_PER_DAY

    def __getitem__(self, key: str) -> Any:
        if key == "used":
            return self.count
        return getattr(self, key)


def _today_key() -> str:
    return datetime.now().date().isoformat()


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(_QUOTA_FILE.read_text())
    except FileNotFoundError:
        return {}
    except Exception:
        # Corrupt quota state should not block the system forever.
        return {}


def _save_state(state: dict[str, Any]) -> None:
    _QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _QUOTA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    tmp.replace(_QUOTA_FILE)


def _get_today_count(state: dict[str, Any]) -> int:
    if state.get("date") != _today_key():
        return 0
    try:
        return int(state.get("count", 0))
    except (TypeError, ValueError):
        return 0


def can_call_expert() -> bool:
    state = _load_state()
    return _get_today_count(state) < MAX_EXPERT_CALLS_PER_DAY


def register_expert_call() -> None:
    state = _load_state()
    today = _today_key()
    if state.get("date") != today:
        state = {"date": today, "count": 0}
    state["count"] = _get_today_count(state) + 1
    _save_state(state)


def quota_status() -> QuotaStatus:
    state = _load_state()
    count = _get_today_count(state)
    allowed = count < MAX_EXPERT_CALLS_PER_DAY
    if allowed:
        return QuotaStatus(allowed=True, reason="within quota", count=count)
    return QuotaStatus(allowed=False, reason="Expert quota exceeded", count=count)


def reset_quota_state() -> None:
    """Test helper: clear persisted quota state."""

    try:
        _QUOTA_FILE.unlink()
    except FileNotFoundError:
        pass
