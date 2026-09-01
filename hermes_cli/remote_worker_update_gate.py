"""Policy gate for autonomous clean-tree remote worker Hermes updates.

This module contains the deterministic decision layer used by Vadim's daily
remote-worker update automation.  It is intentionally small and dependency-light:
cron/agent code can call it with real SSH operations, while tests inject pure
Python callables to exercise clean, dirty, and failure outcomes without touching
live hosts.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


CONTROL_PLANE_NAMES = frozenset(
    {
        "archivarius",
        "control-plane",
        "control_plane",
        "localhost",
        "local",
        "vm-hermes",
        "vm_hermes",
    }
)
CONTROL_PLANE_ROLES = frozenset({"control-plane", "control_plane", "prod"})
REQUIRED_ALLOWED_ACTIONS = frozenset(
    {"git_fetch", "git_pull_ff_only", "install_editable"}
)


@dataclass(frozen=True)
class HostSpec:
    name: str
    repo_path: str
    role: str = "worker"


Operation = Callable[..., dict[str, Any]]


@contextmanager
def _exclusive_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def load_fleet_approval(policy_path: Path | str, *, job_id: str | None = None) -> dict[str, Any]:
    """Load machine-readable fleet approval policy from cron jobs storage."""

    data = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        raise ValueError("policy file must contain a jobs list")

    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job_id is not None and job.get("id") != job_id:
            continue
        approval = job.get("fleet_approval")
        if isinstance(approval, dict):
            return approval
    suffix = f" for job {job_id}" if job_id else ""
    raise ValueError(f"fleet_approval policy not found{suffix}")


def _is_control_plane(host: HostSpec) -> bool:
    return host.role.strip().lower() in CONTROL_PLANE_ROLES or host.name.strip().lower() in CONTROL_PLANE_NAMES


def _policy_allows_host(policy: dict[str, Any], host: HostSpec) -> tuple[bool, str | None]:
    scopes = policy.get("scopes") if isinstance(policy, dict) else None
    scopes = scopes if isinstance(scopes, dict) else {}
    hosts = {str(item).strip().lower() for item in scopes.get("hosts", []) if str(item).strip()}
    repos = {str(item).rstrip("/") for item in scopes.get("repos", []) if str(item).strip()}
    actions = {str(item).strip() for item in policy.get("allowed_actions", []) if str(item).strip()}

    if _is_control_plane(host):
        return False, "control_plane_excluded"
    if host.name.strip().lower() not in hosts:
        return False, "host_not_whitelisted"
    if host.repo_path.rstrip("/") not in repos:
        return False, "repo_not_whitelisted"
    if not REQUIRED_ALLOWED_ACTIONS.issubset(actions):
        return False, "actions_not_whitelisted"
    if not policy.get("evidence_target"):
        return False, "missing_evidence_target"
    return True, None


def _blocked_state_reason(state: dict[str, Any]) -> str | None:
    if state.get("conflicts"):
        return "conflicts"
    if not state.get("clean"):
        return "dirty_tree"
    try:
        ahead = int(state.get("ahead") or 0)
    except (TypeError, ValueError):
        ahead = 0
    if ahead > 0:
        return "ahead_commits"
    return None


def _expert_approved(result: dict[str, Any]) -> bool:
    return str(result.get("status") or "").strip().lower() in {"approved", "ok", "pass"}


def run_worker_update_gate(
    *,
    policy_path: Path | str,
    hosts: Iterable[HostSpec],
    lock_path: Path | str,
    dry_run: bool,
    git_state: Operation,
    snapshot: Operation,
    ask_expert: Callable[[dict[str, Any]], dict[str, Any]],
    update: Operation,
    smoke: Operation,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Run the clean-tree worker update gate.

    The git clean/ahead/conflict check happens under an OS file lock. Dirty,
    ahead, conflict, non-whitelisted, and control-plane states fail closed before
    snapshot/expert/update. A real update requires, in order, a pre-action
    snapshot, an ask_expert approval, the update operation, and a passing smoke.
    """

    policy = load_fleet_approval(policy_path, job_id=job_id)
    results: list[dict[str, Any]] = []

    with _exclusive_lock(Path(lock_path)):
        for host in hosts:
            host_result: dict[str, Any] = {
                "host": host.name,
                "repo_path": host.repo_path,
                "role": host.role,
            }
            allowed, policy_reason = _policy_allows_host(policy, host)
            if not allowed:
                host_result.update({"decision": "blocked", "reason": policy_reason})
                results.append(host_result)
                continue

            state = git_state(host)
            host_result["git_state"] = state
            blocked_reason = _blocked_state_reason(state)
            if blocked_reason:
                host_result.update({"decision": "blocked", "reason": blocked_reason})
                results.append(host_result)
                continue

            if dry_run:
                host_result.update({"decision": "would_update", "reason": "dry_run"})
                results.append(host_result)
                continue

            snap = snapshot(host)
            rollback_pointer = snap.get("rollback_pointer") or state.get("head")
            host_result["snapshot"] = snap
            host_result["rollback_pointer"] = rollback_pointer

            advisory = ask_expert(
                {
                    "host": host.name,
                    "repo_path": host.repo_path,
                    "policy": policy,
                    "git_state": state,
                    "snapshot": snap,
                    "decision": "update_clean_worker",
                    "risk_level": "high",
                }
            )
            host_result["ask_expert"] = advisory
            if not _expert_approved(advisory):
                host_result.update({"decision": "blocked", "reason": "expert_not_approved"})
                results.append(host_result)
                continue

            try:
                update_result = update(host)
            except Exception as exc:  # pragma: no cover - exercised by callers
                host_result.update({"decision": "escalate", "reason": "update_failed", "error": str(exc)})
                results.append(host_result)
                continue
            host_result["update"] = update_result
            if update_result.get("ok") is False:
                host_result.update({"decision": "escalate", "reason": "update_failed"})
                results.append(host_result)
                continue

            try:
                smoke_result = smoke(host)
            except Exception as exc:  # pragma: no cover - exercised by callers
                host_result.update({"decision": "escalate", "reason": "smoke_failed", "error": str(exc)})
                results.append(host_result)
                continue
            host_result["smoke"] = smoke_result
            if not smoke_result.get("ok"):
                host_result.update({"decision": "escalate", "reason": "smoke_failed"})
                results.append(host_result)
                continue

            host_result.update({"decision": "updated", "reason": "clean_worker_smoke_passed"})
            results.append(host_result)

    decisions = {item.get("decision") for item in results}
    if "escalate" in decisions:
        status = "escalate"
    elif dry_run:
        status = "dry_run"
    elif decisions and decisions <= {"updated"}:
        status = "ok"
    elif "blocked" in decisions:
        status = "blocked"
    else:
        status = "ok"

    return {"status": status, "policy_job_id": policy.get("job_id"), "hosts": results}
