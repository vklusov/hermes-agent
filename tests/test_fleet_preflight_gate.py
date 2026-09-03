from datetime import datetime, timedelta, timezone
import json

import model_tools
import agent.fleet_preflight as fleet_preflight
from agent.fleet_preflight import PREFLIGHT_MARKER_ENV


def _decode(result):
    return json.loads(result)


def test_fleet_lifecycle_blocks_without_preflight(monkeypatch):
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
            {"command": 'hermes gateway restart'},
            user_task="Hermes fleet rollout on Archivarius",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is False
    assert result["error_type"] == 'fleet_preflight_required'
    assert result["classified_fleet_context"] is True


def test_compound_read_only_fedor_triage_allowed_without_marker(monkeypatch):
    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)

    decision = fleet_preflight.check_fleet_preflight(
        "terminal",
        {
            "command": (
                "ssh -i /key -o BatchMode=yes root@100.92.229.56 "
                "'set -euo pipefail; cd /usr/local/lib/hermes-agent; "
                "GIT_OPTIONAL_LOCKS=0 git status --short --branch; "
                "git diff --check || true; pgrep -af \"hermes.*gateway|fedoramm\" || true'"
            )
        },
        user_task="Fedor dirty WIP triage",
    )

    assert decision.blocked is False
    assert decision.classified is True
    assert decision.mutation is False


def test_git_pull_still_blocked_without_cron_approval(monkeypatch):
    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.delenv("HERMES_CRON_FLEET_APPROVAL", raising=False)
    monkeypatch.delenv("HERMES_CRON_JOB_ID", raising=False)

    decision = fleet_preflight.check_fleet_preflight(
        "terminal",
        {"command": "ssh fedor cd /srv/hermes && git pull --ff-only"},
        user_task="daily remote Hermes update",
    )

    assert decision.blocked is True
    assert decision.classified is True
    assert decision.mutation is True


def test_valid_preflight_marker_allows_fleet_mutation(monkeypatch, tmp_path):
    called = False
    marker = tmp_path / "fleet-preflight.json"
    marker.write_text(json.dumps({"policy_stage":"Fedor canary","backup_plan":"backup","approval":"approved","evidence_target":str(tmp_path)}), encoding="utf-8")

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name})

    monkeypatch.setenv(PREFLIGHT_MARKER_ENV, str(marker))
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "terminal",
            {"command": 'systemctl restart hermes-gateway.service'},
            user_task="Hermes fleet rollout on Fedor",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True
