"""Validate the machine-readable Hermes infra automation policy."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable, cast

import yaml

DEFAULT_POLICY_PATH = Path(__file__).with_name("automation_policy.yaml")
REQUIRED_HOST_CLASSES = {
    "control-plane",
    "worker",
    "monitoring",
    "mac-capability",
}
REQUIRED_DENY_IDS = {
    "reboot_or_power_cycle",
    "vpn_tailscale_amnezia_or_route_change",
    "firewall_change",
    "secrets_or_env_edit",
    "provider_primary_route_change",
    "deletion_or_pruning_without_retention_policy",
    "dirty_checkout_cleanup",
}
REQUIRED_TIER_B_FIELDS = (
    "max_attempts",
    "window_minutes",
    "pre_action_snapshot",
    "post_check",
    "rollback_pointer",
    "escalation_rule",
)
REQUIRED_NATIVE_FLEET_ACTION_CLASSES = {
    "runtime_code_overlay",
    "scheduled_job_creation_or_update",
    "service_lifecycle",
    "provider_or_config_change",
    "cleanup_or_retention",
    "live_checkout_update",
}
REQUIRED_NATIVE_FLEET_DECISION_FIELDS = {
    "affected_nodes",
    "rollout_order",
    "approvals",
    "evidence_root",
    "result",
}
REQUIRED_NATIVE_FLEET_EXCEPTION_FIELDS = {
    "exception",
    "exception_reason",
    "affected_nodes",
    "skipped_nodes",
    "approval_ref",
}
DENYLIST_PATTERNS = {
    "reboot",
    "poweroff",
    "shutdown",
    "vpn",
    "tailscale",
    "tailscaled",
    "amnezia",
    "route",
    "firewall",
    "ufw",
    "iptables",
    "nft",
    "pfctl",
    ".env",
    "api key",
    "provider key",
    "primary provider",
    "main route",
    "default model",
    "provider routing",
    "fallback chain",
    "rm -rf",
    "truncate",
    "git reset --hard",
    "git clean",
    "dirty checkout",
    "discard changes",
    "remove overlay",
}


class PolicyValidationError(ValueError):
    """Raised when automation policy data would widen unsafe automation."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyValidationError(message)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    _require(isinstance(loaded, dict), "policy must be a YAML mapping")
    return loaded


def _text_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _text_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _text_values(nested)


def _matches_deny_pattern(action: dict[str, Any]) -> str | None:
    haystack = " ".join(_text_values(action)).lower()
    for pattern in sorted(DENYLIST_PATTERNS, key=len, reverse=True):
        if pattern in haystack:
            return pattern
    return None


def _validate_host_classes(policy: dict[str, Any]) -> None:
    host_classes = policy.get("host_classes")
    _require(isinstance(host_classes, dict), "host_classes must be a mapping")
    missing = REQUIRED_HOST_CLASSES - set(host_classes)
    extra = set(host_classes) - REQUIRED_HOST_CLASSES
    _require(not missing, f"missing host classes: {sorted(missing)}")
    _require(not extra, f"unknown host classes: {sorted(extra)}")
    for host_class, spec in host_classes.items():
        _require(isinstance(spec, dict), f"host class {host_class} must be a mapping")
        _require(spec.get("description"), f"host class {host_class} requires description")
        allowed_tiers = spec.get("allowed_tiers")
        _require(isinstance(allowed_tiers, list), f"host class {host_class} requires allowed_tiers list")
        _require(set(allowed_tiers).issubset({"A", "B"}), f"host class {host_class} may only allow Tier A/B automation")


def _validate_denylist(policy: dict[str, Any]) -> None:
    denylist = policy.get("denylist")
    _require(isinstance(denylist, list), "denylist must be a list")
    deny_by_id = {entry.get("id"): entry for entry in denylist if isinstance(entry, dict)}
    missing = REQUIRED_DENY_IDS - set(deny_by_id)
    _require(not missing, f"missing denylist ids: {sorted(missing)}")
    for deny_id, entry in deny_by_id.items():
        _require(entry.get("tier") == "C", f"denylist entry {deny_id} must be Tier C")
        _require(entry.get("requires") == "ask_vadim", f"denylist entry {deny_id} must require ask_vadim")
        patterns = entry.get("patterns")
        _require(isinstance(patterns, list) and patterns, f"denylist entry {deny_id} requires non-empty patterns")


def _validate_tiers(policy: dict[str, Any]) -> None:
    tiers = policy.get("tiers")
    _require(isinstance(tiers, dict), "tiers must be a mapping")
    _require(tiers.get("A", {}).get("mode") == "automatic", "Tier A mode must be automatic")
    _require(tiers.get("B", {}).get("mode") == "whitelisted_with_circuit_breaker", "Tier B mode must require whitelist and circuit breaker")
    _require(tiers.get("C", {}).get("mode") == "ask_vadim", "Tier C mode must ask Vadim")

    tier_a_classes = set(tiers["A"].get("allowed_action_classes", []))
    _require(
        {"read_only_audit", "deduplication", "rehearsal"}.issubset(tier_a_classes),
        "Tier A must allow read-only audit, deduplication, and rehearsal classes",
    )

    defaults = policy.get("circuit_breaker_defaults")
    _require(isinstance(defaults, dict), "circuit_breaker_defaults must be a mapping")
    for field in ("max_attempts", "window_minutes", "cooldown_minutes", "on_trip"):
        _require(defaults.get(field), f"circuit_breaker_defaults requires {field}")
    _require(int(defaults["max_attempts"]) >= 1, "default max_attempts must be >= 1")
    _require(int(defaults["window_minutes"]) >= 1, "default window_minutes must be >= 1")

    required_fields = set(tiers["B"].get("required_per_action_fields", []))
    _require(set(REQUIRED_TIER_B_FIELDS).issubset(required_fields), "Tier B required_per_action_fields missing required gates")

    tier_b_actions = tiers["B"].get("allowed_actions")
    _require(isinstance(tier_b_actions, list), "Tier B allowed_actions must be a list")
    known_host_classes = set(policy["host_classes"])
    for action in tier_b_actions:
        _require(isinstance(action, dict), "Tier B action must be a mapping")
        action_id = action.get("id", "<missing id>")
        for field in REQUIRED_TIER_B_FIELDS:
            _require(bool(action.get(field)), f"Tier B action {action_id} requires {field}")
        _require(int(action["max_attempts"]) >= 1, f"Tier B action {action_id} max_attempts must be >= 1")
        _require(int(action["window_minutes"]) >= 1, f"Tier B action {action_id} window_minutes must be >= 1")
        action_hosts = set(action.get("host_classes") or [])
        _require(action_hosts and action_hosts.issubset(known_host_classes), f"Tier B action {action_id} has unknown host class")
        pattern = _matches_deny_pattern(action)
        _require(pattern is None, f"Tier B action {action_id} matches denylisted pattern: {pattern}")


def _validate_audit_requirements(policy: dict[str, Any]) -> None:
    audit = policy.get("audit_requirements")
    _require(isinstance(audit, dict), "audit_requirements must be a mapping")
    required_fields = set(audit.get("required_fields") or [])
    expected = {
        "timestamp",
        "operator_or_run_id",
        "node",
        "host_class",
        "tier",
        "action_id",
        "before_snapshot",
        "after_snapshot",
        "result",
    }
    missing = expected - required_fields
    _require(not missing, f"audit_requirements missing fields: {sorted(missing)}")
    _require(audit.get("evidence_root"), "audit_requirements requires evidence_root")


def _validate_native_fleet_gate(policy: dict[str, Any]) -> None:
    raw_gate = policy.get("native_fleet_gate")
    _require(isinstance(raw_gate, dict), "native_fleet_gate must be a mapping")
    gate = cast(dict[str, Any], raw_gate)
    _require(gate.get("mode") == "enforce_before_mutation", "native_fleet_gate mode must enforce before mutation")
    _require(
        gate.get("default_rollout_order") == ["fedor", "93", "archivarius"],
        "native_fleet_gate default_rollout_order must be fedor -> 93 -> archivarius",
    )
    action_classes = set(gate.get("fleet_affecting_action_classes") or [])
    missing_actions = REQUIRED_NATIVE_FLEET_ACTION_CLASSES - action_classes
    _require(not missing_actions, f"native_fleet_gate missing action classes: {sorted(missing_actions)}")
    decision_fields = set(gate.get("required_decision_fields") or [])
    missing_decision = REQUIRED_NATIVE_FLEET_DECISION_FIELDS - decision_fields
    _require(not missing_decision, f"native_fleet_gate required_decision_fields missing: {sorted(missing_decision)}")
    exception_fields = set(gate.get("exception_required_fields") or [])
    missing_exception = REQUIRED_NATIVE_FLEET_EXCEPTION_FIELDS - exception_fields
    _require(not missing_exception, f"native_fleet_gate exception_required_fields missing: {sorted(missing_exception)}")
    _require(gate.get("on_missing_gate") == "block_and_create_kanban", "native_fleet_gate requires block_and_create_kanban fallback")


def validate_policy_file(path: str | Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    policy_path = Path(path)
    policy = _load_yaml(policy_path)
    _validate_host_classes(policy)
    _validate_denylist(policy)
    _validate_tiers(policy)
    _validate_audit_requirements(policy)
    _validate_native_fleet_gate(policy)
    return policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", nargs="?", default=str(DEFAULT_POLICY_PATH))
    args = parser.parse_args(argv)
    try:
        validate_policy_file(args.policy)
    except PolicyValidationError as exc:
        print(f"INVALID: {exc}")
        return 1
    print(f"VALID: {args.policy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
