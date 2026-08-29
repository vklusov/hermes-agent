"""Regression tests for native skill activation in cron jobs.

A cron job can attach prompt skills and per-job toolsets, but native skills also
need executable activation before AIAgent builds its tool schema. The update
maintenance job relies on `ask-expert` registering `ask_expert`; merely injecting
SKILL.md into the prompt is not enough.
"""

from __future__ import annotations

from unittest.mock import patch


def test_cron_run_activates_job_native_skills_before_agent(monkeypatch):
    from cron import scheduler

    job = {
        "id": "native-job",
        "name": "native-job",
        "prompt": "verify native tools",
        "schedule_display": "manual",
        "skills": ["ask-expert"],
        "enabled_toolsets": ["terminal", "hermes-ask-expert"],
        "provider": "custom:test",
        "model": "test-model",
        "deliver": "local",
    }

    activations_seen = []
    agent_kwargs = {}

    class DummyAgent:
        def __init__(self, **kwargs):
            agent_kwargs.update(kwargs)

        def run_conversation(self, prompt, auto_confirm=False):
            assert activations_seen == [["ask-expert"]]
            return {
                "completed": True,
                "failed": False,
                "final_response": "ok",
                "messages": [],
                "turn_exit_reason": "text_response",
            }

        def get_activity_summary(self):
            return {"seconds_since_activity": 0}

    monkeypatch.setenv("HERMES_CRON_TIMEOUT", "0")

    with patch("cron.scheduler._build_job_prompt", return_value="prompt"), \
         patch("cron.scheduler._resolve_delivery_target", return_value=None), \
         patch("cron.scheduler._preflight_job_config", return_value=None), \
         patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value={
             "provider": "custom:test",
             "api_key": "test-key",
             "base_url": "https://example.invalid/v1",
             "requested_provider": "custom:test",
             "api_mode": "openai",
         }), \
         patch("run_agent.AIAgent", DummyAgent), \
         patch("cron.scheduler._activate_cron_native_skills", side_effect=lambda j: activations_seen.append(j.get("skills", []))), \
         patch("tools.mcp_tool.discover_mcp_tools", return_value=[]):
        ok, _output, response, error = scheduler.run_job(job)

    assert ok is True
    assert response == "ok"
    assert error is None
    assert agent_kwargs["enabled_toolsets"] == ["terminal", "hermes-ask-expert"]
