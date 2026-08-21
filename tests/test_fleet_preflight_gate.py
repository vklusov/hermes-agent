import json

import model_tools
import agent.fleet_preflight as fleet_preflight
from agent.fleet_preflight import PREFLIGHT_MARKER_ENV


def _decode(result):
    return json.loads(result)


def test_fleet_restart_blocks_without_preflight(monkeypatch):
    called = False

    def fake_dispatch(*_args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "terminal",
            {"command": "hermes gateway restart"},
            user_task="Hermes fleet rollout on Archivarius",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is False
    assert result["error_type"] == "fleet_preflight_required"
    assert result["classified_fleet_context"] is True
    assert "policy_stage" in result["error"]


def test_fleet_read_only_audit_stays_allowed(monkeypatch):
    called = False

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name, "args": args})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "terminal",
            {"command": "git status --short --branch"},
            user_task="Hermes fleet read-only audit on Fedor",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True


def test_policy_preflight_with_restart_intent_stays_read_only(monkeypatch):
    called = False

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name, "args": args})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "terminal",
            {
                "command": (
                    "python3 -m hermes_cli.main policy preflight --intent "
                    "'change Hermes provider routing and restart gateway' --output json"
                )
            },
            user_task="Hermes fleet policy smoke",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True


def test_ordinary_non_fleet_write_stays_allowed(monkeypatch, tmp_path):
    called = False
    target = tmp_path / "note.txt"

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name, "path": args.get("path")})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "write_file",
            {"path": str(target), "content": "hello"},
            user_task="write a local project note",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True


def test_knowledge_vault_daily_write_stays_allowed(monkeypatch):
    called = False
    target = "/home/wwolfy/.hermes/knowledge/daily/2026-08-21.md"

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name, "path": args.get("path")})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "write_file",
            {"path": target, "content": "# daily"},
            user_task="Hermes KB daily log maintenance",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True


def test_service_mutation_blocks_in_fleet_context(monkeypatch):
    called = False

    def fake_dispatch(*_args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "ha_call_service",
            {"domain": "script", "service": "turn_on", "entity_id": "script.gateway_restart"},
            user_task="Hermes fleet gateway restart through Home Assistant",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is False
    assert result["error_type"] == "fleet_preflight_required"


def test_preflight_guard_errors_fail_closed_for_mutations(monkeypatch):
    called = False

    def broken_guard(*_args, **_kwargs):
        raise RuntimeError("boom")

    def fake_dispatch(*_args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True})

    monkeypatch.setattr(fleet_preflight, "check_fleet_preflight", broken_guard)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "write_file",
            {"path": "/tmp/example.txt", "content": "hello"},
            user_task="ordinary write",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is False
    assert result["error_type"] == "fleet_preflight_guard_error"


def test_valid_preflight_marker_allows_fleet_mutation(monkeypatch, tmp_path):
    called = False
    marker = tmp_path / "fleet-preflight.json"
    marker.write_text(
        json.dumps(
            {
                "policy_stage": "Fedor canary",
                "backup_plan": "timestamped config backup",
                "approval": "Vadim approved this exact restart",
                "evidence_target": str(tmp_path / "evidence.md"),
            }
        ),
        encoding="utf-8",
    )

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name})

    monkeypatch.setenv(PREFLIGHT_MARKER_ENV, str(marker))
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "terminal",
            {"command": "systemctl restart hermes-gateway.service"},
            user_task="Hermes fleet rollout on Fedor",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True
