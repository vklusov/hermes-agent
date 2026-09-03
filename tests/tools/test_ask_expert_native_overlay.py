import json


def test_ask_expert_native_overlay_is_registered():
    import tools.ask_expert as ask_expert_module  # noqa: F401 - import registers tool
    from tools.registry import registry

    entry = registry.get_entry("ask_expert")

    assert entry is not None
    assert entry.toolset == "hermes-ask-expert"
    assert entry.schema["parameters"]["required"] == ["task"]
    assert entry.schema["parameters"]["properties"]["task"]["type"] == "string"


def test_ask_expert_native_activation_imports_tool():
    from agent.native_skills import activate_native_skill
    from tools.registry import registry

    activation = activate_native_skill("ask-expert")

    assert activation is not None
    assert activation.name == "ask-expert"
    assert activation.mode == "tool"
    assert registry.get_entry("ask_expert") is not None


def test_registry_dispatch_accepts_runtime_kwargs(monkeypatch):
    import tools.ask_expert as ask_expert_module
    from tools.registry import registry

    captured = {}

    def fake_ask_expert(task):
        captured["task"] = task
        return "expert answer"

    monkeypatch.setattr(ask_expert_module, "ask_expert", fake_ask_expert)

    result = registry.dispatch(
        "ask_expert",
        {"task": "Need update gate advice"},
        task_id="cron-update-gate",
        session_id="cron-session",
        enabled_tools={"ask_expert"},
    )

    assert result == "expert answer"
    assert captured == {"task": "Need update gate advice"}


def test_registry_dispatch_reports_missing_task():
    import tools.ask_expert  # noqa: F401 - import registers tool
    from tools.registry import registry

    result = registry.dispatch(
        "ask_expert",
        {},
        task_id="cron-update-gate",
        session_id="cron-session",
        enabled_tools={"ask_expert"},
    )

    assert json.loads(result) == {
        "status": "error",
        "reason": "missing required string field: task",
    }


def test_ask_expert_route_is_anymodel_sonnet():
    import tools.ask_expert as ask_expert_module

    assert ask_expert_module.EXPERT_PROVIDER == "custom:anymodel"
    assert ask_expert_module.EXPERT_MODEL == "cc/claude-sonnet-5"
    assert ask_expert_module.EXPERT_PROVIDER != "custom:cockpit-claude"
