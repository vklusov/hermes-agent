"""Native routine-worker tool.

This tool gives the main agent an explicit, low-friction route for broad but
routine subtasks that should run on configured worker routes instead of
consuming the main session model for every reasoning step.
"""

from __future__ import annotations

from typing import Any
import re

from tools.registry import registry, tool_error


ROUTINE_WORKER_SCHEMA = {
    "name": "routine_worker",
    "description": (
        "Dispatch routine, source-specific work to configured worker subagents "
        "while the main agent stays as orchestrator. Use this by default for "
        "broad marketplace, travel, web research, and KB triage tasks where "
        "independent sources can be checked in parallel. The tool wraps "
        "delegate_task with safe presets and per-task model routing; it does "
        "not perform external writes or purchases."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_type": {
                "type": "string",
                "enum": [
                    "marketplace_research",
                    "web_research",
                    "web_research_strong",
                    "cheap_flash",
                    "kb_triage",
                    "single",
                ],
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

_DEFAULT_ROUTING: dict[str, dict[str, str]] = {
    "default": {"provider": "custom:neurogate-anthropic", "model": "minimax-m3"},
    "marketplace_research": {"provider": "custom:neurogate-anthropic", "model": "minimax-m3"},
    "web_research": {"provider": "custom:neurogate-anthropic", "model": "minimax-m3"},
    "web_research_strong": {"provider": "custom:neurogate-anthropic", "model": "qwen3.7-plus"},
    "cheap_flash": {"provider": "custom:neurogate-chat", "model": "deepseek-v4-flash"},
    "kb_triage": {"provider": "custom:neurogate", "model": "gpt-5.4-mini"},
}


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


_GPT55_BYPASS_RE = re.compile(
    r"(gpt\s*-?\s*5[\.,]?5|gpt55|гпт\s*-?\s*5[\.,]?5|"
    r"основн(?:ой|ая|ую)\s+модел|main\s+model|"
    r"без\s+(?:worker|воркер|деш[её]в|делегир)|"
    r"не\s+(?:используй|надо|нужно)?\s*(?:worker|воркер|делегир)|"
    r"на\s+дорог(?:ой|ую)\s+модел)",
    re.IGNORECASE,
)


def _latest_user_text(messages: list[dict[str, Any]] | None) -> str:
    if not messages:
        return ""
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        chunks.append(text)
                elif isinstance(item, str):
                    chunks.append(item)
            return "\n".join(chunks)
    return ""


def _web_tool_subject(function_name: str, function_args: dict[str, Any]) -> tuple[str, list[str]]:
    if function_name == "web_search":
        query = _compact(function_args.get("query") or function_args.get("q"))
        return query or "web search", [query] if query else []
    if function_name == "web_extract":
        raw_urls = function_args.get("urls") or function_args.get("url") or []
        if isinstance(raw_urls, str):
            urls = [raw_urls]
        else:
            urls = [str(url).strip() for url in raw_urls if str(url).strip()]
        subject = ", ".join(urls[:3]) if urls else "web extraction"
        return subject, urls[:3]
    return function_name, []


def should_autoroute_web_research(
    function_name: str,
    function_args: dict[str, Any],
    *,
    messages: list[dict[str, Any]] | None = None,
    agent: Any = None,
) -> bool:
    """Return True when a main-agent web call should become routine_worker.

    This is deliberately conservative about recursion: child/delegated agents do
    their own source collection directly. The parent can still opt into the main
    model by explicitly asking for GPT-5.5/main/no-worker search.
    """
    if function_name not in {"web_search", "web_extract"}:
        return False
    if not isinstance(function_args, dict) or function_args.get("routine_worker_bypass"):
        return False
    if getattr(agent, "_delegate_depth", 0) > 0:
        return False
    valid_tool_names = getattr(agent, "valid_tool_names", None)
    if valid_tool_names is not None and "routine_worker" not in valid_tool_names:
        return False
    user_text = _latest_user_text(messages)
    if _GPT55_BYPASS_RE.search(user_text):
        return False
    subject, _sources = _web_tool_subject(function_name, function_args)
    return bool(subject)


def build_web_research_autoroute_args(
    function_name: str,
    function_args: dict[str, Any],
    *,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build routine_worker args for an intercepted web_search/web_extract."""
    subject, sources = _web_tool_subject(function_name, function_args)
    user_text = _latest_user_text(messages)
    objective = (
        f"Research the user's request using web sources. Initial {function_name} target: {subject}."
    )
    context_parts = [
        "Auto-routed from a direct web tool call to routine_worker so routine web research runs on the configured cheaper worker model by default.",
        "Return a concise evidence-backed summary with source URLs and uncertainty labels.",
        "Do not login, purchase, send messages, or modify external state.",
    ]
    if user_text:
        context_parts.append(f"Original user request: {user_text}")
    return {
        "task_type": "web_research",
        "objective": objective,
        "context": "\n".join(context_parts),
        "sources": sources or [subject],
    }


def _load_routine_worker_config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("routine_worker") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def resolve_routine_route(task_type: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve provider/model for a routine-worker preset.

    Config wins, then built-in defaults. Returns only non-empty string values so
    callers can merge the result directly into delegate_task args.
    """
    cfg = config if config is not None else _load_routine_worker_config()
    task_type = _compact(task_type) or "single"

    route: dict[str, Any] = {}
    if isinstance(cfg, dict):
        candidate = cfg.get(task_type) or cfg.get("default")
        if isinstance(candidate, dict):
            route.update(candidate)

    if not route:
        route.update(_DEFAULT_ROUTING.get(task_type) or _DEFAULT_ROUTING["default"])

    resolved = {
        key: str(value).strip()
        for key, value in route.items()
        if key in {"provider", "model", "base_url", "api_key", "api_mode"}
        and isinstance(value, str)
        and value.strip()
    }

    fallback_chain = route.get("fallback_chain")
    if isinstance(fallback_chain, list) and fallback_chain:
        resolved["fallback_model"] = [
            {
                key: str(value).strip()
                for key, value in item.items()
                if key in {"provider", "model", "base_url", "api_key", "api_mode"}
                and isinstance(value, str)
                and value.strip()
            }
            for item in fallback_chain
            if isinstance(item, dict)
        ]
        resolved["fallback_model"] = [item for item in resolved["fallback_model"] if item.get("model")]
        if not resolved["fallback_model"]:
            resolved.pop("fallback_model", None)

    return resolved


def build_routine_delegate_args(args: dict[str, Any]) -> dict[str, Any]:
    """Translate routine_worker args into delegate_task-compatible args."""
    task_type = _compact(args.get("task_type")) or "single"
    objective = _compact(args.get("objective"))
    context = _compact(args.get("context"))
    sources = [str(s).strip() for s in (args.get("sources") or []) if str(s).strip()]
    toolsets = args.get("toolsets")

    if not objective:
        raise ValueError("routine_worker requires a non-empty objective")

    route = resolve_routine_route(task_type)

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
        return {"tasks": tasks, "background": False, **route}

    if task_type in {"web_research", "web_research_strong", "cheap_flash"}:
        selected = sources or ["official/source docs", "web search results", "secondary verification"]
        if len(selected) == 1:
            source = selected[0]
            return {
                "goal": f"Research {source}: {objective}",
                "context": _worker_context(context, f"Focus only on source lane: {source}. Return URLs and quoted/grounded findings."),
                "toolsets": toolsets or ["web"],
                "role": "leaf",
                "background": bool(args.get("background", False)),
                **route,
            }
        tasks = [
            {
                "goal": f"Research {source}: {objective}",
                "context": _worker_context(context, f"Focus only on source lane: {source}. Return URLs and quoted/grounded findings."),
                "toolsets": toolsets or ["web"],
                "role": "leaf",
            }
            for source in selected[:3]
        ]
        return {"tasks": tasks, "background": False, **route}

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
            **route,
        }

    return {
        "goal": f"Routine worker: {objective}",
        "context": _worker_context(context, "Keep the result concise and evidence-backed."),
        "toolsets": toolsets or ["web", "file"],
        "role": "leaf",
        "background": bool(args.get("background", False)),
        **route,
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
