#!/usr/bin/env python3
"""Quiet transition wrapper for Mac-93 Tailnet diagnostics.

The underlying diagnostic script exits non-zero when Mac-93 is unhealthy. That
is data, not a wrapper failure. This wrapper stores a compact signature and
prints only transitions so no_agent cron can stay silent on unchanged states.
It never mutates Tailscale, VPN, routes, launchd, or node_exporter.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path("/home/wwolfy/.hermes/hermes-agent")
DIAGNOSTIC = REPO / "scripts" / "observability" / "mac93_tailnet_diagnostic.py"
DEFAULT_STATE = Path("/home/wwolfy/.hermes/state/mac93_tailnet_diagnostic_watchdog.json")
SEVERITY_RANK = {"ok": 0, "info": 1, "warning": 2, "critical": 3}


@dataclass(frozen=True)
class DiagnosticRun:
    returncode: int
    stdout: str
    stderr: str

    def payload(self) -> dict[str, Any]:
        try:
            data = json.loads(self.stdout)
        except json.JSONDecodeError as exc:
            return {
                "target": {"name": "mac93"},
                "status": "diagnostic_error",
                "severity": "critical",
                "dedupe_key": f"mac93:diagnostic_error:json:{self.returncode}",
                "diagnosis": f"Mac-93 diagnostic returned non-JSON output: {exc}",
                "checks": {},
                "safety": {"mode": "read_only", "forbidden_actions_used": False},
                "wrapper_error": (self.stderr or self.stdout)[-500:],
            }
        if not isinstance(data, dict):
            return {
                "target": {"name": "mac93"},
                "status": "diagnostic_error",
                "severity": "critical",
                "dedupe_key": f"mac93:diagnostic_error:type:{self.returncode}",
                "diagnosis": "Mac-93 diagnostic returned a non-object JSON payload.",
                "checks": {},
                "safety": {"mode": "read_only", "forbidden_actions_used": False},
            }
        return data


@dataclass(frozen=True)
class WatchdogDecision:
    should_emit: bool
    transition: str
    current: dict[str, Any]
    previous: dict[str, Any] | None


def _signature(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": payload.get("status"),
        "severity": payload.get("severity"),
        "dedupe_key": payload.get("dedupe_key"),
    }


def _load_state(state_file: Path) -> dict[str, Any]:
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1}


def _save_state(state_file: Path, state: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(state_file.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(state_file)


def update_watchdog_state(payload: dict[str, Any], state_file: Path) -> WatchdogDecision:
    state = _load_state(state_file)
    previous = state.get("last") if isinstance(state.get("last"), dict) else None
    current = _signature(payload)
    status = str(current.get("status") or "unknown")
    severity = str(current.get("severity") or "warning")
    previous_status = str(previous.get("status")) if previous else None
    previous_severity = str(previous.get("severity")) if previous else None

    if previous == current:
        transition = "unchanged_healthy" if status == "healthy" else "unchanged"
        should_emit = False
    elif status == "healthy" and previous and previous_status != "healthy":
        transition = "recovery"
        should_emit = True
    elif not previous and status != "healthy":
        transition = "first_unhealthy"
        should_emit = True
    elif previous and status != previous_status:
        transition = "status_change"
        should_emit = True
    elif previous and SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(previous_severity or "ok", 0):
        transition = "severity_escalation"
        should_emit = True
    elif previous is None and status == "healthy":
        transition = "initial_healthy"
        should_emit = False
    else:
        transition = "signature_change"
        should_emit = True

    state["version"] = 1
    state["last"] = current
    _save_state(state_file, state)
    return WatchdogDecision(should_emit=should_emit, transition=transition, current=payload, previous=previous)


def format_transition(decision: WatchdogDecision) -> str:
    payload = decision.current
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    tailscale = checks.get("tailscale") if isinstance(checks.get("tailscale"), dict) else {}
    listen = checks.get("node_exporter_listen") if isinstance(checks.get("node_exporter_listen"), dict) else {}
    metrics = checks.get("metrics") if isinstance(checks.get("metrics"), dict) else {}
    safety = payload.get("safety") if isinstance(payload.get("safety"), dict) else {}
    return (
        f"MAC93 DIAGNOSTIC {decision.transition.upper()}: "
        f"status={payload.get('status')} severity={payload.get('severity')} "
        f"dedupe_key={payload.get('dedupe_key')} "
        f"tailscale={tailscale.get('backend_state')} online={tailscale.get('self_online')} "
        f"tailnet_bound={listen.get('tailnet_bound')} metrics_tailnet_ok={metrics.get('tailnet_ok')} "
        f"forbidden_actions_used={safety.get('forbidden_actions_used')} "
        f"diagnosis={payload.get('diagnosis')}"
    )


def run_diagnostic(timeout: int) -> DiagnosticRun:
    proc = subprocess.run(
        [sys.executable, str(DIAGNOSTIC)],
        cwd=str(REPO),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return DiagnosticRun(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", default=str(DEFAULT_STATE))
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)

    run = run_diagnostic(args.timeout)
    decision = update_watchdog_state(run.payload(), Path(args.state_file).expanduser())
    if decision.should_emit:
        print(format_transition(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
