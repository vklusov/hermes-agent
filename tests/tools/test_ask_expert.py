import json
import types



def test_expert_quota_limit_defaults_to_five_when_config_missing(monkeypatch):
    from quota import expert_quota

    monkeypatch.setattr(expert_quota, "_load_state", lambda: {"date": expert_quota._today_key(), "count": 4})
    monkeypatch.setattr(expert_quota, "_configured_daily_limit", lambda: 5)

    assert expert_quota.can_call_expert() is True

    monkeypatch.setattr(expert_quota, "_load_state", lambda: {"date": expert_quota._today_key(), "count": 5})

    assert expert_quota.can_call_expert() is False


def test_expert_quota_limit_can_be_overridden_by_config(monkeypatch):
    from quota import expert_quota

    monkeypatch.setattr(expert_quota, "_load_state", lambda: {"date": expert_quota._today_key(), "count": 9})
    monkeypatch.setattr(expert_quota, "_configured_daily_limit", lambda: 10)

    assert expert_quota.can_call_expert() is True

    monkeypatch.setattr(expert_quota, "_load_state", lambda: {"date": expert_quota._today_key(), "count": 10})

    assert expert_quota.can_call_expert() is False


def test_tool_is_registered_for_native_skill_startup():
    import tools.ask_expert as ask_expert_module
    from tools.registry import registry

    entry = registry.get_entry("ask_expert")

    assert entry is not None
    assert entry.toolset == "hermes-ask-expert"
    assert entry.handler is ask_expert_module._handle_ask_expert
    assert entry.schema["parameters"]["required"] == ["task"]
    assert entry.schema["parameters"]["properties"]["task"]["type"] == "string"


def test_ask_expert_routes_only_to_anymodel_cockpit_sonnet(monkeypatch):
    import tools.ask_expert as ask_expert_module

    monkeypatch.setattr(ask_expert_module, "should_call_expert", lambda task, context: True)
    monkeypatch.setattr(ask_expert_module, "can_call_expert", lambda: True)
    monkeypatch.setattr(ask_expert_module, "register_expert_call", lambda: None)
    monkeypatch.setattr(ask_expert_module, "log_expert_call", lambda **kwargs: None)
    monkeypatch.setattr(
        ask_expert_module,
        "build_expert_context",
        lambda history, task: "Summary: compact facts\nCurrent task: " + task,
    )

    captured = {}

    class DummyResponse:
        choices = [types.SimpleNamespace(message=types.SimpleNamespace(content="sonnet advice"))]
        usage = types.SimpleNamespace(prompt_tokens=17, completion_tokens=23)

    def fake_call_llm(*, provider=None, messages, model, **kwargs):
        captured.update(provider=provider, model=model, messages=messages, kwargs=kwargs)
        return DummyResponse()

    monkeypatch.setattr(ask_expert_module, "call_llm", fake_call_llm)

    result = ask_expert_module.ask_expert("Review a high-risk Hermes rollout")

    assert result == "sonnet advice"
    assert captured["provider"] == "custom:anymodel"
    assert captured["model"] == "cc/claude-sonnet-5"
    assert captured["provider"] not in {"auto", "anthropic", "custom:neurogate", "custom:cockpit-claude"}
    assert captured["model"] != "gpt-5.5"
    assert [message["role"] for message in captured["messages"]] == ["system", "user"]
    prompt = captured["messages"][1]["content"]
    assert "Summary: compact facts" in prompt
    assert "Review a high-risk Hermes rollout" in prompt
    assert "full conversation" not in prompt.lower()


def test_policy_allows_expert_topics_even_with_routine_words():
    from policy.expert_policy import should_call_expert

    task = (
        "Design automation architecture for Hermes fleet maintenance. "
        "Include implementation patterns, alert templates, rollout tradeoffs, "
        "and production incident failure modes."
    )

    assert should_call_expert(task, "") is True


def test_policy_still_rejects_routine_only_tasks():
    from policy.expert_policy import should_call_expert

    assert should_call_expert("Translate and format this short template", "") is False


def test_ask_expert_blocks_when_policy_rejects(monkeypatch):
    import tools.ask_expert as ask_expert_module

    monkeypatch.setattr(ask_expert_module, "should_call_expert", lambda task, context: False)
    monkeypatch.setattr(ask_expert_module, "can_call_expert", lambda: True)
    monkeypatch.setattr(ask_expert_module, "log_expert_call", lambda **kwargs: None)
    monkeypatch.setattr(
        ask_expert_module,
        "call_llm",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not call expert")),
    )

    result = json.loads(ask_expert_module.ask_expert("Translate this short sentence"))

    assert result["status"] == "skipped"
    assert result["reason"] == "policy rejected"


def test_registry_dispatch_invokes_ask_expert_handler(monkeypatch):
    import tools.ask_expert as ask_expert_module
    from tools.registry import registry

    captured = {}

    def fake_ask_expert(task):
        captured["task"] = task
        return "dispatched answer"

    monkeypatch.setattr(ask_expert_module, "ask_expert", fake_ask_expert)

    result = registry.dispatch(
        "ask_expert",
        {"task": "Need Sonnet advice"},
        task_id="cron-update-gate",
        session_id="session-1",
        enabled_tools={"ask_expert"},
    )

    assert result == "dispatched answer"
    assert captured["task"] == "Need Sonnet advice"


def test_registry_dispatch_reports_missing_ask_expert_task():
    import tools.ask_expert  # noqa: F401 - registers the native tool
    from tools.registry import registry

    result_text = registry.dispatch(
        "ask_expert",
        {},
        task_id="cron-update-gate",
        session_id="session-1",
        enabled_tools={"ask_expert"},
    )
    assert isinstance(result_text, str)
    result = json.loads(result_text)

    assert result == {
        "status": "error",
        "reason": "missing required string field: task",
    }
