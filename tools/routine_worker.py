"""Native routine-worker tool.

This tool gives the main agent an explicit, low-friction route for broad but
routine subtasks that should run on the configured delegation worker model
instead of consuming the main session model for every reasoning step.
"""

from __future__ import annotations

import json
from typing import Any

from tools.registry import registry, tool_error


ROUTINE_WORKER_SCHEMA = {
    "name": "routine_worker",
    "description": (
        "Dispatch routine, source-specific work to configured worker subagents "
        "(delegation.provider/model, e.g. gpt-5.4-mini) while the main agent "
        "stays as orchestrator. Use this by default for broad marketplace, "
        "travel, web research, and KB triage tasks where independent sources "
        "can be checked in parallel. The tool wraps delegate_task with safe "
        "presets; it does not perform external writes or purchases."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_type": {
                "type": "string",
                "enum": ["marketplace_research", "web_research", "kb_triage", "single"],
                "description": (
                    "Preset routing kind. marketplace_research splits into "
                    "Yandex Market, Wildberries, and search/Ozon visual fallback workers."
                ),
            },
            "objective": {
                "type": "string",
                "description": "Concrete task objective, including constraints and desired output.",
            },
            "context": {
                "type": "string",
                "description": "Relevant background, constraints, user preferences, and safety boundaries.",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional source names/URLs/marketplaces to target. For marketplace_research, "
                    "defaults to Yandex Market, Wildberries, and search/Ozon fallback."
                ),
            },
            "toolsets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional worker toolsets override. Defaults depend on task_type.",
            },
            "background": {
                "type": "boolean",
                "description": "Run a single worker in background. Batch presets ignore this and run synchronously.",
            },
        },
        "required": ["task_type", "objective"],
    },
}


_MARKETPLACE_DEFAULT_SOURCES = ["Yandex Market", "Wildberries", "Search/Ozon visual fallback"]


def _compact(value: Any) -> str:
    return str(value or "").strip()


def _worker_context(base_context: str, extra: str) -> str:
    parts = [
        "You are a routine worker spawned by the main Архивариус agent.",
        "Return a compact evidence-backed summary. Do not purchase, login, or modify external state.",
        "Label uncertain facts explicitly; distinguish verified fields from search snippets.",
    ]
    if base_context:
        parts.append(f"Parent context: {base_context}")
    if extra:
        parts.append(extra)
    return "\n".join(parts)


def build_routine_delegate_args(args: dict[str, Any]) -> dict[str, Any]:
    """Translate routine_worker args into delegate_task-compatible args."""
    task_type = _compact(args.get("task_type")) or "single"
    objective = _compact(args.get("objective"))
    context = _compact(args.get("context"))
    sources = [str(s).strip() for s in (args.get("sources") or []) if str(s).strip()]
    toolsets = args.get("toolsets")

    if not objective:
        raise ValueError("routine_worker requires a non-empty objective")

    if task_type == "marketplace_research":
        selected = sources or _MARKETPLACE_DEFAULT_SOURCES
        tasks = []
        for source in selected[:3]:
            source_l = source.lower()
            if "wild" in source_l or "wb" == source_l:
                goal = f"Wildberries worker: {objective}"
                extra = "Focus on Wildberries. Confirm dimensions, color/type, stock, price, delivery, and URL when possible."
                worker_toolsets = toolsets or ["web", "browser"]
            elif "yandex" in source_l or "market" in source_l or "янд" in source_l:
                goal = f"Yandex Market worker: {objective}"
                extra = "Focus on Yandex Market. Confirm product-card specs, availability, price, seller, and URL when possible."
                worker_toolsets = toolsets or ["web", "browser"]
            else:
                goal = f"Search/Ozon fallback worker: {objective}"
                extra = "Use web search and browser/visual fallback for Ozon or independent stores. Mark visual-only evidence clearly."
                worker_toolsets = toolsets or ["web", "browser"]
            tasks.append({
                "goal": goal,
                "context": _worker_context(context, extra),
                "toolsets": worker_toolsets,
                "role": "leaf",
            })
        return {"tasks": tasks, "background": False}

    if task_type == "web_research":
        selected = sources or ["official/source docs", "web search results", "secondary verification"]
        tasks = [
            {
                "goal": f"Research {source}: {objective}",
                "context": _worker_context(context, f"Focus only on source lane: {source}. Return URLs and quoted/grounded findings."),
                "toolsets": toolsets or ["web"],
                "role": "leaf",
            }
            for source in selected[:3]
        ]
        return {"tasks": tasks, "background": False}

    if task_type == "kb_triage":
        return {
            "goal": f"KB/source-of-truth triage worker: {objective}",
            "context": _worker_context(
                context,
                "Inspect the local KB only. Prefer search_files/read_file/session_search. Do not make external or infrastructure changes.",
            ),
            "toolsets": toolsets or ["file", "session_search"],
            "role": "leaf",
            "background": bool(args.get("background", False)),
        }

    return {
        "goal": f"Routine worker: {objective}",
        "context": _worker_context(context, "Keep the result concise and evidence-backed."),
        "toolsets": toolsets or ["web", "file"],
        "role": "leaf",
        "background": bool(args.get("background", False)),
    }


def routine_worker(**_: Any) -> str:
    """Registry fallback.

    The real execution path is agent-owned because it must call
    ``agent._dispatch_delegate_task`` with the parent agent context. If this
    function is reached, the tool was not routed through the agent runtime.
    """
    return tool_error("routine_worker requires agent runtime context; use from an active agent session.")


registry.register(
    name="routine_worker",
    toolset="delegation",
    schema=ROUTINE_WORKER_SCHEMA,
    handler=lambda args, **kw: routine_worker(**args),
    emoji="🧑‍🏭",
)
