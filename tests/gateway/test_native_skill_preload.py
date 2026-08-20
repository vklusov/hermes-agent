def test_gateway_global_auto_preload_activates_native_skill(monkeypatch):
    import gateway.run as gateway_run

    calls = {}

    def fake_activate(skill_names):
        calls["skill_names"] = list(skill_names)
        return [object()]

    monkeypatch.setattr(
        gateway_run,
        "_gateway_activate_auto_preloaded_native_skills",
        fake_activate,
    )
    monkeypatch.setattr(
        gateway_run,
        "_gateway_format_native_skill_log",
        lambda activations: "ask-expert:tool",
    )

    loaded = gateway_run._gateway_prepare_auto_skill_names(
        {"skills": {"auto_preload": ["bridge-agents", "ask-expert"]}},
        event_auto_skill=["ask-expert", "topic-skill"],
        session_key="telegram:dm:1",
    )

    assert loaded == ["bridge-agents", "ask-expert", "topic-skill"]
    assert calls["skill_names"] == ["bridge-agents", "ask-expert", "topic-skill"]


def test_gateway_global_auto_preload_handles_missing_config():
    import gateway.run as gateway_run

    assert gateway_run._gateway_prepare_auto_skill_names(
        {}, event_auto_skill=None, session_key="telegram:dm:1"
    ) == []
