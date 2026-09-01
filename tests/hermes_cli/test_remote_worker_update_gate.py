from __future__ import annotations

import json
from pathlib import Path

import pytest


def _policy_file(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.json"
    path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "daily-worker-update",
                        "fleet_approval": {
                            "job_id": "daily-worker-update",
                            "allowed_actions": [
                                "git_fetch",
                                "git_pull_ff_only",
                                "install_editable",
                            ],
                            "scopes": {
                                "hosts": ["fedor", "mac93", "vps", "93"],
                                "repos": ["/srv/hermes-agent", "/Users/vadim/.hermes/hermes-agent"],
                            },
                            "evidence_target": str(tmp_path / "evidence.md"),
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_clean_worker_can_update_under_lock_with_snapshot_expert_and_smoke(tmp_path):
    from hermes_cli.remote_worker_update_gate import HostSpec, run_worker_update_gate

    calls: list[tuple[str, str]] = []

    def git_state(host: HostSpec):
        calls.append(("git_state", host.name))
        return {"clean": True, "ahead": 0, "conflicts": False, "head": "old123"}

    def snapshot(host: HostSpec):
        calls.append(("snapshot", host.name))
        return {"snapshot_path": f"/backups/{host.name}.tgz", "rollback_pointer": "old123"}

    def expert(payload):
        calls.append(("expert", payload["host"]))
        assert payload["policy"]["job_id"] == "daily-worker-update"
        assert payload["snapshot"]["rollback_pointer"] == "old123"
        return {"status": "approved", "reason": "clean worker allowed"}

    def update(host: HostSpec):
        calls.append(("update", host.name))
        return {"updated": True, "head": "new456"}

    def smoke(host: HostSpec):
        calls.append(("smoke", host.name))
        return {"ok": True, "detail": "health ok"}

    result = run_worker_update_gate(
        policy_path=_policy_file(tmp_path),
        hosts=[HostSpec(name="fedor", repo_path="/srv/hermes-agent")],
        lock_path=tmp_path / "gate.lock",
        dry_run=False,
        git_state=git_state,
        snapshot=snapshot,
        ask_expert=expert,
        update=update,
        smoke=smoke,
    )

    assert result["status"] == "ok"
    assert result["hosts"][0]["decision"] == "updated"
    assert result["hosts"][0]["snapshot"]["snapshot_path"] == "/backups/fedor.tgz"
    assert result["hosts"][0]["rollback_pointer"] == "old123"
    assert calls == [
        ("git_state", "fedor"),
        ("snapshot", "fedor"),
        ("expert", "fedor"),
        ("update", "fedor"),
        ("smoke", "fedor"),
    ]


def test_dirty_worker_blocks_before_snapshot_expert_or_update(tmp_path):
    from hermes_cli.remote_worker_update_gate import HostSpec, run_worker_update_gate

    forbidden = pytest.fail

    result = run_worker_update_gate(
        policy_path=_policy_file(tmp_path),
        hosts=[HostSpec(name="mac93", repo_path="/Users/vadim/.hermes/hermes-agent")],
        lock_path=tmp_path / "gate.lock",
        dry_run=False,
        git_state=lambda host: {"clean": False, "ahead": 0, "conflicts": False, "head": "abc"},
        snapshot=lambda host: forbidden("snapshot must not run for dirty trees"),
        ask_expert=lambda payload: forbidden("expert must not run for dirty trees"),
        update=lambda host: forbidden("update must not run for dirty trees"),
        smoke=lambda host: forbidden("smoke must not run for dirty trees"),
    )

    assert result["status"] == "blocked"
    assert result["hosts"][0]["decision"] == "blocked"
    assert result["hosts"][0]["reason"] == "dirty_tree"


def test_ahead_worker_blocks_before_update(tmp_path):
    from hermes_cli.remote_worker_update_gate import HostSpec, run_worker_update_gate

    result = run_worker_update_gate(
        policy_path=_policy_file(tmp_path),
        hosts=[HostSpec(name="fedor", repo_path="/srv/hermes-agent")],
        lock_path=tmp_path / "gate.lock",
        dry_run=False,
        git_state=lambda host: {"clean": True, "ahead": 1, "conflicts": False, "head": "abc"},
        snapshot=lambda host: pytest.fail("snapshot must not run for ahead trees"),
        ask_expert=lambda payload: pytest.fail("expert must not run for ahead trees"),
        update=lambda host: pytest.fail("update must not run for ahead trees"),
        smoke=lambda host: pytest.fail("smoke must not run for ahead trees"),
    )

    assert result["status"] == "blocked"
    assert result["hosts"][0]["reason"] == "ahead_commits"


def test_control_plane_is_never_eligible_even_if_policy_lists_it(tmp_path):
    from hermes_cli.remote_worker_update_gate import HostSpec, run_worker_update_gate

    policy_path = _policy_file(tmp_path)
    data = json.loads(policy_path.read_text(encoding="utf-8"))
    data["jobs"][0]["fleet_approval"]["scopes"]["hosts"].append("vm-hermes")
    data["jobs"][0]["fleet_approval"]["scopes"]["repos"].append("/home/wwolfy/.hermes/hermes-agent")
    policy_path.write_text(json.dumps(data), encoding="utf-8")

    result = run_worker_update_gate(
        policy_path=policy_path,
        hosts=[HostSpec(name="vm-hermes", repo_path="/home/wwolfy/.hermes/hermes-agent", role="control-plane")],
        lock_path=tmp_path / "gate.lock",
        dry_run=False,
        git_state=lambda host: pytest.fail("control-plane git state must not be checked for update"),
        snapshot=lambda host: pytest.fail("control-plane snapshot must not run"),
        ask_expert=lambda payload: pytest.fail("control-plane expert must not run"),
        update=lambda host: pytest.fail("control-plane update must not run"),
        smoke=lambda host: pytest.fail("control-plane smoke must not run"),
    )

    assert result["status"] == "blocked"
    assert result["hosts"][0]["decision"] == "blocked"
    assert result["hosts"][0]["reason"] == "control_plane_excluded"


def test_update_failure_escalates_with_rollback_pointer(tmp_path):
    from hermes_cli.remote_worker_update_gate import HostSpec, run_worker_update_gate

    result = run_worker_update_gate(
        policy_path=_policy_file(tmp_path),
        hosts=[HostSpec(name="fedor", repo_path="/srv/hermes-agent")],
        lock_path=tmp_path / "gate.lock",
        dry_run=False,
        git_state=lambda host: {"clean": True, "ahead": 0, "conflicts": False, "head": "old123"},
        snapshot=lambda host: {"snapshot_path": "/backups/fedor.tgz", "rollback_pointer": "old123"},
        ask_expert=lambda payload: {"status": "approved"},
        update=lambda host: {"ok": False, "detail": "pull failed"},
        smoke=lambda host: pytest.fail("smoke must not run after failed update"),
    )

    assert result["status"] == "escalate"
    assert result["hosts"][0]["decision"] == "escalate"
    assert result["hosts"][0]["reason"] == "update_failed"
    assert result["hosts"][0]["rollback_pointer"] == "old123"


def test_smoke_failure_escalates_with_rollback_pointer(tmp_path):
    from hermes_cli.remote_worker_update_gate import HostSpec, run_worker_update_gate

    result = run_worker_update_gate(
        policy_path=_policy_file(tmp_path),
        hosts=[HostSpec(name="fedor", repo_path="/srv/hermes-agent")],
        lock_path=tmp_path / "gate.lock",
        dry_run=False,
        git_state=lambda host: {"clean": True, "ahead": 0, "conflicts": False, "head": "old123"},
        snapshot=lambda host: {"snapshot_path": "/backups/fedor.tgz", "rollback_pointer": "old123"},
        ask_expert=lambda payload: {"status": "approved"},
        update=lambda host: {"ok": True, "updated": True, "head": "new456"},
        smoke=lambda host: {"ok": False, "detail": "health failed"},
    )

    assert result["status"] == "escalate"
    assert result["hosts"][0]["decision"] == "escalate"
    assert result["hosts"][0]["reason"] == "smoke_failed"
    assert result["hosts"][0]["rollback_pointer"] == "old123"


def test_dry_run_reports_clean_worker_without_mutation(tmp_path):
    from hermes_cli.remote_worker_update_gate import HostSpec, run_worker_update_gate

    result = run_worker_update_gate(
        policy_path=_policy_file(tmp_path),
        hosts=[HostSpec(name="fedor", repo_path="/srv/hermes-agent")],
        lock_path=tmp_path / "gate.lock",
        dry_run=True,
        git_state=lambda host: {"clean": True, "ahead": 0, "conflicts": False, "head": "old123"},
        snapshot=lambda host: pytest.fail("dry-run must not snapshot"),
        ask_expert=lambda payload: pytest.fail("dry-run must not ask expert"),
        update=lambda host: pytest.fail("dry-run must not update"),
        smoke=lambda host: pytest.fail("dry-run must not smoke"),
    )

    assert result["status"] == "dry_run"
    assert result["hosts"][0]["decision"] == "would_update"
