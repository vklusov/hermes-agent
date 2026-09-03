import json
from pathlib import Path

from tools.semi_auto_remediation import (
    CircuitOpen,
    MissingSnapshotError,
    SemiAutoRemediationGate,
)


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_requires_pre_action_snapshot_before_attempt_is_allowed(tmp_path):
    gate = SemiAutoRemediationGate(
        state_path=tmp_path / "state.json",
        audit_path=tmp_path / "audit.jsonl",
    )

    try:
        gate.begin_action("restart_gateway", "fedor")
    except MissingSnapshotError as exc:
        assert "snapshot_path" in str(exc)
    else:
        raise AssertionError("begin_action allowed a Tier B action without snapshot_path")

    assert not (tmp_path / "state.json").exists()
    assert not (tmp_path / "audit.jsonl").exists()


def test_success_records_attempt_post_check_and_rollback_pointer(tmp_path):
    gate = SemiAutoRemediationGate(
        state_path=tmp_path / "state.json",
        audit_path=tmp_path / "audit.jsonl",
    )

    decision = gate.begin_action(
        "restart_gateway",
        "fedor",
        snapshot_path="/snapshots/fedor-before.json",
        rollback_pointer="git:refs/rollback/fedor-before",
        now=100.0,
    )
    assert decision.allowed is True
    assert decision.attempt_count == 1

    gate.record_post_check(
        decision,
        result="success",
        rollback_pointer="git:refs/rollback/fedor-before",
        details={"health": "ok"},
        now=101.0,
    )

    entries = read_jsonl(tmp_path / "audit.jsonl")
    assert [entry["event"] for entry in entries] == ["attempt_allowed", "post_check"]
    assert entries[0]["snapshot_path"] == "/snapshots/fedor-before.json"
    assert entries[1]["post_check_result"] == "success"
    assert entries[1]["rollback_pointer"] == "git:refs/rollback/fedor-before"
    assert entries[1]["details"] == {"health": "ok"}


def test_failure_and_flapping_attempts_are_tracked_over_rolling_window(tmp_path):
    gate = SemiAutoRemediationGate(
        state_path=tmp_path / "state.json",
        audit_path=tmp_path / "audit.jsonl",
        max_attempts=3,
        window_seconds=60,
    )

    first = gate.begin_action("restart_gateway", "fedor", snapshot_path="/snap/1", now=100.0)
    gate.record_post_check(first, result="failure", rollback_pointer="rollback-1", now=101.0)

    second = gate.begin_action("restart_gateway", "fedor", snapshot_path="/snap/2", now=120.0)
    gate.record_post_check(second, result="success", rollback_pointer="rollback-2", now=121.0)

    third = gate.begin_action("restart_gateway", "fedor", snapshot_path="/snap/3", now=150.0)

    assert first.attempt_count == 1
    assert second.attempt_count == 2
    assert third.attempt_count == 3

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    attempts = state["actions"]["restart_gateway\u241ffedor"]["attempts"]
    assert [attempt["result"] for attempt in attempts] == ["failure", "success", "pending"]

    after_window = gate.begin_action("restart_gateway", "fedor", snapshot_path="/snap/4", now=179.5)
    assert after_window.attempt_count == 3
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    attempts = state["actions"]["restart_gateway\u241ffedor"]["attempts"]
    assert [attempt["snapshot_path"] for attempt in attempts] == ["/snap/2", "/snap/3", "/snap/4"]


def test_escalates_and_stops_after_max_attempts_in_window(tmp_path):
    gate = SemiAutoRemediationGate(
        state_path=tmp_path / "state.json",
        audit_path=tmp_path / "audit.jsonl",
        max_attempts=2,
        window_seconds=300,
    )

    first = gate.begin_action("restart_gateway", "fedor", snapshot_path="/snap/1", now=100.0)
    gate.record_post_check(first, result="failure", rollback_pointer="rollback-1", now=101.0)
    second = gate.begin_action("restart_gateway", "fedor", snapshot_path="/snap/2", now=120.0)
    gate.record_post_check(second, result="failure", rollback_pointer="rollback-2", now=121.0)

    try:
        gate.begin_action("restart_gateway", "fedor", snapshot_path="/snap/3", now=130.0)
    except CircuitOpen as exc:
        escalation = exc.escalation
    else:
        raise AssertionError("circuit breaker allowed attempt after max_attempts")

    assert escalation["event"] == "escalation"
    assert escalation["action"] == "restart_gateway"
    assert escalation["target"] == "fedor"
    assert escalation["attempt_count"] == 2
    assert escalation["max_attempts"] == 2
    assert escalation["rollback_pointers"] == ["rollback-1", "rollback-2"]

    entries = read_jsonl(tmp_path / "audit.jsonl")
    assert entries[-1]["event"] == "escalation"
    assert entries[-1]["allowed"] is False
