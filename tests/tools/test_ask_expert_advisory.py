import json
import types

import tools.ask_expert as module


class _Response:
    def __init__(self, content):
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]


def test_low_risk_is_skipped_without_provider_call(monkeypatch):
    calls = []
    monkeypatch.setattr(module, "call_llm", lambda **kwargs: calls.append(kwargs))
    result = json.loads(module.ask_expert("routine", "looks safe", "update now", "medium"))
    assert result["status"] == "skipped"
    assert calls == []


def test_arbiter_agreement_returns_advisory_agreed(monkeypatch):
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return _Response('{"agrees": true, "verdict": "safe to proceed", "confidence": 0.9, "risks": [], "checks_before_change": [], "stop_conditions": []}')

    monkeypatch.setattr(module, "call_llm", fake_call_llm)
    result = json.loads(module.ask_expert("update Hermes", "backup exists, clean checkout", "update staging and production", "high"))
    assert result["status"] == "agreed"
    assert result["decision"] == "manual_user_decision_required"
    assert result["advisory_only"] is True
    assert result["gpt_decision"] == "update staging and production"
    assert len(calls) == 1
    assert calls[0]["provider"] == "custom:cockpit-codex"
    assert calls[0]["model"] == "gpt-5.6-sol"
    assert calls[0]["api_mode"] == "chat_completions"


def test_arbiter_disagreement_returns_rejected(monkeypatch):
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "gpt-5.6-sol":
            return _Response('{"agrees": false, "verdict": "dirty checkout detected, do not update", "confidence": 0.95, "risks": ["local changes may be lost"], "checks_before_change": [], "stop_conditions": ["dirty git status"]}')
        raise AssertionError(f"Unexpected arbiter model: {kwargs['model']}")

    monkeypatch.setattr(module, "call_llm", fake_call_llm)
    result = json.loads(module.ask_expert("update Hermes", "git status shows modified files", "update staging and production", "critical"))
    assert result["status"] == "rejected"
    assert result["decision"] == "manual_user_decision_required"
    assert result["advisory_only"] is True
    assert result["gpt_decision"] == "update staging and production"
    assert "Arbiter disagrees" in result["note"]
    assert [call["model"] for call in calls] == ["gpt-5.6-sol"]


def test_arbiter_unavailable_returns_error(monkeypatch):
    def fake_call_llm(**kwargs):
        raise RuntimeError("Connection timeout")

    monkeypatch.setattr(module, "call_llm", fake_call_llm)
    result = json.loads(module.ask_expert("update Hermes", "all checks passed", "update both", "high"))
    assert result["status"] == "unavailable"
    assert result["stage"] == "arbiter"
    assert "Connection timeout" in result["error"]


def test_parse_failure_returns_parse_failed(monkeypatch):
    def fake_call_llm(**kwargs):
        return _Response("not valid json at all")

    monkeypatch.setattr(module, "call_llm", fake_call_llm)
    result = json.loads(module.ask_expert("update Hermes", "checks passed", "update", "high"))
    assert result["status"] == "parse_failed"
    assert result["decision"] == "manual_user_decision_required"
    assert result["advisory_only"] is True


def test_credentials_are_redacted_before_external_call(monkeypatch):
    captured = {}

    def fake_call_llm(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return _Response('{"agrees": true, "verdict": "safe", "confidence": 1, "risks": [], "checks_before_change": [], "stop_conditions": []}')

    monkeypatch.setattr(module, "call_llm", fake_call_llm)
    module.ask_expert("rotate api_key=secret-value", "password=hunter2 in logs", "proceed with rotation", "high")
    assert "secret-value" not in captured["prompt"]
    assert "hunter2" not in captured["prompt"]
    assert "[REDACTED]" in captured["prompt"]
