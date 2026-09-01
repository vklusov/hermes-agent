#!/usr/bin/env python3
"""Quiet transition wrapper for VM Hermes scratch update rehearsal.

This wrapper runs the existing scratch-only rehearsal and emits only compact
READY/BLOCKED transitions. It never performs live checkout activation, gateway
lifecycle actions, dependency installation in the live venv, cleanup, or
pruning. Evidence directories are retained.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path("/home/wwolfy/.hermes/hermes-agent")
REHEARSAL = REPO / "scripts" / "vm_hermes_update_rehearsal.py"
DEFAULT_STATE = Path("/home/wwolfy/.hermes/state/vm_hermes_rehearsal_watchdog.json")
DEFAULT_RELEASE_ROOT = Path("/home/wwolfy/.hermes/fleet/releases")
DEFAULT_SCRATCH_ROOT = Path("/home/wwolfy/.hermes/fleet/scratch")


@dataclass(frozen=True)
class RehearsalRun:
    returncode: int
    stdout: str
    stderr: str
    release_dir: Path

    def report(self) -> dict[str, Any]:
        path = self.release_dir / "readiness.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "schema": 1,
                "status": "BLOCKED",
                "target_head": None,
                "base_head": None,
                "conflicts": [],
                "tests": [{"name": "rehearsal wrapper", "status": "FAIL"}],
                "release_dir": str(self.release_dir),
                "live_checkout_modified": None,
                "dry_run": False,
                "wrapper_error": f"could not read readiness.json: {type(exc).__name__}: {exc}",
                "returncode": self.returncode,
            }
        if not isinstance(data, dict):
            return {
                "schema": 1,
                "status": "BLOCKED",
                "target_head": None,
                "base_head": None,
                "conflicts": [],
                "tests": [{"name": "rehearsal wrapper", "status": "FAIL"}],
                "release_dir": str(self.release_dir),
                "live_checkout_modified": None,
                "dry_run": False,
                "wrapper_error": "readiness.json was not a JSON object",
                "returncode": self.returncode,
            }
        return data


@dataclass(frozen=True)
class RehearsalDecision:
    should_emit: bool
    transition: str
    current: dict[str, Any]
    previous: dict[str, Any] | None


def _test_signature(tests: list[Any]) -> list[dict[str, str]]:
    signature: list[dict[str, str]] = []
    for item in tests:
        if isinstance(item, dict):
            signature.append({
                "name": str(item.get("name", "")),
                "status": str(item.get("status", "")),
            })
    return signature


def _signature(report: dict[str, Any]) -> dict[str, Any]:
    conflicts = report.get("conflicts") if isinstance(report.get("conflicts"), list) else []
    tests = report.get("tests") if isinstance(report.get("tests"), list) else []
    return {
        "status": report.get("status"),
        "target_head": report.get("target_head"),
        "conflicts": [str(item) for item in conflicts],
        "tests": _test_signature(tests),
        "live_checkout_modified": report.get("live_checkout_modified"),
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


def update_rehearsal_state(report: dict[str, Any], state_file: Path) -> RehearsalDecision:
    state = _load_state(state_file)
    previous = state.get("last") if isinstance(state.get("last"), dict) else None
    current = _signature(report)
    status = str(current.get("status") or "UNKNOWN")
    previous_status = str(previous.get("status")) if previous else None

    if previous == current:
        transition = "unchanged_ready" if status == "READY" else "unchanged"
        should_emit = False
    elif previous is None and status == "READY":
        transition = "initial_ready"
        should_emit = False
    elif previous is None:
        transition = "first_blocked"
        should_emit = True
    elif previous_status != status and status == "READY":
        transition = "ready"
        should_emit = True
    elif previous_status != status:
        transition = "status_change"
        should_emit = True
    elif previous.get("target_head") != current.get("target_head"):
        transition = "target_change"
        should_emit = True
    elif previous.get("conflicts") != current.get("conflicts"):
        transition = "conflict_change"
        should_emit = True
    elif previous.get("tests") != current.get("tests"):
        transition = "test_change"
        should_emit = True
    elif previous.get("live_checkout_modified") != current.get("live_checkout_modified"):
        transition = "live_checkout_change"
        should_emit = True
    else:
        transition = "signature_change"
        should_emit = True

    state["version"] = 1
    state["last"] = current
    _save_state(state_file, state)
    return RehearsalDecision(should_emit=should_emit, transition=transition, current=report, previous=previous)


def format_rehearsal_transition(decision: RehearsalDecision) -> str:
    report = decision.current
    conflicts = report.get("conflicts") if isinstance(report.get("conflicts"), list) else []
    tests = report.get("tests") if isinstance(report.get("tests"), list) else []
    test_text = ",".join(
        f"{item.get('name')}:{item.get('status')}" for item in tests if isinstance(item, dict)
    ) or "none"
    conflict_text = ",".join(str(item) for item in conflicts) or "none"
    return (
        f"VM HERMES REHEARSAL {decision.transition.upper()}: "
        f"status={report.get('status')} target_head={report.get('target_head')} "
        f"conflicts={conflict_text} tests={test_text} "
        f"live_checkout_modified={report.get('live_checkout_modified')} "
        f"evidence={report.get('release_dir')}"
    )


def _release_name() -> str:
    return time.strftime("vm-hermes-rehearsal-watchdog-%Y%m%dT%H%M%SZ", time.gmtime())


def run_rehearsal(release_name: str, timeout: int, skip_tests: bool) -> RehearsalRun:
    release_dir = DEFAULT_RELEASE_ROOT / release_name
    cmd = [
        sys.executable,
        str(REHEARSAL),
        "--repo",
        str(REPO),
        "--scratch-root",
        str(DEFAULT_SCRATCH_ROOT),
        "--release-root",
        str(DEFAULT_RELEASE_ROOT),
        "--release-name",
        release_name,
    ]
    if skip_tests:
        cmd.append("--skip-tests")
    proc = subprocess.run(
        cmd,
        cwd=str(REPO),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return RehearsalRun(proc.returncode, proc.stdout, proc.stderr, release_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", default=str(DEFAULT_STATE))
    parser.add_argument("--release-name", default="")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args(argv)

    release_name = args.release_name or _release_name()
    run = run_rehearsal(release_name, args.timeout, args.skip_tests)
    decision = update_rehearsal_state(run.report(), Path(args.state_file).expanduser())
    if decision.should_emit:
        print(format_rehearsal_transition(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
