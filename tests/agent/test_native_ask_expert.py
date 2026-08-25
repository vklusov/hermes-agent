def test_native_bridge_activation_writes_runtime_context(tmp_path, monkeypatch):
    from agent import native_skills as ns
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(tmp_path)
    monkeypatch.delenv("HERMES_HOME", raising=False)

    try:
        activation = ns.activate_native_skill("bridge-agents")
    finally:
        reset_hermes_home_override(token)

    assert activation is not None
    assert activation.name == "bridge-agents"
    assert activation.mode == "bootstrap+prompt"
    context_path = tmp_path / ".codex" / "session-bridge.md"
    content = context_path.read_text(encoding="utf-8")
    assert "http://100.92.229.56:8001/health" in content
    assert "http://192.168.1.93:8002/task" in content
    assert "API-Key" not in content


def test_native_ask_expert_activation_exposes_core_tool():
    from agent import native_skills as ns
    from model_tools import get_tool_definitions

    activation = ns.activate_native_skill("ask-expert")

    assert activation is not None
    assert activation.name == "ask-expert"
    assert activation.mode == "tool"
    assert "custom:anymodel/cc/claude-sonnet-5" in activation.detail

    tool_defs = get_tool_definitions(
        enabled_toolsets=["hermes-cli"],
        quiet_mode=True,
        skip_tool_search_assembly=True,
    )
    ask_expert_def = next(
        item for item in tool_defs if item["function"]["name"] == "ask_expert"
    )
    parameters = ask_expert_def["function"]["parameters"]
    assert parameters["required"] == ["task"]
    assert parameters["properties"]["task"]["type"] == "string"
    assert "function" not in ask_expert_def["function"]


def test_native_skill_activation_deduplicates_and_logs_label():
    from agent import native_skills as ns

    activations = ns.activate_auto_preloaded_native_skills(
        ["bridge-agents", "ask-expert", "missing-skill", "ask-expert", "bridge-agents", ""]
    )

    assert [(item.name, item.mode) for item in activations] == [
        ("bridge-agents", "bootstrap+prompt"),
        ("ask-expert", "tool"),
    ]
    assert ns.format_native_skill_log(activations) == "bridge-agents:bootstrap+prompt,ask-expert:tool"
