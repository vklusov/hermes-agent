from __future__ import annotations

import json
from argparse import Namespace


def test_policy_definition_loads_rollout_order():
    from hermes_cli.policy_cmd import load_policy_definition

    policy = load_policy_definition()

    assert policy["id"] == "hermes-fleet-change-policy"
    assert policy["required_rollout_order"] == ["Fedor", "93", "Archivarius"]
    assert "write" in policy["requires_approval_for"]


def test_preflight_classifies_provider_config_change_as_fleet(capsys):
    from hermes_cli.policy_cmd import cmd_policy

    rc = cmd_policy(
        Namespace(
            policy_command="preflight",
            intent="change Hermes provider routing on Fedor and restart gateway",
            output="json",
        )
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["policy"] == "hermes-fleet-change-policy"
    assert payload["is_fleet_change"] is True
    assert payload["change_class"] == "model/provider routing"
    assert payload["required_stages"] == [
        "classify",
        "read_only_audit",
        "proposal_and_approval",
        "backup_marker",
        "fedor_smoke",
        "mac93_smoke",
        "archivarius_smoke",
        "evidence",
        "retention",
    ]
    assert "provider" in payload["matched_triggers"]
    assert "restart" in payload["matched_triggers"]


def test_preflight_classifies_ordinary_task_as_not_fleet(capsys):
    from hermes_cli.policy_cmd import cmd_policy

    rc = cmd_policy(
        Namespace(
            policy_command="preflight",
            intent="fix typo in README",
            output="json",
        )
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["is_fleet_change"] is False
    assert payload["change_class"] == "ordinary"
    assert payload["required_stages"] == ["none"]


def test_check_fails_closed_for_missing_write_gate_fields(tmp_path, capsys):
    from hermes_cli.policy_cmd import cmd_policy

    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "policy": "hermes-fleet-change-policy",
                "is_fleet_change": True,
                "required_stages": ["read_only_audit", "proposal_and_approval"],
                "completed_stages": ["read_only_audit"],
            }
        ),
        encoding="utf-8",
    )

    rc = cmd_policy(
        Namespace(
            policy_command="check",
            before="write",
            preflight=str(preflight),
            output="json",
        )
    )

    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed"] is False
    assert "approval" in payload["missing_fields"]
    assert "backup_marker" in payload["missing_fields"]
    assert "evidence_path" in payload["missing_fields"]


def test_check_allows_non_fleet_preflight_before_write(tmp_path, capsys):
    from hermes_cli.policy_cmd import cmd_policy

    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "policy": "hermes-fleet-change-policy",
                "is_fleet_change": False,
                "change_class": "ordinary",
            }
        ),
        encoding="utf-8",
    )

    rc = cmd_policy(
        Namespace(
            policy_command="check",
            before="write",
            preflight=str(preflight),
            output="json",
        )
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed"] is True
    assert payload["missing_fields"] == []