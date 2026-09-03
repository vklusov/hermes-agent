from __future__ import annotations

import json
from pathlib import Path

from scripts.observability.mac93_tailnet_diagnostic_watchdog import (
    DiagnosticRun,
    format_transition,
    update_watchdog_state,
)


def _payload(status: str, *, severity: str = "warning", dedupe_key: str | None = None) -> dict:
    return {
        "target": {"name": "mac93"},
        "status": status,
        "severity": severity,
        "dedupe_key": dedupe_key or f"mac93:{status}",
        "diagnosis": f"diagnosis for {status}",
        "checks": {
            "tailscale": {"backend_state": "Stopped", "self_online": False},
            "node_exporter_listen": {"tailnet_bound": True},
            "metrics": {"tailnet_ok": False, "tailnet_http_code": None},
        },
        "safety": {"mode": "read_only", "forbidden_actions_used": False},
    }


def test_first_unhealthy_emits_then_unchanged_is_silent(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    first = update_watchdog_state(_payload("tailscale_stopped_exporter_present"), state)
    second = update_watchdog_state(_payload("tailscale_stopped_exporter_present"), state)

    assert first.should_emit is True
    assert first.transition == "first_unhealthy"
    assert second.should_emit is False
    assert second.transition == "unchanged"
    assert json.loads(state.read_text())["last"]["dedupe_key"] == "mac93:tailscale_stopped_exporter_present"


def test_recovery_emits_once_after_unhealthy(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    update_watchdog_state(_payload("tailscale_stopped_exporter_present"), state)

    recovery = update_watchdog_state(_payload("healthy", severity="ok"), state)
    repeated = update_watchdog_state(_payload("healthy", severity="ok"), state)

    assert recovery.should_emit is True
    assert recovery.transition == "recovery"
    assert repeated.should_emit is False
    assert repeated.transition == "unchanged_healthy"


def test_status_change_and_escalation_emit(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    update_watchdog_state(_payload("bridge_unreachable", severity="warning"), state)

    changed = update_watchdog_state(_payload("metrics_unreachable", severity="warning"), state)
    escalated = update_watchdog_state(_payload("metrics_unreachable", severity="critical", dedupe_key="mac93:metrics_unreachable:critical"), state)

    assert changed.should_emit is True
    assert changed.transition == "status_change"
    assert escalated.should_emit is True
    assert escalated.transition == "severity_escalation"


def test_underlying_unhealthy_exit_is_data_not_wrapper_failure(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    run = DiagnosticRun(returncode=1, stdout=json.dumps(_payload("tailscale_stopped_exporter_present")), stderr="")

    result = update_watchdog_state(run.payload(), state)
    message = format_transition(result)

    assert result.should_emit is True
    assert "MAC93 DIAGNOSTIC FIRST_UNHEALTHY" in message
    assert "forbidden_actions_used=False" in message
    assert "diagnosis for tailscale_stopped_exporter_present" in message
