"""Conservative policy gate for native ask_expert."""

from __future__ import annotations

from typing import Iterable

_EXPERT_KEYWORDS: tuple[str, ...] = (
    "architecture",
    "architectural",
    "security",
    "design",
    "distributed system",
    "multi-agent",
    "multi agent",
    "production incident",
    "incident response",
    "complex refactor",
    "migration",
    "upgrade",
    "fleet",
    "rollout",
    "routing",
    "trade-off",
    "tradeoffs",
    "compare approaches",
    "root cause",
    "failure mode",
)

_ROUTINE_KEYWORDS: tuple[str, ...] = (
    "translate",
    "translation",
    "small talk",
    "greeting",
    "template",
    "boilerplate",
    "rename",
    "format",
    "fix typo",
    "typo",
)


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def should_call_expert(task: str, context_summary: str) -> bool:
    """Return True only for tasks where advisory Sonnet escalation is justified."""

    combined = f"{_normalize(task)} {_normalize(context_summary)}".strip()
    if not combined:
        return False
    if _contains_any(combined, _ROUTINE_KEYWORDS):
        return False
    return _contains_any(combined, _EXPERT_KEYWORDS)
