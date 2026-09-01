from __future__ import annotations

import json
from pathlib import Path

from scripts.vm_hermes_rehearsal_watchdog import (
    RehearsalRun,
    format_rehearsal_transition,
    update_rehearsal_state,
)


def _report(
    status: str,
    *,
    target_head: str = "target-a",
    conflicts: list[str] | None = None,
    tests: list[dict] | None = None,
    release_dir: str = "/tmp/release-a",
) -> dict:
    return {
        "schema": 1,
        "status": status,
        "target_head": target_head,
        "base_head": "base-a",
        "conflicts": conflicts or [],
        "tests": tests if tests is not None else [{"name": "schema smoke", "status": "PASS"}],
        "release_dir": release_dir,
        "live_checkout_modified": False,
        "dry_run": False,
    }


def test_first_blocked_emits_then_unchanged_is_silent(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    first = update_rehearsal_state(_report("BLOCKED", conflicts=["abc"]), state)
    second = update_rehearsal_state(_report("BLOCKED", conflicts=["abc"]), state)

    assert first.should_emit is True
    assert first.transition == "first_blocked"
    assert second.should_emit is False
    assert second.transition == "unchanged"
    assert json.loads(state.read_text())["last"]["conflicts"] == ["abc"]


def test_ready_after_blocked_emits_recovery(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    update_rehearsal_state(_report("BLOCKED", conflicts=["abc"]), state)

    ready = update_rehearsal_state(_report("READY", target_head="target-a", release_dir="/tmp/release-b"), state)
    repeated = update_rehearsal_state(_report("READY", target_head="target-a", release_dir="/tmp/release-c"), state)

    assert ready.should_emit is True
    assert ready.transition == "ready"
    assert repeated.should_emit is False
    assert repeated.transition == "unchanged_ready"


def test_target_conflict_and_test_changes_emit(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    update_rehearsal_state(_report("BLOCKED", target_head="target-a", conflicts=["abc"]), state)

    target_changed = update_rehearsal_state(_report("BLOCKED", target_head="target-b", conflicts=["abc"]), state)
    conflict_changed = update_rehearsal_state(_report("BLOCKED", target_head="target-b", conflicts=["def"]), state)
    test_changed = update_rehearsal_state(
        _report("BLOCKED", target_head="target-b", conflicts=["def"], tests=[{"name": "schema smoke", "status": "FAIL"}]),
        state,
    )

    assert target_changed.should_emit is True
    assert target_changed.transition == "target_change"
    assert conflict_changed.should_emit is True
    assert conflict_changed.transition == "conflict_change"
    assert test_changed.should_emit is True
    assert test_changed.transition == "test_change"


def test_format_includes_evidence_path_and_never_claims_live_activation(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    decision = update_rehearsal_state(_report("BLOCKED", conflicts=["abc"], release_dir="/evidence/run1"), state)
    message = format_rehearsal_transition(decision)

    assert "VM HERMES REHEARSAL FIRST_BLOCKED" in message
    assert "status=BLOCKED" in message
    assert "conflicts=abc" in message
    assert "evidence=/evidence/run1" in message
    assert "live_checkout_modified=False" in message
    assert "live activation" not in message.lower()


def test_rehearsal_run_loads_readiness_json(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    report = _report("READY", release_dir=str(release))
    (release / "readiness.json").write_text(json.dumps(report), encoding="utf-8")

    run = RehearsalRun(returncode=0, stdout=f"noise\n{release}\n", stderr="", release_dir=release)

    assert run.report()["status"] == "READY"
