"""Compact context builder for native ask_expert calls."""

from __future__ import annotations

from typing import Iterable

_MAX_SUMMARY_CHARS = 1200
_MAX_TASK_CHARS = 1200


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _extract_text(message: object) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return str(content)


def build_expert_context(conversation_history: Iterable[object], current_task: str) -> str:
    """Return a bounded prompt fragment; never forward full conversation history."""

    summary_bits: list[str] = []
    for idx, message in enumerate(conversation_history):
        if idx >= 12:
            break
        text = _truncate(_extract_text(message), 180)
        if text:
            summary_bits.append(text)

    summary = _truncate(" | ".join(summary_bits), _MAX_SUMMARY_CHARS)
    task = _truncate(current_task, _MAX_TASK_CHARS)
    return f"Summary: {summary}\nCurrent task: {task}"
