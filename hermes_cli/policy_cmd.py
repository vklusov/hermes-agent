from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

POLICY_ID = "hermes-fleet-change-policy"
POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / f"{POLICY_ID}.yaml"


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


def _emit(payload: dict[str, Any], output: str) -> None:
    if output == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if "allowed" in payload:
        verdict = "allowed" if payload["allowed"] else "blocked"
        print(f"{verdict}: {payload['before']}")
        if payload["missing_fields"]:
            print("missing: " + ", ".join(payload["missing_fields"]))
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
