"""Policy for routing simple routine work to the cheaper worker model.

This is intentionally the mirror image of expert routing: the routine worker is
only for bounded, low-risk tasks. Anything requiring architecture, security,
root-cause analysis, multi-step implementation, or external side effects should
stay with the main agent (GPT-5.5) unless explicitly delegated through another
native path.
"""

from __future__ import annotations

from typing import Iterable

try:
    from policy.expert_policy import should_call_expert
except Exception:  # pragma: no cover - import-order defensive fallback
    def should_call_expert(task: str, context_summary: str) -> bool:
        return False


_ROUTINE_KEYWORDS: tuple[str, ...] = (
    "summarize",
    "summary",
    "translate",
    "translation",
    "rewrite",
    "rephrase",
    "format",
    "proofread",
    "fix typo",
    "typo",
    "extract",
    "classify",
    "list",
    "bullet",
    "draft",
    "boilerplate",
    "template",
    "simple",
    "routine",
    "quick",
)

_HIGH_RISK_KEYWORDS: tuple[str, ...] = (
    "architecture",
    "security",
    "production",
    "incident",
    "root cause",
    "delete",
    "remove files",
    "deploy",
    "publish",
    "send email",
    "payment",
    "credentials",
    "secret",
    "token",
    "password",
)

_MAX_ROUTINE_CHARS = 6000


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def should_use_routine_worker(task: str, context_summary: str = "") -> bool:
    """Return True only for simple, bounded tasks suitable for GPT-5.4-mini."""

    task_text = _normalize(task)
    context_text = _normalize(context_summary)
    combined = f"{task_text} {context_text}".strip()
    if not combined:
        return False
    if len(combined) > _MAX_ROUTINE_CHARS:
        return False
    if _contains_any(combined, _HIGH_RISK_KEYWORDS):
        return False
    if should_call_expert(task, context_summary):
        return False
    return _contains_any(combined, _ROUTINE_KEYWORDS) or len(combined) < 1200
