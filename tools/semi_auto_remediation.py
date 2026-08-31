"""Audit/state/circuit-breaker guard for semi-automatic remediation.

This module is intentionally side-effect free except for its local state and
JSONL audit files. Callers use it before Tier B actions to prove that a
snapshot exists, cap flapping retries, and leave rollback evidence for humans.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any, Literal

AttemptResult = Literal["pending", "success", "failure"]

_KEY_SEPARATOR = "\u241f"


class MissingSnapshotError(ValueError):
    """Raised when a Tier B action is requested without a snapshot path."""


class CircuitOpen(RuntimeError):
    """Raised when the rolling attempt limit has already been reached."""

    def __init__(self, escalation: dict[str, Any]) -> None:
        self.escalation = escalation
        super().__init__(
            f"Circuit open for {escalation['action']} on {escalation['target']}: "
            f"{escalation['attempt_count']}/{escalation['max_attempts']} attempts "
            "inside rolling window"
        )


@dataclass(frozen=True)
class ActionDecision:
    """Decision returned after a semi-auto action passes the gate."""

    action: str
    target: str
    allowed: bool
    attempt_id: str
    attempt_count: int
    snapshot_path: str
    rollback_pointer: str | None
    opened_at: float
    window_seconds: float
    max_attempts: int


class SemiAutoRemediationGate:
    """Persisted audit/state helper for Tier B semi-automatic actions.

    The gate tracks attempts per ``(action, target)`` over a rolling time
    window. An action is allowed only when a non-empty pre-action snapshot path
    is supplied and the existing in-window attempts are below ``max_attempts``.
    Every allow/deny and post-check is appended to JSONL for auditability.
    """

    def __init__(
        self,
        *,
        state_path: str | Path,
        audit_path: str | Path,
        max_attempts: int = 3,
        window_seconds: float = 3600.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.state_path = Path(state_path)
        self.audit_path = Path(audit_path)
        self.max_attempts = max_attempts
        self.window_seconds = float(window_seconds)

    def begin_action(
        self,
        action: str,
        target: str,
        *,
        snapshot_path: str | Path | None = None,
        rollback_pointer: str | None = None,
        now: float | None = None,
    ) -> ActionDecision:
        """Record and return an allow decision, or raise on missing snapshot/open circuit."""

        timestamp = self._timestamp(now)
        snapshot = str(snapshot_path).strip() if snapshot_path is not None else ""
        if not snapshot:
            raise MissingSnapshotError("snapshot_path is required before Tier B action")

        state = self._load_state()
        bucket = self._bucket(state, action, target)
        attempts = self._prune_attempts(bucket, timestamp)

        if len(attempts) >= self.max_attempts:
            escalation = self._escalation_event(
                action=action,
                target=target,
                attempts=attempts,
                snapshot_path=snapshot,
                now=timestamp,
            )
            self._append_audit(escalation)
            self._save_state(state)
            raise CircuitOpen(escalation)

        attempt_id = f"{int(timestamp * 1000)}-{len(attempts) + 1}"
        attempt = {
            "attempt_id": attempt_id,
            "opened_at": timestamp,
            "snapshot_path": snapshot,
            "rollback_pointer": rollback_pointer,
            "result": "pending",
        }
        attempts.append(attempt)
        bucket["attempts"] = attempts
        self._save_state(state)

        decision = ActionDecision(
            action=action,
            target=target,
            allowed=True,
            attempt_id=attempt_id,
            attempt_count=len(attempts),
            snapshot_path=snapshot,
            rollback_pointer=rollback_pointer,
            opened_at=timestamp,
            window_seconds=self.window_seconds,
            max_attempts=self.max_attempts,
        )
        self._append_audit(
            {
                "event": "attempt_allowed",
                "allowed": True,
                "action": action,
                "target": target,
                "attempt_id": attempt_id,
                "attempt_count": len(attempts),
                "max_attempts": self.max_attempts,
                "window_seconds": self.window_seconds,
                "snapshot_path": snapshot,
                "rollback_pointer": rollback_pointer,
                "timestamp": timestamp,
            }
        )
        return decision

    def record_post_check(
        self,
        decision: ActionDecision,
        *,
        result: AttemptResult,
        rollback_pointer: str,
        details: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> None:
        """Record the post-action verification result and rollback pointer."""

        if result not in {"success", "failure"}:
            raise ValueError("post-check result must be 'success' or 'failure'")
        if not str(rollback_pointer).strip():
            raise ValueError("rollback_pointer is required with post-check result")

        timestamp = self._timestamp(now)
        state = self._load_state()
        bucket = self._bucket(state, decision.action, decision.target)
        attempts = self._prune_attempts(bucket, timestamp)
        for attempt in attempts:
            if attempt.get("attempt_id") == decision.attempt_id:
                attempt["result"] = result
                attempt["closed_at"] = timestamp
                attempt["rollback_pointer"] = rollback_pointer
                break
        bucket["attempts"] = attempts
        self._save_state(state)

        self._append_audit(
            {
                "event": "post_check",
                "action": decision.action,
                "target": decision.target,
                "attempt_id": decision.attempt_id,
                "snapshot_path": decision.snapshot_path,
                "post_check_result": result,
                "rollback_pointer": rollback_pointer,
                "details": details or {},
                "timestamp": timestamp,
            }
        )

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"version": 1, "actions": {}}
        with self.state_path.open("r", encoding="utf-8") as fh:
            state = json.load(fh)
        if not isinstance(state, dict):
            raise ValueError(f"invalid semi-auto remediation state: {self.state_path}")
        state.setdefault("version", 1)
        state.setdefault("actions", {})
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
        tmp_path.replace(self.state_path)

    def _append_audit(self, event: dict[str, Any]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")))
            fh.write("\n")

    def _bucket(self, state: dict[str, Any], action: str, target: str) -> dict[str, Any]:
        key = self._key(action, target)
        actions = state.setdefault("actions", {})
        bucket = actions.setdefault(key, {"action": action, "target": target, "attempts": []})
        bucket.setdefault("action", action)
        bucket.setdefault("target", target)
        bucket.setdefault("attempts", [])
        return bucket

    def _prune_attempts(self, bucket: dict[str, Any], now: float) -> list[dict[str, Any]]:
        cutoff = now - self.window_seconds
        attempts = [
            attempt
            for attempt in bucket.get("attempts", [])
            if float(attempt.get("opened_at", 0.0)) > cutoff
        ]
        bucket["attempts"] = attempts
        return attempts

    def _escalation_event(
        self,
        *,
        action: str,
        target: str,
        attempts: list[dict[str, Any]],
        snapshot_path: str,
        now: float,
    ) -> dict[str, Any]:
        return {
            "event": "escalation",
            "allowed": False,
            "action": action,
            "target": target,
            "attempt_count": len(attempts),
            "max_attempts": self.max_attempts,
            "window_seconds": self.window_seconds,
            "snapshot_path": snapshot_path,
            "rollback_pointers": [
                attempt["rollback_pointer"]
                for attempt in attempts
                if attempt.get("rollback_pointer")
            ],
            "timestamp": now,
        }

    @staticmethod
    def _timestamp(now: float | None) -> float:
        return float(time.time() if now is None else now)

    @staticmethod
    def _key(action: str, target: str) -> str:
        return f"{action}{_KEY_SEPARATOR}{target}"
