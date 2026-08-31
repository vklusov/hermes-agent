from __future__ import annotations

import json
from pathlib import Path

from scripts.monitoring import prometheus_alert_deduper as deduper


def _payload(*alerts):
    return {"status": "success", "data": {"alerts": list(alerts)}}


def _alert(alertname="InstanceDown", severity="warning", **labels):
    merged = {
        "alertname": alertname,
        "severity": severity,
        "instance_name": "mac93",
        "instance": "100.64.0.93:9100",
        **labels,
    }
    return {
        "state": "firing",
        "labels": merged,
        "annotations": {"summary": "node_exporter down over Tailnet"},
        "activeAt": "2026-08-31T10:00:00Z",
        "fingerprint": f"fp-{alertname}-{severity}-{merged.get('job', 'node')}",
    }


def test_first_firing_then_unchanged_empty_then_recovery(tmp_path: Path):
    state_path = tmp_path / "state" / "prometheus_alert_deduper.json"
    first = deduper.run(_payload(_alert()), state_path)
    assert first == [
        "PROMETHEUS FIRING WARNING: mac93_tailnet_node_exporter_down "
        "hosts=mac93 alerts=InstanceDown count=1 active_since=2026-08-31T10:00:00Z"
    ]

    assert deduper.run(_payload(_alert()), state_path) == []

    recovery = deduper.run(_payload(), state_path)
    assert recovery == [
        "PROMETHEUS RECOVERY WARNING: mac93_tailnet_node_exporter_down "
        "hosts=mac93 alerts=InstanceDown previously_active_since=2026-08-31T10:00:00Z"
    ]


def test_severity_escalation_emits_once(tmp_path: Path):
    state_path = tmp_path / "dedupe.json"
    assert deduper.run(_payload(_alert(severity="warning")), state_path)

    escalation = deduper.run(_payload(_alert(severity="critical")), state_path)
    assert escalation == [
        "PROMETHEUS ESCALATION WARNING->CRITICAL: mac93_tailnet_node_exporter_down "
        "hosts=mac93 alerts=InstanceDown count=1 active_since=2026-08-31T10:00:00Z"
    ]
    assert deduper.run(_payload(_alert(severity="critical")), state_path) == []


def test_root_cause_change_for_same_bucket_emits(tmp_path: Path):
    state_path = tmp_path / "dedupe.json"
    assert deduper.run(_payload(_alert(job="node")), state_path)

    changed = deduper.run(_payload(_alert(job="node", fingerprint="not-used")), state_path)
    assert changed == []

    second = _alert(job="node")
    second["fingerprint"] = "different-fingerprint"
    changed = deduper.run(_payload(second), state_path)
    assert changed == [
        "PROMETHEUS ROOT_CAUSE_CHANGE: mac93_tailnet_node_exporter_down "
        "hosts=mac93 alerts=InstanceDown count=1 active_since=2026-08-31T10:00:00Z"
    ]


def test_state_schema_is_versioned_and_compact(tmp_path: Path):
    state_path = tmp_path / "dedupe.json"
    deduper.run(_payload(_alert()), state_path)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["version"] == 1
    assert set(state) == {"version", "incidents"}
    incident = next(iter(state["incidents"].values()))
    assert set(incident) == {
        "root_cause",
        "severity",
        "severity_rank",
        "alert_count",
        "alertnames",
        "hosts",
        "fingerprints",
        "active_since",
        "signature",
    }
    assert incident["root_cause"] == "mac93_tailnet_node_exporter_down"
