from __future__ import annotations

import json
from argparse import Namespace

import yaml


def test_policy_definition_loads_rollout_order():
    from hermes_cli.policy_cmd import load_policy_definition

    policy = load_policy_definition()

    assert policy["id"] == "hermes-fleet-change-policy"
    assert policy["required_rollout_order"] == ["Fedor", "93", "Archivarius"]
    assert "write" in policy["requires_approval_for"]

    machine = policy["rollout_state_machine"]
    assert machine["initial_stage"] == "classify"
    assert machine["override_field"] == "policy_override"
    assert machine["stages"]["fedor_apply"]["phase"] == "apply"
    assert machine["stages"]["mac93_apply"]["required_completed"] == ["fedor_smoke"]
    assert machine["stages"]["archivarius_apply"]["required_completed"] == ["mac93_smoke"]


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
        "fedor_apply",
        "fedor_smoke",
        "mac93_apply",
        "mac93_smoke",
        "archivarius_apply",
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


def test_policy_definition_declares_fleet_evidence_schema():
    from hermes_cli.policy_cmd import evidence_schema

    schema = evidence_schema()

    assert schema["required_fields"] == [
        "evidence_path",
        "backups",
        "changed_paths",
        "hashes",
        "approvals",
        "smoke",
        "retention_decision",
    ]
    assert schema["smoke_nodes"] == ["Fedor", "93", "Archivarius"]


def test_check_evidence_reports_missing_durable_fields(capsys, tmp_path):
    from hermes_cli.policy_cmd import cmd_policy

    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "evidence_path": "/tmp/fleet.md",
                "backups": ["/tmp/backup.tgz"],
                "changed_paths": ["~/.hermes/config.yaml"],
                "hashes": {"before": "abc", "after": "def"},
                "approvals": ["approved by Vadim"],
                "smoke": {"Fedor": "PASS"},
            }
        ),
        encoding="utf-8",
    )

    rc = cmd_policy(
        Namespace(
            policy_command="check-evidence",
            evidence=str(evidence),
            output="json",
        )
    )

    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed"] is False
    assert "retention_decision" in payload["missing_fields"]
    assert "smoke.93" in payload["missing_fields"]
    assert "smoke.Archivarius" in payload["missing_fields"]


def test_check_evidence_allows_complete_schema(capsys, tmp_path):
    from hermes_cli.policy_cmd import cmd_policy

    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "evidence_path": "/tmp/fleet.md",
                "backups": ["/tmp/backup.tgz"],
                "changed_paths": ["~/.hermes/config.yaml"],
                "hashes": {"before": "abc", "after": "def"},
                "approvals": ["approved by Vadim"],
                "smoke": {"Fedor": "PASS", "93": "PASS", "Archivarius": "PASS"},
                "retention_decision": "retain backups for rollback",
            }
        ),
        encoding="utf-8",
    )

    rc = cmd_policy(
        Namespace(
            policy_command="check-evidence",
            evidence=str(evidence),
            output="json",
        )
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed"] is True
    assert payload["missing_fields"] == []


def _stage_evidence(status="PASS"):
    return {
        "evidence_path": "/tmp/fleet-rollout.json",
        "status": status,
        "recorded_at": "2026-08-20T20:00:00Z",
    }


def test_stage_blocks_mac93_apply_before_fedor_smoke(capsys, tmp_path):
    from hermes_cli.policy_cmd import cmd_policy

    record = tmp_path / "rollout.json"
    record.write_text(
        json.dumps(
            {
                "completed_stages": [
                    "classify",
                    "read_only_audit",
                    "proposal_and_approval",
                    "backup_marker",
                    "fedor_apply",
                ],
                "evidence": {
                    "read_only_audit": _stage_evidence(),
                    "proposal_and_approval": _stage_evidence(),
                    "backup_marker": _stage_evidence(),
                    "fedor_apply": _stage_evidence(),
                },
            }
        ),
        encoding="utf-8",
    )

    rc = cmd_policy(
        Namespace(policy_command="stage", target="mac93_apply", record=str(record), output="json")
    )

    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed"] is False
    assert payload["node"] == "93"
    assert "fedor_smoke" in payload["missing_fields"]
    assert "evidence.fedor_smoke" in payload["missing_fields"]


def test_stage_blocks_archivarius_apply_before_mac93_smoke(capsys, tmp_path):
    from hermes_cli.policy_cmd import cmd_policy

    record = tmp_path / "rollout.json"
    record.write_text(
        json.dumps(
            {
                "completed_stages": [
                    "classify",
                    "read_only_audit",
                    "proposal_and_approval",
                    "backup_marker",
                    "fedor_apply",
                    "fedor_smoke",
                    "mac93_apply",
                ],
                "evidence": {
                    "read_only_audit": _stage_evidence(),
                    "proposal_and_approval": _stage_evidence(),
                    "backup_marker": _stage_evidence(),
                    "fedor_apply": _stage_evidence(),
                    "fedor_smoke": _stage_evidence(),
                    "mac93_apply": _stage_evidence(),
                },
            }
        ),
        encoding="utf-8",
    )

    rc = cmd_policy(
        Namespace(policy_command="stage", target="archivarius_apply", record=str(record), output="json")
    )

    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed"] is False
    assert payload["node"] == "Archivarius"
    assert "mac93_smoke" in payload["missing_fields"]
    assert "evidence.mac93_smoke" in payload["missing_fields"]


def test_stage_requires_machine_readable_evidence_for_completed_prereqs(capsys, tmp_path):
    from hermes_cli.policy_cmd import cmd_policy

    record = tmp_path / "rollout.json"
    record.write_text(
        json.dumps(
            {
                "completed_stages": ["classify", "read_only_audit"],
                "evidence": {"read_only_audit": {"evidence_path": "/tmp/audit.json"}},
            }
        ),
        encoding="utf-8",
    )

    rc = cmd_policy(
        Namespace(
            policy_command="stage",
            target="proposal_and_approval",
            record=str(record),
            output="json",
        )
    )

    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed"] is False
    assert "evidence.read_only_audit.recorded_at" in payload["missing_fields"]
    assert "evidence.read_only_audit.status" in payload["missing_fields"]


def test_stage_allows_ordered_transition_with_durable_evidence(capsys, tmp_path):
    from hermes_cli.policy_cmd import cmd_policy

    record = tmp_path / "rollout.json"
    record.write_text(
        json.dumps(
            {
                "completed_stages": [
                    "classify",
                    "read_only_audit",
                    "proposal_and_approval",
                    "backup_marker",
                    "fedor_apply",
                    "fedor_smoke",
                ],
                "evidence": {
                    "read_only_audit": _stage_evidence(),
                    "proposal_and_approval": _stage_evidence(),
                    "backup_marker": _stage_evidence(),
                    "fedor_apply": _stage_evidence(),
                    "fedor_smoke": _stage_evidence(),
                },
            }
        ),
        encoding="utf-8",
    )

    rc = cmd_policy(
        Namespace(policy_command="stage", target="mac93_apply", record=str(record), output="json")
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed"] is True
    assert payload["missing_fields"] == []


def test_stage_allows_explicit_policy_override_for_skip(capsys, tmp_path):
    from hermes_cli.policy_cmd import cmd_policy

    record = tmp_path / "rollout.json"
    record.write_text(
        json.dumps(
            {
                "completed_stages": ["classify"],
                "policy_override": {
                    "reason": "emergency rollback rehearsal",
                    "approved_by": "Vadim",
                },
            }
        ),
        encoding="utf-8",
    )

    rc = cmd_policy(
        Namespace(policy_command="stage", target="archivarius_apply", record=str(record), output="json")
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed"] is True
    assert payload["policy_override"] is True
    assert "mac93_smoke" in payload["missing_fields"]


def test_autoload_normalizes_string_list_without_writing(monkeypatch, tmp_path, capsys):
    from hermes_cli.policy_cmd import cmd_policy

    home = tmp_path / "home"
    home.mkdir()
    config_path = home / "config.yaml"
    config_path.write_text(
        "skills:\n  auto_preload: '[\"bridge-agents\", \"ask-expert\"]'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))

    rc = cmd_policy(Namespace(policy_command="autoload", write=False, output="json"))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["auto_preload"] == [
        "bridge-agents",
        "ask-expert",
        "hermes-fleet-change-policy",
    ]
    assert payload["changed"] is True
    assert payload["written"] is False
    assert "does not mutate the current conversation" in payload["reset_boundary"]
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["skills"]["auto_preload"] == '["bridge-agents", "ask-expert"]'


def test_autoload_write_preserves_entries_as_structured_yaml_list(monkeypatch, tmp_path, capsys):
    from hermes_cli.policy_cmd import cmd_policy

    home = tmp_path / "home"
    home.mkdir()
    config_path = home / "config.yaml"
    config_path.write_text(
        "model:\n  default: test-model\nskills:\n  auto_preload:\n    - bridge-agents\n    - ask-expert\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))

    rc = cmd_policy(Namespace(policy_command="autoload", write=True, output="json"))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["written"] is True
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["skills"]["auto_preload"] == [
        "bridge-agents",
        "ask-expert",
        "hermes-fleet-change-policy",
    ]
    assert not isinstance(saved["skills"]["auto_preload"], str)


def test_autoload_is_idempotent_when_policy_already_configured(monkeypatch, tmp_path, capsys):
    from hermes_cli.policy_cmd import cmd_policy

    home = tmp_path / "home"
    home.mkdir()
    config_path = home / "config.yaml"
    config_path.write_text(
        "skills:\n  auto_preload:\n    - bridge-agents\n    - hermes-fleet-change-policy\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))

    rc = cmd_policy(Namespace(policy_command="autoload", write=True, output="json"))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed"] is False
    assert payload["written"] is False
    assert payload["auto_preload"] == ["bridge-agents", "hermes-fleet-change-policy"]
