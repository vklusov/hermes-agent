from agent import native_skills as ns


def test_resolve_native_skill_known():
    assert ns.resolve_native_skill("bridge-agents") is not None
    assert ns.resolve_native_skill("routine-worker") is not None


def test_activate_auto_preloaded_native_skills_deduplicates(monkeypatch):
    called = {"bridge": 0, "routine": 0}

    def fake_activate(name, mode):
        def _inner():
            called[name] += 1
            return ns.NativeSkillActivation(
                name=name,
                mode=mode,
                detail=name,
            )
        return _inner

    monkeypatch.setattr(
        ns,
        "_NATIVE_SKILLS",
        {
            "bridge-agents": ns.NativeSkillSpec(
                name="bridge-agents",
                mode="bootstrap+prompt",
                activate=fake_activate("bridge", "bootstrap+prompt"),
            ),
            "routine-worker": ns.NativeSkillSpec(
                name="routine-worker",
                mode="tool+routing",
                activate=fake_activate("routine", "tool+routing"),
            ),
        },
    )

    activations = ns.activate_auto_preloaded_native_skills(
        ["bridge-agents", "routine-worker", "missing-skill", "bridge-agents", ""]
    )

    assert len(activations) == 2
    assert [a.mode for a in activations] == ["bootstrap+prompt", "tool+routing"]
    assert called["bridge"] == 1
    assert called["routine"] == 1


def test_activate_native_skill_unknown_returns_none():
    assert ns.activate_native_skill("missing-skill") is None


def test_routine_worker_native_activation_exposes_core_tool():
    ns.activate_auto_preloaded_native_skills(["routine-worker"])

    from model_tools import get_tool_definitions

    tool_names = [
        item["function"]["name"]
        for item in get_tool_definitions(
            enabled_toolsets=["hermes-cli"],
            quiet_mode=True,
            skip_tool_search_assembly=True,
        )
    ]
    assert "routine_worker" in tool_names


def test_format_native_skill_log_uses_standard_label():
    activations = [
        ns.NativeSkillActivation(
            name="bridge-agents",
            mode="bootstrap+prompt",
            detail="y",
        ),
        ns.NativeSkillActivation(
            name="routine-worker",
            mode="tool+routing",
            detail="z",
        ),
    ]
    assert (
        ns.format_native_skill_log(activations)
        == "bridge-agents:bootstrap+prompt, routine-worker:tool+routing"
    )
