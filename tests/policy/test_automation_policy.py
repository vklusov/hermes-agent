from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from policy.validate_automation_policy import PolicyValidationError, validate_policy_file


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "policy" / "automation_policy.yaml"


def load_policy() -> dict:
    with POLICY_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_policy_declares_machine_checkable_host_classes_and_tiers():
    policy = validate_policy_file(POLICY_PATH)

    assert set(policy["host_classes"]) == {
        "control-plane",
        "worker",
        "monitoring",
        "mac-capability",
    }
    assert policy["tiers"]["A"]["mode"] == "automatic"
    assert set(policy["tiers"]["A"]["allowed_action_classes"]) == {
        "read_only_audit",
        "deduplication",
        "rehearsal",
    }
    assert policy["tiers"]["B"]["mode"] == "whitelisted_with_circuit_breaker"
    assert policy["tiers"]["C"]["mode"] == "ask_vadim"


def test_tier_b_actions_require_snapshot_postcheck_rollback_and_circuit_window():
    policy = validate_policy_file(POLICY_PATH)

    defaults = policy["circuit_breaker_defaults"]
    assert defaults["max_attempts"] >= 1
    assert defaults["window_minutes"] >= 1

    for action in policy["tiers"]["B"]["allowed_actions"]:
        assert action["max_attempts"] >= 1
        assert action["window_minutes"] >= 1
        assert action["pre_action_snapshot"]
        assert action["post_check"]
        assert action["rollback_pointer"]
        assert action["escalation_rule"]


def test_denylist_covers_tier_c_gates():
    policy = validate_policy_file(POLICY_PATH)
    deny_ids = {entry["id"] for entry in policy["denylist"]}

    assert {
        "reboot_or_power_cycle",
        "vpn_tailscale_amnezia_or_route_change",
        "firewall_change",
        "secrets_or_env_edit",
        "provider_primary_route_change",
        "deletion_or_pruning_without_retention_policy",
        "dirty_checkout_cleanup",
    }.issubset(deny_ids)

    assert all(entry["tier"] == "C" for entry in policy["denylist"])
    assert all(entry["requires"] == "ask_vadim" for entry in policy["denylist"])


def test_validator_rejects_unsafe_tier_b_expansion(tmp_path):
    policy = load_policy()
    policy["tiers"]["B"]["allowed_actions"].append(
        {
            "id": "restart_gateway_without_snapshot",
            "description": "unsafe expansion missing required gates",
            "host_classes": ["control-plane"],
            "packages": [],
            "services": ["hermes-gateway"],
            "max_attempts": 3,
            "window_minutes": 30,
            "pre_action_snapshot": "",
            "post_check": "",
            "rollback_pointer": "",
            "escalation_rule": "",
        }
    )
    mutated = tmp_path / "unsafe.yaml"
    mutated.write_text(yaml.safe_dump(policy), encoding="utf-8")

    with pytest.raises(PolicyValidationError, match="pre_action_snapshot"):
        validate_policy_file(mutated)


def test_validator_rejects_denylisted_action_in_tier_b(tmp_path):
    policy = load_policy()
    policy["tiers"]["B"]["allowed_actions"].append(
        {
            "id": "restart_tailscale",
            "description": "must stay Tier C",
            "host_classes": ["worker"],
            "packages": [],
            "services": ["tailscaled"],
            "max_attempts": 1,
            "window_minutes": 60,
            "pre_action_snapshot": "systemctl status tailscaled",
            "post_check": "tailscale status",
            "rollback_pointer": "operator rollback only",
            "escalation_rule": "ask Vadim",
        }
    )
    mutated = tmp_path / "denylisted.yaml"
    mutated.write_text(yaml.safe_dump(policy), encoding="utf-8")

    with pytest.raises(PolicyValidationError, match="denylisted pattern"):
        validate_policy_file(mutated)
