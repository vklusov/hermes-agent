"""Profile-safe quota for native ask_expert calls."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from hermes_constants import get_hermes_home

MAX_EXPERT_CALLS_PER_DAY = 5


def _configured_daily_limit() -> int:
    """Return the configured daily expert-call limit, falling back to 5.

    User-facing behavioral settings belong in config.yaml, not .env. Keep this
    read-only and best-effort so a broken config cannot disable the safety gate.
    """
    try:
        from hermes_cli.config import cfg_get, load_config_readonly

        value = cfg_get(
            load_config_readonly(),
            "expert",
            "max_calls_per_day",
            default=MAX_EXPERT_CALLS_PER_DAY,
        )
        limit = int(value)
    except Exception:
        return MAX_EXPERT_CALLS_PER_DAY
    if limit < 1:
        return MAX_EXPERT_CALLS_PER_DAY
    return limit


def _quota_file():
    return get_hermes_home() / "expert_quota.json"


def _today_key() -> str:
    return datetime.now().date().isoformat()


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(_quota_file().read_text())
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = _quota_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    tmp.replace(path)


def _today_count(state: dict[str, Any]) -> int:
    if state.get("date") != _today_key():
        return 0
    try:
        return int(state.get("count", 0))
    except (TypeError, ValueError):
        return 0


def can_call_expert() -> bool:
    return _today_count(_load_state()) < _configured_daily_limit()


def register_expert_call() -> None:
    state = _load_state()
    today = _today_key()
    if state.get("date") != today:
        state = {"date": today, "count": 0}
    state["count"] = _today_count(state) + 1
    _save_state(state)
