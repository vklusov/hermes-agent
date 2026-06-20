"""Tests for native routine worker delegation tool."""

from __future__ import annotations

from tools.routine_worker import (
    build_routine_delegate_args,
    resolve_routine_route,
)
from toolsets import resolve_toolset
import model_tools


def test_marketplace_preset_splits_into_source_workers():
    delegate_args = build_routine_delegate_args({
        "task_type": "marketplace_research",
        "objective": "найти белую обувницу шириной до 55 см и глубиной от 30 см",
        "context": "Вернуть shortlist, не покупать и не логиниться.",
    })

    tasks = delegate_args["tasks"]
    assert len(tasks) == 3
    goals = [task["goal"] for task in tasks]
    assert any("Yandex Market worker" in goal for goal in goals)
    assert any("Wildberries worker" in goal for goal in goals)
    assert any("Search/Ozon fallback worker" in goal for goal in goals)
    assert all(task["role"] == "leaf" for task in tasks)
    assert all("Do not purchase, login, or modify external state" in task["context"] for task in tasks)
    # routing: marketplace -> minimax-m3 / neurogate-anthropic
    assert delegate_args.get("provider") == "custom:neurogate-anthropic"
    assert delegate_args.get("model") == "minimax-m3"


def test_web_research_strong_routes_to_qwen():
    delegate_args = build_routine_delegate_args({
        "task_type": "web_research_strong",
        "objective": "сравнить три источника",
    })
    assert delegate_args.get("provider") == "custom:neurogate-anthropic"
    assert delegate_args.get("model") == "qwen3.7-plus"


def test_cheap_flash_routes_to_deepseek():
    delegate_args = build_routine_delegate_args({
        "task_type": "cheap_flash",
        "objective": "быстрый поиск",
    })
    assert delegate_args.get("provider") == "custom:neurogate-chat"
    assert delegate_args.get("model") == "deepseek-v4-flash"


def test_kb_triage_routes_to_gpt54mini():
    delegate_args = build_routine_delegate_args({
        "task_type": "kb_triage",
        "objective": "сверить Cockpit и roadmap",
        "background": True,
    })
    assert delegate_args.get("provider") == "custom:neurogate"
    assert delegate_args.get("model") == "gpt-5.4-mini"


def test_resolve_routine_route_falls_back_to_default():
    route = resolve_routine_route("single", config={})
    assert route.get("model") == "minimax-m3"


def test_resolve_routine_route_config_wins():
    cfg = {"default": {"provider": "custom:test", "model": "test-model"}}
    route = resolve_routine_route("unknown", config=cfg)
    assert route.get("model") == "test-model"


def test_resolve_routine_route_task_type_wins():
    cfg = {
        "default": {"provider": "custom:fallback", "model": "fallback"},
        "web_research": {"provider": "custom:web", "model": "web-model"},
    }
    route = resolve_routine_route("web_research", config=cfg)
    assert route.get("model") == "web-model"


def test_routine_worker_is_in_delegation_toolset_and_core_schema():
    assert "routine_worker" in resolve_toolset("delegation")
    assert "routine_worker" in resolve_toolset("hermes-cli")

    definitions = model_tools.get_tool_definitions(enabled_toolsets=["delegation"])
    names = {definition["function"]["name"] for definition in definitions}
    assert "delegate_task" in names
    assert "routine_worker" in names
