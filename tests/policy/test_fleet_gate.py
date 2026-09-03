from __future__ import annotations

from pathlib import Path

import pytest

from policy.fleet_gate import evaluate_fleet_gate, main


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "policy" / "automation_policy.yaml"


def test_native_gate_allows_recorded_full_rollout_decision():
    result = evaluate_fleet_gate(
        {
            "action_class": "scheduled_job_creation_or_update",
            "affected_nodes": ["fedor", "93", "archivarius"],
            "rollout_order": ["fedor", "93", "archivarius"],
            "approvals": ["chat:approval"],
            "evidence_root": "/tmp/evidence",
            "result": "PASS",
        },
        policy_path=POLICY_PATH,
    )

    assert result.allowed is True
    assert result.reason == "staged_rollout_recorded"


def test_native_gate_blocks_archivarius_first_without_exception():
    result = evaluate_fleet_gate(
        {
            "action_class": "live_checkout_update",
            "affected_nodes": ["archivarius"],
            "rollout_order": ["archivarius"],
            "approvals": ["chat:approval"],
            "evidence_root": "/tmp/evidence",
            "result": "PASS",
        },
        policy_path=POLICY_PATH,
    )

    assert result.allowed is False
    assert "rollout_order" in result.reason


def test_native_gate_allows_explicit_archivarius_only_exception():
    result = evaluate_fleet_gate(
        {
            "action_class": "live_checkout_update",
            "affected_nodes": ["archivarius"],
            "exception": "archivarius_only",
            "exception_reason": "control-plane emergency repair; staged rollout deferred",
            "skipped_nodes": ["fedor", "93"],
            "approval_ref": "telegram:2026-09-03",
        },
        policy_path=POLICY_PATH,
    )

    assert result.allowed is True
    assert result.reason == "archivarius_only_exception_recorded"


def test_native_gate_blocks_incomplete_archivarius_exception():
    result = evaluate_fleet_gate(
        {
            "action_class": "service_lifecycle",
            "affected_nodes": ["archivarius"],
            "exception": "archivarius_only",
            "exception_reason": "approved restart",
            "skipped_nodes": ["fedor"],
            "approval_ref": "telegram:2026-09-03",
        },
        policy_path=POLICY_PATH,
    )

    assert result.allowed is False
    assert "skipped fedor and 93" in result.reason


def test_native_gate_ignores_non_fleet_action_class():
    result = evaluate_fleet_gate(
        {"action_class": "read_only_audit", "affected_nodes": ["archivarius"]},
        policy_path=POLICY_PATH,
    )

    assert result.allowed is True
    assert result.reason == "not_fleet_affecting"


@pytest.mark.parametrize(
    "action_class",
    [
        "runtime_code_overlay",
        "scheduled_job_creation_or_update",
        "service_lifecycle",
        "provider_or_config_change",
        "cleanup_or_retention",
        "live_checkout_update",
    ],
)
def test_native_gate_blocks_archivarius_first_for_all_fleet_action_classes(action_class: str):
    result = evaluate_fleet_gate(
        {
            "action_class": action_class,
            "affected_nodes": ["archivarius"],
            "rollout_order": ["archivarius"],
            "approvals": ["chat:approval"],
            "evidence_root": "/tmp/evidence",
            "result": "PASS",
        },
        policy_path=POLICY_PATH,
    )

    assert result.allowed is False


def test_cli_preflight_allows_valid_decision(capsys):
    code = main(
        [
            "--action-class",
            "runtime_code_overlay",
            "--mutation",
            "scratch preflight",
            "--policy",
            str(POLICY_PATH),
            "--decision-json",
            '{"affected_nodes":["fedor","93","archivarius"],"rollout_order":["fedor","93","archivarius"],"approvals":["test"],"evidence_root":"/tmp/evidence","result":"preflight_passed"}',
            "--json",
        ]
    )

    assert code == 0
    assert '"allowed": true' in capsys.readouterr().out


def test_cli_preflight_blocks_missing_decision(capsys):
    code = main(
        [
            "--action-class",
            "runtime_code_overlay",
            "--mutation",
            "live checkout update",
            "--policy",
            str(POLICY_PATH),
            "--json",
        ]
    )

    assert code == 1
    out = capsys.readouterr().out
    assert '"allowed": false' in out
    assert "missing fleet_decision" in out
