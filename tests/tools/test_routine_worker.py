from __future__ import annotations

from tools.routine_worker import build_routine_delegate_args
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
    assert delegate_args["background"] is False
    goals = [task["goal"] for task in tasks]
    assert any("Yandex Market worker" in goal for goal in goals)
    assert any("Wildberries worker" in goal for goal in goals)
    assert any("Search/Ozon fallback worker" in goal for goal in goals)
    assert all(task["role"] == "leaf" for task in tasks)
    assert all(task["toolsets"] == ["web", "browser"] for task in tasks)
    assert all("Do not purchase, login, or modify external state" in task["context"] for task in tasks)


def test_kb_triage_preset_uses_file_and_session_search_tools():
    delegate_args = build_routine_delegate_args({
        "task_type": "kb_triage",
        "objective": "сверить Cockpit и roadmap",
        "background": True,
    })

    assert delegate_args["goal"].startswith("KB/source-of-truth triage worker")
    assert delegate_args["toolsets"] == ["file", "session_search"]
    assert delegate_args["background"] is True
    assert "local KB only" in delegate_args["context"]


def test_routine_worker_is_in_delegation_toolset_and_core_schema():
    assert "routine_worker" in resolve_toolset("delegation")
    assert "routine_worker" in resolve_toolset("hermes-cli")

    definitions = model_tools.get_tool_definitions(enabled_toolsets=["delegation"])
    names = {definition["function"]["name"] for definition in definitions}
    assert "delegate_task" in names
    assert "routine_worker" in names
