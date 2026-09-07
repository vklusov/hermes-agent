"""Native fleet rollout gate helpers for Hermes automation policy.

The validator defines the policy schema.  This module is the small runtime
surface other native paths can call before fleet-affecting mutations.  It does
not perform side effects; it classifies a proposed decision and returns a
machine-readable allow/block result.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from policy.validate_automation_policy import DEFAULT_POLICY_PATH, validate_policy_file


FLEET_ORDER = ["fedor", "93", "archivarius"]
FLEET_AFFECTING_JOB_PATTERNS = (
    r"\bfleet\b",
    r"\bfedor\b",
    r"\barchivarius\b",
    r"\bmac[-_ ]?93\b",
    r"\bvm[-_ ]?hermes\b",
    r"\bremote[-_ ]?worker\b",
    r"\btailscale\b",
    r"\bamnezia\b",
    r"\bprovider[-_ ]?routing\b",
    r"\bconfig\.ya?ml\b",
    r"\bservice\s+(restart|stop|start)\b",
    r"\b(systemctl|launchctl|docker\s+restart)\b",
    r"\bgit\s+(pull|fetch|reset|clean|checkout|merge|cherry-pick)\b",
)


@dataclass(frozen=True)
class FleetGateDecision:
    """A proposed rollout/exception decision for a fleet-affecting mutation."""

    action_class: str
    affected_nodes: list[str]
    rollout_order: list[str] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    evidence_root: str = ""
    result: str = ""
    exception: str | None = None
    exception_reason: str = ""
    skipped_nodes: list[str] = field(default_factory=list)
    approval_ref: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FleetGateDecision":
        return cls(
            action_class=str(raw.get("action_class") or ""),
            affected_nodes=[str(node) for node in raw.get("affected_nodes") or []],
            rollout_order=[str(node) for node in raw.get("rollout_order") or []],
            approvals=[str(item) for item in raw.get("approvals") or []],
            evidence_root=str(raw.get("evidence_root") or ""),
            result=str(raw.get("result") or ""),
            exception=str(raw["exception"]) if raw.get("exception") is not None else None,
            exception_reason=str(raw.get("exception_reason") or ""),
            skipped_nodes=[str(node) for node in raw.get("skipped_nodes") or []],
            approval_ref=str(raw.get("approval_ref") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value not in (None, "", [])}


@dataclass(frozen=True)
class FleetGateResult:
    allowed: bool
    reason: str
    mode: str
    required: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "mode": self.mode,
            "required": self.required,
        }


class FleetGateError(ValueError):
    """Raised before a fleet-affecting mutation without native policy evidence."""


def scheduled_job_requires_fleet_gate(
    *,
    prompt: str = "",
    script: str | None = None,
    name: str | None = None,
    workdir: str | None = None,
    skills: list[str] | None = None,
) -> bool:
    """Return True for scheduled jobs likely to affect Hermes fleet/runtime.

    This is intentionally conservative for fleet/runtime markers while leaving
    ordinary local reminders and one-off personal automations alone.  Cron tools
    call this before persisting new/updated jobs; callers can satisfy the gate
    with either full staged rollout evidence or an explicit exception decision.
    """

    haystack = "\n".join(
        part
        for part in [prompt, script or "", name or "", workdir or "", " ".join(skills or [])]
        if part
    ).lower()
    if not haystack.strip():
        return False
    return any(re.search(pattern, haystack, re.IGNORECASE) for pattern in FLEET_AFFECTING_JOB_PATTERNS)


def evaluate_fleet_gate(
    decision: FleetGateDecision | Mapping[str, Any],
    *,
    policy_path: str | Path = DEFAULT_POLICY_PATH,
) -> FleetGateResult:
    """Evaluate whether a fleet-affecting decision satisfies native policy.

    A mutation is allowed only when it records either the default staged order
    or an explicit Archivarius-only exception with skipped-node and approval
    metadata.  This function is side-effect free so cron/gateway/CLI wrappers
    can call it before doing their own writes/restarts/updates.
    """

    policy = validate_policy_file(policy_path)
    gate = policy["native_fleet_gate"]
    dec = decision if isinstance(decision, FleetGateDecision) else FleetGateDecision.from_mapping(decision)

    required = {
        "default_rollout_order": gate["default_rollout_order"],
        "required_decision_fields": gate["required_decision_fields"],
        "exception_required_fields": gate["exception_required_fields"],
    }

    if dec.action_class not in set(gate["fleet_affecting_action_classes"]):
        return FleetGateResult(True, "not_fleet_affecting", gate["mode"], required)

    if dec.exception == "archivarius_only":
        missing = []
        for field_name in gate["exception_required_fields"]:
            value = getattr(dec, field_name, None)
            if value in (None, "", []):
                missing.append(field_name)
        if missing:
            return FleetGateResult(False, f"missing exception fields: {missing}", gate["mode"], required)
        if dec.affected_nodes != ["archivarius"]:
            return FleetGateResult(False, "archivarius_only exception must affect only archivarius", gate["mode"], required)
        if set(dec.skipped_nodes) != {"fedor", "93"}:
            return FleetGateResult(False, "archivarius_only exception must record skipped fedor and 93", gate["mode"], required)
        return FleetGateResult(True, "archivarius_only_exception_recorded", gate["mode"], required)

    missing = []
    for field_name in gate["required_decision_fields"]:
        value = getattr(dec, field_name, None)
        if value in (None, "", []):
            missing.append(field_name)
    if missing:
        return FleetGateResult(False, f"missing rollout fields: {missing}", gate["mode"], required)

    if dec.rollout_order != gate["default_rollout_order"]:
        return FleetGateResult(False, "rollout_order must be fedor -> 93 -> archivarius", gate["mode"], required)
    if dec.affected_nodes != gate["default_rollout_order"]:
        return FleetGateResult(False, "affected_nodes must match full staged fleet order", gate["mode"], required)

    return FleetGateResult(True, "staged_rollout_recorded", gate["mode"], required)


def require_fleet_gate(
    decision: FleetGateDecision | Mapping[str, Any] | None,
    *,
    action_class: str,
    mutation: str,
    policy_path: str | Path = DEFAULT_POLICY_PATH,
) -> FleetGateDecision:
    """Raise unless a fleet-affecting mutation has an allowed native decision."""

    if decision is None:
        raise FleetGateError(
            f"Native fleet policy gate blocked {mutation}: missing fleet_decision "
            f"for {action_class}. Record staged rollout evidence or an explicit "
            "archivarius_only exception before mutation."
        )
    dec = decision if isinstance(decision, FleetGateDecision) else FleetGateDecision.from_mapping(decision)
    if not dec.action_class:
        dec = FleetGateDecision.from_mapping({**dec.to_dict(), "action_class": action_class})
    result = evaluate_fleet_gate(dec, policy_path=policy_path)
    if not result.allowed:
        raise FleetGateError(f"Native fleet policy gate blocked {mutation}: {result.reason}")
    return dec


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Hermes native fleet policy gate")
    parser.add_argument("--decision-json", help="JSON object with fleet gate decision fields")
    parser.add_argument("--decision-file", help="Path to a JSON decision file")
    parser.add_argument("--action-class", required=True, help="Policy action class")
    parser.add_argument("--mutation", default="fleet mutation", help="Human-readable mutation label")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH), help="Policy YAML path")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def _load_cli_decision(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.decision_file:
        raw = Path(args.decision_file).read_text(encoding="utf-8")
    else:
        raw = args.decision_json
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("decision must be a JSON object")
    data.setdefault("action_class", args.action_class)
    return data


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    decision = _load_cli_decision(args)
    try:
        dec = require_fleet_gate(
            decision,
            action_class=args.action_class,
            mutation=args.mutation,
            policy_path=args.policy,
        )
        result = evaluate_fleet_gate(dec, policy_path=args.policy)
        payload = {"allowed": True, "decision": dec.to_dict(), "result": result.to_dict()}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(f"ALLOW: {result.reason}")
        return 0
    except Exception as exc:
        payload = {"allowed": False, "error": str(exc), "action_class": args.action_class, "mutation": args.mutation}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(f"BLOCK: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
