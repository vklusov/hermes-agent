#!/usr/bin/env python3
"""Deduplicate Prometheus alerts into root-cause transition messages.

This script is designed for Hermes no_agent cron jobs. It performs a read-only
GET against Prometheus ``/api/v1/alerts``, persists compact state under
``~/.hermes/state/``, and prints only incident transitions: first firing,
severity escalation, root-cause change, and recovery. Repeated unchanged firing
states produce empty stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - standalone fallback
    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))

STATE_VERSION = 1
DEFAULT_PROMETHEUS_URL = "http://127.0.0.1:9090"
DEFAULT_STATE_NAME = "prometheus_alert_deduper.json"
SEVERITY_RANKS = {
    "none": 0,
    "info": 1,
    "healthy": 1,
    "warning": 2,
    "warn": 2,
    "minor": 2,
    "error": 3,
    "critical": 4,
    "crit": 4,
}


def default_state_path() -> Path:
    return get_hermes_home() / "state" / DEFAULT_STATE_NAME


def _severity_rank(severity: str | None) -> int:
    if not severity:
        return SEVERITY_RANKS["warning"]
    return SEVERITY_RANKS.get(str(severity).strip().lower(), SEVERITY_RANKS["warning"])


def _severity_name(alert: dict[str, Any]) -> str:
    labels = alert.get("labels") or {}
    value = str(labels.get("severity") or "warning").strip().lower()
    return "warning" if value == "warn" else value


def _alert_text(alert: dict[str, Any]) -> str:
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    haystack = []
    for src in (labels, annotations):
        for key, value in sorted(src.items()):
            haystack.append(f"{key}={value}")
    return "\n".join(haystack).lower()


def _host(alert: dict[str, Any]) -> str:
    labels = alert.get("labels") or {}
    for key in ("instance_name", "nodename", "node", "host", "hostname"):
        value = labels.get(key)
        if value:
            return str(value)
    instance = str(labels.get("instance") or "unknown")
    return instance.split(":", 1)[0]


def _fingerprint(alert: dict[str, Any]) -> str:
    if alert.get("fingerprint"):
        return str(alert["fingerprint"])
    labels = alert.get("labels") or {}
    payload = json.dumps(labels, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _is_target_down(alert: dict[str, Any]) -> bool:
    labels = alert.get("labels") or {}
    alertname = str(labels.get("alertname") or "").lower()
    if alertname in {
        "instancedown",
        "targetdown",
        "nodexporterdown",
        "nodeexporterdown",
        "prometheustargetdown",
        "updown",
    }:
        return True
    if any(token in alertname for token in ("instancedown", "targetdown", "nodeexporterdown")):
        return True
    text = _alert_text(alert)
    return "up == 0" in text or "target down" in text or "node_exporter" in text and "down" in text


def root_cause_for(alert: dict[str, Any], all_alerts: Iterable[dict[str, Any]] | None = None) -> str:
    """Return a stable root-cause bucket for one alert.

    mac93 target-down alerts are grouped into a dedicated Tailnet/node_exporter
    bucket when the alert itself or a sibling mac93 alert carries diagnostic
    evidence mentioning Tailscale/Tailnet/node_exporter reachability.
    """
    labels = alert.get("labels") or {}
    host = _host(alert).lower()
    alertname = str(labels.get("alertname") or "unknown")
    text = _alert_text(alert)
    sibling_text = ""
    if all_alerts:
        sibling_text = "\n".join(
            _alert_text(other)
            for other in all_alerts
            if _host(other).lower() == host
        )
    evidence = f"{text}\n{sibling_text}"

    if host in {"mac93", "mac-93", "m2", "m2pro", "mac-mini-93"} and _is_target_down(alert):
        if any(token in evidence for token in ("tailscale", "tailnet", "node_exporter", "node-exporter", "9100")):
            return "mac93_tailnet_node_exporter_down"
        return "mac93_tailnet_node_exporter_suspected_down"

    if _is_target_down(alert):
        return f"target_down:{host}"

    mountpoint = labels.get("mountpoint")
    if mountpoint:
        return f"disk:{host}:{mountpoint}"

    service = labels.get("service") or labels.get("job") or labels.get("name")
    if service:
        return f"{alertname}:{host}:{service}"
    return f"{alertname}:{host}"


def _group_key(root_cause: str) -> str:
    digest = hashlib.sha256(root_cause.encode("utf-8")).hexdigest()[:12]
    return f"rc:{digest}"


def normalize_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    status = payload.get("status")
    if status not in (None, "success"):
        raise ValueError(f"Prometheus API returned status={status!r}")
    data = payload.get("data") or {}
    alerts = data.get("alerts") or []
    if not isinstance(alerts, list):
        raise ValueError("Prometheus API data.alerts is not a list")
    firing = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        state = str(alert.get("state") or "firing").lower()
        if state == "firing":
            firing.append(alert)
    return firing


def build_incidents(alerts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for alert in alerts:
        root_cause = root_cause_for(alert, alerts)
        grouped.setdefault(root_cause, []).append(alert)

    incidents: dict[str, dict[str, Any]] = {}
    for root_cause, group_alerts in sorted(grouped.items()):
        severities = [_severity_name(a) for a in group_alerts]
        max_severity = max(severities, key=_severity_rank) if severities else "warning"
        labels = [a.get("labels") or {} for a in group_alerts]
        hosts = sorted({_host(a) for a in group_alerts})
        alertnames = sorted({str(label.get("alertname") or "unknown") for label in labels})
        fingerprints = sorted(_fingerprint(a) for a in group_alerts)
        active_ats = sorted(str(a.get("activeAt") or "") for a in group_alerts if a.get("activeAt"))
        incidents[_group_key(root_cause)] = {
            "root_cause": root_cause,
            "severity": max_severity,
            "severity_rank": _severity_rank(max_severity),
            "alert_count": len(group_alerts),
            "alertnames": alertnames,
            "hosts": hosts,
            "fingerprints": fingerprints,
            "active_since": active_ats[0] if active_ats else None,
            "signature": hashlib.sha256(
                json.dumps(
                    {
                        "root_cause": root_cause,
                        "alertnames": alertnames,
                        "hosts": hosts,
                        "fingerprints": fingerprints,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:16],
        }
    return incidents


def empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "incidents": {}}


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        return empty_state()
    incidents = state.get("incidents")
    if not isinstance(incidents, dict):
        state["incidents"] = {}
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(state, sort_keys=True, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(encoded)
        tmp_name = handle.name
    os.replace(tmp_name, path)


def format_down(kind: str, incident: dict[str, Any], previous: dict[str, Any] | None = None) -> str:
    severity = str(incident["severity"]).upper()
    hosts = ",".join(incident["hosts"])
    alertnames = ",".join(incident["alertnames"])
    since = incident.get("active_since") or "unknown"
    reason = incident["root_cause"]
    count = incident["alert_count"]
    if kind == "ESCALATION":
        old = str((previous or {}).get("severity") or "unknown").upper()
        prefix = f"PROMETHEUS ESCALATION {old}->{severity}"
    elif kind == "ROOT_CAUSE_CHANGE":
        prefix = "PROMETHEUS ROOT_CAUSE_CHANGE"
    else:
        prefix = f"PROMETHEUS FIRING {severity}"
    return f"{prefix}: {reason} hosts={hosts} alerts={alertnames} count={count} active_since={since}"


def format_recovery(previous: dict[str, Any]) -> str:
    severity = str(previous.get("severity") or "unknown").upper()
    hosts = ",".join(previous.get("hosts") or ["unknown"])
    alertnames = ",".join(previous.get("alertnames") or ["unknown"])
    since = previous.get("active_since") or "unknown"
    reason = previous.get("root_cause") or "unknown"
    return f"PROMETHEUS RECOVERY {severity}: {reason} hosts={hosts} alerts={alertnames} previously_active_since={since}"


def transition_messages(previous_state: dict[str, Any], current_incidents: dict[str, dict[str, Any]]) -> list[str]:
    previous_incidents = previous_state.get("incidents") or {}
    messages: list[str] = []

    for key, incident in sorted(current_incidents.items()):
        previous = previous_incidents.get(key)
        if not previous:
            messages.append(format_down("FIRING", incident))
            continue
        if int(incident.get("severity_rank", 0)) > int(previous.get("severity_rank", 0)):
            messages.append(format_down("ESCALATION", incident, previous))
            continue
        if incident.get("signature") != previous.get("signature"):
            messages.append(format_down("ROOT_CAUSE_CHANGE", incident, previous))

    for key, previous in sorted(previous_incidents.items()):
        if key not in current_incidents:
            messages.append(format_recovery(previous))

    return messages


def fetch_prometheus_alerts(base_url: str, timeout: float) -> dict[str, Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", "api/v1/alerts")
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - operator-provided Prometheus URL
        body = response.read()
    return json.loads(body.decode("utf-8"))


def fetch_prometheus_alerts_via_ssh(host: str, prometheus_url: str, timeout: float) -> dict[str, Any]:
    """Read Prometheus alerts from a remote host without changing remote state."""
    url = urllib.parse.urljoin(prometheus_url.rstrip("/") + "/", "api/v1/alerts")
    connect_timeout = max(1, int(timeout))
    remote_timeout = max(1, int(timeout))
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        host,
        "curl",
        "-fsS",
        "--max-time",
        str(remote_timeout),
        url,
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout + 5)
    return json.loads(completed.stdout)


def run(payload: dict[str, Any], state_path: Path, *, save: bool = True) -> list[str]:
    previous = load_state(state_path)
    current_incidents = build_incidents(normalize_alerts(payload))
    messages = transition_messages(previous, current_incidents)
    next_state = {"version": STATE_VERSION, "incidents": current_incidents}
    if save:
        save_state(state_path, next_state)
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prometheus-url",
        default=os.environ.get("PROMETHEUS_URL", DEFAULT_PROMETHEUS_URL),
        help="Prometheus base URL on vps-new; default: %(default)s",
    )
    parser.add_argument(
        "--ssh-host",
        default=os.environ.get("PROMETHEUS_SSH_HOST", "vps-new"),
        help="SSH host used to query vps-new-local Prometheus; empty string disables SSH.",
    )
    parser.add_argument("--state-file", type=Path, default=default_state_path())
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--alerts-json",
        type=Path,
        help="Read a saved Prometheus /api/v1/alerts JSON payload instead of making a network request.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Compute messages without writing state; useful for rehearsals only.",
    )
    args = parser.parse_args(argv)

    try:
        if args.alerts_json:
            payload = json.loads(args.alerts_json.read_text(encoding="utf-8"))
        elif args.ssh_host:
            payload = fetch_prometheus_alerts_via_ssh(args.ssh_host, args.prometheus_url, args.timeout)
        else:
            payload = fetch_prometheus_alerts(args.prometheus_url, args.timeout)
        messages = run(payload, args.state_file, save=not args.no_save)
    except (OSError, subprocess.SubprocessError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        print(f"PROMETHEUS ALERT DEDUPER ERROR: {exc}", file=sys.stderr)
        return 2

    if messages:
        print("\n".join(messages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
