"""Policy for routing rare expert-level tasks to GPT-5.5.

This module intentionally stays conservative: it should only approve calls
when the task clearly benefits from deep reasoning. Routine chat, translation,
short questions, template code, and simple implementation work should stay on
the default model.
"""

from __future__ import annotations

from typing import Iterable


# Positive signals: tasks where a stronger model is justified.
_EXPERT_KEYWORDS: tuple[str, ...] = (
    "architecture",
    "architectural",
    "security",
    "secure",
    "design",
    "distributed system",
    "distributed systems",
    "multi-agent",
    "multi agent",
    "production incident",
    "incident response",
    "complex refactor",
    "refactoring",
    "multiple options",
    "trade-off",
    "tradeoffs",
    "compare approaches",
    "deep analysis",
    "root cause",
    "systems design",
)

# Negative signals: routine work that should not trigger expert mode.
_ROUTINE_KEYWORDS: tuple[str, ...] = (
    "translate",
    "translation",
    "chat",
    "small talk",
    "greeting",
    "simple code",
    "template",
    "boilerplate",
    "write a function",
    "rename",
    "format",
    "short question",
    "quick question",
    "routine",
    "todo",
    "fix typo",
    "typo",
    "lint",
    "documentation",
)

_MIN_ANALYSIS_LENGTH = 120


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def should_call_expert(task: str, context_summary: str) -> bool:
    """Return True only when GPT-5.5 is warranted.

    The policy is deliberately biased toward *not* escalating. A task must
    contain a strong expert signal and must not look routine.
    """

    task_text = _normalize(task)
    context_text = _normalize(context_summary)
    combined = f"{task_text} {context_text}".strip()

    if not combined:
        return False

    # Explicitly block obviously routine requests.
    if _contains_any(combined, _ROUTINE_KEYWORDS):
        return False

    expert_signal = _contains_any(combined, _EXPERT_KEYWORDS)

    # Short prompts should not escalate unless they strongly match an expert topic.
    if len(combined) < _MIN_ANALYSIS_LENGTH and not expert_signal:
        return False

    # Encourage expert mode for tasks that ask for analysis of alternatives or
    # indicate complexity, even if the keyword list is sparse.
    complexity_hints = (
        "compare",
        "evaluate",
        "trade-off",
        "tradeoffs",
        "options",
        "alternatives",
        "what if",
        "risk",
        "failure mode",
        "root cause",
        "production",
        "incident",
        "security review",
        "system design",
    )
    complex_request = _contains_any(combined, complexity_hints)

    return expert_signal or complex_request
