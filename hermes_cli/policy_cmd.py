from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

POLICY_ID = "hermes-fleet-change-policy"
POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / f"{POLICY_ID}.yaml"
RESET_BOUNDARY_NOTE = (
    "skills.auto_preload is only applied when a new CLI/gateway session builds "
    "its system prompt; this command does not mutate the current conversation."
)


@lru_cache(maxsize=1)
def load_policy_definition() -> dict[str, Any]:
    data = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"policy definition must be a mapping: {POLICY_PATH}")
    if data.get("id") != POLICY_ID:
        raise ValueError(f"policy id mismatch in {POLICY_PATH}")
    return data


def _required_stages(policy: dict[str, Any]) -> list[str]:
    stages = policy.get("required_stages") or []
    return [str(stage) for stage in stages]


def _fleet_triggers(policy: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    applies_to = policy.get("applies_to") or {}
    keywords = applies_to.get("keywords") if isinstance(applies_to, dict) else {}
    if not isinstance(keywords, dict):
        return {}
    return {
        str(change_class): tuple(str(trigger) for trigger in triggers or [])
        for change_class, triggers in keywords.items()
    }


def _before_required_fields(policy: dict[str, Any]) -> dict[str, list[str]]:
    fields = policy.get("before_required_fields") or {}
    if not isinstance(fields, dict):
        return {}
    return {
        str(before): [str(field) for field in required or []]
        for before, required in fields.items()
    }


def evidence_schema(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy_definition()
    schema = policy.get("evidence_schema") or {}
    if not isinstance(schema, dict):
        return {"required_fields": [], "smoke_nodes": []}
    return {
        "required_fields": [str(field) for field in schema.get("required_fields") or []],
        "smoke_nodes": [str(node) for node in schema.get("smoke_nodes") or []],
    }


def check_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    schema = evidence_schema()
    missing: list[str] = []
    for field in schema["required_fields"]:
        if not evidence.get(field):
            missing.append(field)

    smoke = evidence.get("smoke")
    smoke_by_node = smoke if isinstance(smoke, dict) else {}
    for node in schema["smoke_nodes"]:
        node_smoke = smoke_by_node.get(node)
        if not node_smoke:
            missing.append(f"smoke.{node}")

    return {
        "allowed": not missing,
        "before": "done",
        "missing_fields": sorted(set(missing)),
        "required_fields": schema["required_fields"],
        "smoke_nodes": schema["smoke_nodes"],
    }


def _rollout_state_machine(policy: dict[str, Any]) -> dict[str, Any]:
    machine = policy.get("rollout_state_machine") or {}
    return machine if isinstance(machine, dict) else {}


def _stage_definitions(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stages = _rollout_state_machine(policy).get("stages") or {}
    if not isinstance(stages, dict):
        return {}
    return {
        str(name): details if isinstance(details, dict) else {}
        for name, details in stages.items()
    }


def _per_stage_evidence_fields(policy: dict[str, Any]) -> list[str]:
    schema = policy.get("evidence_schema") or {}
    fields = schema.get("per_stage_required_fields") if isinstance(schema, dict) else None
    return [str(field) for field in (fields or ["evidence_path", "status"])]


def _explicit_policy_override(record: dict[str, Any], machine: dict[str, Any]) -> bool:
    override = record.get(str(machine.get("override_field") or "policy_override"))
    if override is True:
        return True
    if not isinstance(override, dict):
        return False
    return bool(override.get("reason") and (override.get("approved_by") or override.get("approval")))


def _missing_stage_evidence(
    policy: dict[str, Any], record: dict[str, Any], required_stages: list[str]
) -> list[str]:
    evidence = record.get("evidence") or {}
    evidence_by_stage = evidence if isinstance(evidence, dict) else {}
    stages = _stage_definitions(policy)
    required_fields = _per_stage_evidence_fields(policy)
    missing: list[str] = []
    for stage in required_stages:
        if not stages.get(stage, {}).get("evidence_required", False):
            continue
        stage_evidence = evidence_by_stage.get(stage)
        if not isinstance(stage_evidence, dict):
            missing.append(f"evidence.{stage}")
            continue
        for field in required_fields:
            if not stage_evidence.get(field):
                missing.append(f"evidence.{stage}.{field}")
    return missing


def check_stage_transition(record: dict[str, Any], target_stage: str) -> dict[str, Any]:
    policy = load_policy_definition()
    machine = _rollout_state_machine(policy)
    stages = _stage_definitions(policy)
    if not machine or not stages:
        return {
            "allowed": False,
            "target_stage": target_stage,
            "missing_fields": ["rollout_state_machine"],
            "policy_override": False,
        }

    stage = stages.get(target_stage)
    if stage is None:
        return {
            "allowed": False,
            "target_stage": target_stage,
            "missing_fields": ["known_stage"],
            "policy_override": False,
        }

    completed = {str(item) for item in (record.get("completed_stages") or [])}
    required = [str(item) for item in (stage.get("required_completed") or [])]
    missing = [stage_name for stage_name in required if stage_name not in completed]
    missing.extend(_missing_stage_evidence(policy, record, required))

    override = _explicit_policy_override(record, machine)
    return {
        "allowed": not missing or override,
        "target_stage": target_stage,
        "phase": stage.get("phase"),
        "node": stage.get("node"),
        "required_completed": required,
        "missing_fields": sorted(set(missing)),
        "policy_override": override,
    }


def classify_intent(intent: str) -> dict[str, Any]:
    policy = load_policy_definition()
    text = (intent or "").lower()
    matched: list[str] = []
    matched_classes: list[str] = []
    for change_class, triggers in _fleet_triggers(policy).items():
        class_hits = [trigger for trigger in triggers if trigger in text]
        if class_hits:
            matched_classes.append(change_class)
            matched.extend(class_hits)

    is_fleet_change = bool(matched)
    change_class = matched_classes[0] if matched_classes else "ordinary"
    if "model/provider routing" in matched_classes:
        change_class = "model/provider routing"
    elif "service/runtime config" in matched_classes:
        change_class = "service/runtime config"

    return {
        "policy": POLICY_ID,
        "is_fleet_change": is_fleet_change,
        "change_class": change_class,
        "matched_triggers": sorted(set(matched)),
        "required_rollout_order": policy.get("required_rollout_order", []),
        "required_stages": _required_stages(policy) if is_fleet_change else ["none"],
    }


def check_preflight(preflight: dict[str, Any], before: str) -> dict[str, Any]:
    policy = load_policy_definition()
    missing: list[str] = []
    if preflight.get("policy") != POLICY_ID:
        missing.append("policy")

    if not preflight.get("is_fleet_change"):
        return {"allowed": True, "before": before, "missing_fields": []}

    for field in _before_required_fields(policy).get(before, []):
        if not preflight.get(field):
            missing.append(field)

    completed = set(preflight.get("completed_stages") or [])
    for stage in ("read_only_audit", "proposal_and_approval"):
        if stage not in completed:
            missing.append(stage)

    return {
        "allowed": not missing,
        "before": before,
        "missing_fields": sorted(set(missing)),
    }


def _normalize_auto_preload(value: Any) -> list[str]:
    """Return skills.auto_preload as Hermes runtime readers will see it."""
    from agent.skill_utils import parse_config_string_list
    from cli import _parse_skills_argument

    return _parse_skills_argument(parse_config_string_list(value))


def _read_auto_preload_config() -> tuple[dict[str, Any], list[str]]:
    from hermes_cli.config import read_raw_config

    raw_config = read_raw_config()
    skills_cfg = raw_config.get("skills") if isinstance(raw_config, dict) else None
    auto_preload = (skills_cfg or {}).get("auto_preload") if isinstance(skills_cfg, dict) else None
    return raw_config, _normalize_auto_preload(auto_preload)


def ensure_policy_auto_preload(*, write: bool = False) -> dict[str, Any]:
    raw_config, existing = _read_auto_preload_config()
    updated = _normalize_auto_preload(existing + [POLICY_ID])
    changed = updated != existing

    if write and changed:
        from hermes_cli.config import get_config_path, require_readable_config_before_write
        from utils import atomic_yaml_write

        config_path = get_config_path()
        require_readable_config_before_write(config_path)
        if not isinstance(raw_config, dict):
            raw_config = {}
        skills_cfg = raw_config.get("skills")
        if not isinstance(skills_cfg, dict):
            skills_cfg = {}
            raw_config["skills"] = skills_cfg
        skills_cfg["auto_preload"] = updated
        atomic_yaml_write(config_path, raw_config, sort_keys=False)

    return {
        "policy": POLICY_ID,
        "configured": POLICY_ID in updated,
        "changed": changed,
        "written": bool(write and changed),
        "auto_preload": updated,
        "reset_boundary": RESET_BOUNDARY_NOTE,
    }


def _emit(payload: dict[str, Any], output: str) -> None:
    if output == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if "allowed" in payload:
        verdict = "allowed" if payload["allowed"] else "blocked"
        target = payload.get("before") or payload.get("target_stage") or "policy"
        print(f"{verdict}: {target}")
        if payload["missing_fields"]:
            print("missing: " + ", ".join(payload["missing_fields"]))
        return
    if "auto_preload" in payload:
        print(f"policy: {payload['policy']}")
        print(f"configured: {payload['configured']}")
        print(f"changed: {payload['changed']}")
        print(f"written: {payload['written']}")
        print("auto_preload: " + ", ".join(payload["auto_preload"]))
        print("reset_boundary: " + payload["reset_boundary"])
        return
    print(f"policy: {payload['policy']}")
    print(f"fleet_change: {payload['is_fleet_change']}")
    print(f"change_class: {payload['change_class']}")
    print("required_stages: " + ", ".join(payload["required_stages"]))


def cmd_policy(args: argparse.Namespace) -> int:
    command = getattr(args, "policy_command", None)
    output = getattr(args, "output", "json")
    if command == "preflight":
        payload = classify_intent(getattr(args, "intent", ""))
        _emit(payload, output)
        return 0
    if command == "check":
        path = Path(getattr(args, "preflight"))
        try:
            preflight = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            payload = {
                "allowed": False,
                "before": getattr(args, "before", ""),
                "missing_fields": ["readable_preflight"],
                "error": str(exc),
            }
            _emit(payload, output)
            return 2
        payload = check_preflight(preflight, getattr(args, "before", ""))
        _emit(payload, output)
        return 0 if payload["allowed"] else 2
    if command == "check-evidence":
        path = Path(getattr(args, "evidence"))
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            payload = {
                "allowed": False,
                "before": "done",
                "missing_fields": ["readable_evidence"],
                "error": str(exc),
            }
            _emit(payload, output)
            return 2
        payload = check_evidence(evidence)
        _emit(payload, output)
        return 0 if payload["allowed"] else 2
    if command == "stage":
        path = Path(getattr(args, "record"))
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            payload = {
                "allowed": False,
                "target_stage": getattr(args, "target", ""),
                "missing_fields": ["readable_rollout_record"],
                "error": str(exc),
                "policy_override": False,
            }
            _emit(payload, output)
            return 2
        payload = check_stage_transition(record, getattr(args, "target", ""))
        _emit(payload, output)
        return 0 if payload["allowed"] else 2
    if command == "autoload":
        payload = ensure_policy_auto_preload(write=bool(getattr(args, "write", False)))
        _emit(payload, output)
        return 0
    raise SystemExit("policy subcommand required")


def register_cli(parent: argparse.ArgumentParser) -> None:
    parent.set_defaults(func=lambda args: (parent.print_help(), 0)[1])
    subparsers = parent.add_subparsers(dest="policy_command")

    preflight = subparsers.add_parser(
        "preflight",
        help="Classify a proposed Hermes fleet change and list policy stages",
    )
    preflight.add_argument("--intent", required=True, help="Natural-language change intent")
    preflight.add_argument("--output", choices=("json", "text"), default="json")
    preflight.set_defaults(func=cmd_policy)

    check = subparsers.add_parser(
        "check",
        help="Check a preflight JSON before write, restart, or delete",
    )
    check.add_argument("--before", choices=("write", "restart", "delete"), required=True)
    check.add_argument("--preflight", required=True, help="Path to preflight JSON")
    check.add_argument("--output", choices=("json", "text"), default="json")
    check.set_defaults(func=cmd_policy)

    evidence = subparsers.add_parser(
        "check-evidence",
        help="Check durable fleet-change evidence before final Kanban completion",
    )
    evidence.add_argument("--evidence", required=True, help="Path to evidence JSON")
    evidence.add_argument("--output", choices=("json", "text"), default="json")
    evidence.set_defaults(func=cmd_policy)

    stage = subparsers.add_parser(
        "stage",
        help="Check a rollout state-machine transition against durable evidence",
    )
    stage.add_argument("--target", required=True, help="Rollout stage to enter")
    stage.add_argument("--record", required=True, help="Path to rollout record JSON")
    stage.add_argument("--output", choices=("json", "text"), default="json")
    stage.set_defaults(func=cmd_policy)

    autoload = subparsers.add_parser(
        "autoload",
        help="Verify or add the fleet-change policy skill to skills.auto_preload",
    )
    autoload.add_argument(
        "--write",
        action="store_true",
        help="Persist the policy skill for future new sessions",
    )
    autoload.add_argument("--output", choices=("json", "text"), default="json")
    autoload.set_defaults(func=cmd_policy)
