from __future__ import annotations

import pytest

from cron.jobs import create_job, load_jobs, update_job
from policy.fleet_gate import FleetGateError


@pytest.fixture()
def temp_cron_store(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    return tmp_path


FLEET_DECISION = {
    "action_class": "scheduled_job_creation_or_update",
    "affected_nodes": ["fedor", "93", "archivarius"],
    "rollout_order": ["fedor", "93", "archivarius"],
    "approvals": ["telegram:vadim:2026-09-03"],
    "evidence_root": "/home/wwolfy/.hermes/fleet/releases/native-fleet-policy-gate-test",
    "result": "preflight_passed",
}


def test_local_cron_job_does_not_require_fleet_decision(temp_cron_store):
    job = create_job(
        prompt="Remind me to drink water",
        schedule="every hour",
        name="local reminder",
        deliver="local",
    )

    assert job["name"] == "local reminder"
    assert "fleet_decision" not in job


def test_fleet_affecting_cron_job_requires_native_decision(temp_cron_store):
    with pytest.raises(FleetGateError, match="missing fleet_decision"):
        create_job(
            prompt="Run Fedor -> 93 -> Archivarius fleet provider routing audit",
            schedule="every day at 9am",
            name="fleet routing audit",
            deliver="local",
        )


def test_fleet_affecting_cron_job_persists_native_decision(temp_cron_store):
    job = create_job(
        prompt="Run Fedor -> 93 -> Archivarius fleet provider routing audit",
        schedule="every day at 9am",
        name="fleet routing audit",
        deliver="local",
        fleet_decision=FLEET_DECISION,
    )

    assert job["fleet_decision"]["rollout_order"] == ["fedor", "93", "archivarius"]
    assert load_jobs()[0]["fleet_decision"] == job["fleet_decision"]


def test_update_to_fleet_affecting_job_requires_decision(temp_cron_store):
    job = create_job(
        prompt="local note",
        schedule="every hour",
        name="local",
        deliver="local",
    )

    with pytest.raises(FleetGateError, match="missing fleet_decision"):
        update_job(job["id"], {"prompt": "Run fleet update on Fedor and Archivarius"})


def test_update_to_fleet_affecting_job_accepts_decision(temp_cron_store):
    job = create_job(
        prompt="local note",
        schedule="every hour",
        name="local",
        deliver="local",
    )

    updated = update_job(
        job["id"],
        {
            "prompt": "Run fleet update on Fedor and Archivarius",
            "fleet_decision": FLEET_DECISION,
        },
    )

    assert updated is not None
    assert updated["fleet_decision"]["result"] == "preflight_passed"
