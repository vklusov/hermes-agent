#!/usr/bin/env python3
"""Read-only diagnostics for the Mac-93 Tailnet Prometheus target.

The probe intentionally uses only SSH-backed read commands and curl checks. It
never starts, stops, restarts, unloads, loads, or reconfigures Tailscale,
node_exporter, launchd, VPN, or routes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

DEFAULT_HOST = "vadimklusov@192.168.1.93"
DEFAULT_IDENTITY_FILE = "~/.ssh/hermes_93"
DEFAULT_TAILNET_IP = "100.124.204.124"
DEFAULT_EXPORTER_PORT = 9100

# Machine-readable allowlist: every remote command is read-only. Keep this list
# explicit so the script can be audited without tracing string construction.
REMOTE_COMMANDS: dict[str, tuple[str, ...]] = {
    "bridge": ("/usr/bin/curl", "-fsS", "--max-time", "3", "http://127.0.0.1:8002/health"),
    "tailscale": ("/Applications/Tailscale.app/Contents/MacOS/Tailscale", "status", "--json"),
    "node_process": ("/usr/bin/pgrep", "-fl", "node_exporter"),
    "node_listen": ("/usr/sbin/lsof", "-nP", "-iTCP:9100", "-sTCP:LISTEN"),
    "metrics_local": ("/usr/bin/curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "3", "http://127.0.0.1:9100/metrics"),
    "metrics_tailnet": ("/usr/bin/curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "3"),
}

FORBIDDEN_REMOTE_TOKENS = re.compile(
    r"\b(start|stop|restart|reboot|shutdown|kill|pkill|launchctl|scutil|ifconfig|route|networksetup|tailscale\s+(up|down|set))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    command: str

    def compact(self, *, stdout_limit: int = 240, stderr_limit: int = 160) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": self.ok,
            "returncode": self.returncode,
        }
        if self.name == "tailscale" and self.stdout:
            data["summary"] = _parse_tailscale(self.stdout)
        elif self.stdout:
            data["stdout"] = _one_line(self.stdout, stdout_limit)
        if self.stderr and not self.ok:
            data["stderr"] = _one_line(self.stderr, stderr_limit)
        return data


def _one_line(value: str, limit: int) -> str:
    compact = " ".join(value.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _ssh_command(host: str, remote_args: tuple[str, ...], *, connect_timeout: int, identity_file: str | None = None) -> list[str]:
    remote = " ".join(shlex.quote(part) for part in remote_args)
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={connect_timeout}",
    ]
    if identity_file:
        cmd.extend(["-o", "IdentitiesOnly=yes", "-i", os.path.expanduser(identity_file)])
    cmd.extend([host, remote])
    return cmd


def _run_remote(
    name: str,
    host: str,
    remote_args: tuple[str, ...],
    *,
    connect_timeout: int,
    timeout: int,
    identity_file: str | None = None,
) -> ProbeResult:
    remote_text = " ".join(remote_args)
    if FORBIDDEN_REMOTE_TOKENS.search(remote_text):
        raise RuntimeError(f"refusing non-read-only remote command for {name}: {remote_text}")
    cmd = _ssh_command(host, remote_args, connect_timeout=connect_timeout, identity_file=identity_file)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return ProbeResult(
        name=name,
        ok=proc.returncode == 0,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        command=remote_text,
    )


def _run_all(host: str, tailnet_ip: str, exporter_port: int, connect_timeout: int, timeout: int, identity_file: str | None) -> dict[str, ProbeResult]:
    metrics_tailnet = REMOTE_COMMANDS["metrics_tailnet"] + (f"http://{tailnet_ip}:{exporter_port}/metrics",)
    commands = dict(REMOTE_COMMANDS)
    commands["metrics_tailnet"] = metrics_tailnet
    return {
        name: _run_remote(name, host, args, connect_timeout=connect_timeout, timeout=timeout, identity_file=identity_file)
        for name, args in commands.items()
    }


def _parse_tailscale(stdout: str) -> dict[str, Any]:
    try:
        data = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return {"parse_error": True, "backend_state": "unknown", "self_online": None, "health_count": None}
    self_info = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    health = data.get("Health")
    return {
        "backend_state": data.get("BackendState") or "unknown",
        "self_online": self_info.get("Online"),
        "self_tailscale_ip": _first_tailnet_ip(self_info),
        "health_count": len(health) if isinstance(health, list) else 0 if health == [] else None,
    }


def _first_tailnet_ip(self_info: dict[str, Any]) -> str | None:
    ips = self_info.get("TailscaleIPs")
    if isinstance(ips, list) and ips:
        return str(ips[0])
    return None


def _process_summary(stdout: str) -> dict[str, Any]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    return {
        "running": any("node_exporter" in line for line in lines),
        "count": len(lines),
        "sample": _one_line(lines[0], 160) if lines else None,
    }


def _listen_summary(stdout: str, tailnet_ip: str, exporter_port: int) -> dict[str, Any]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    joined = "\n".join(lines)
    expected = f"{tailnet_ip}:{exporter_port}"
    return {
        "listening": bool(lines),
        "tailnet_bound": expected in joined,
        "expected_address": expected,
        "sample": _one_line(lines[1] if len(lines) > 1 else lines[0], 180) if lines else None,
    }


def _http_code(result: ProbeResult) -> str | None:
    match = re.search(r"\b([1-5][0-9]{2})\b", result.stdout)
    if match:
        return match.group(1)
    return None


def diagnose(results: dict[str, ProbeResult], *, tailnet_ip: str, exporter_port: int) -> dict[str, Any]:
    tailscale = _parse_tailscale(results["tailscale"].stdout) if results["tailscale"].ok else {
        "backend_state": "unknown",
        "self_online": None,
        "self_tailscale_ip": None,
        "health_count": None,
    }
    node_process = _process_summary(results["node_process"].stdout)
    node_listen = _listen_summary(results["node_listen"].stdout, tailnet_ip, exporter_port)
    local_http = _http_code(results["metrics_local"])
    tailnet_http = _http_code(results["metrics_tailnet"])

    bridge_alive = results["bridge"].ok
    tailscale_running = tailscale.get("backend_state") == "Running" and tailscale.get("self_online") is True
    exporter_running_tailnet_bound = node_process["running"] and node_listen["tailnet_bound"]
    metrics_local_ok = local_http == "200"
    metrics_tailnet_ok = tailnet_http == "200"

    if bridge_alive and not tailscale_running and exporter_running_tailnet_bound:
        status = "tailscale_stopped_exporter_present"
        diagnosis = "mac93 bridge is alive; Tailscale is not running/online; node_exporter is present and bound to the Tailnet IP, so Prometheus cannot scrape until Tailscale is restored."
    elif bridge_alive and tailscale_running and exporter_running_tailnet_bound and metrics_tailnet_ok:
        status = "healthy"
        diagnosis = "mac93 bridge, Tailscale, Tailnet-bound node_exporter, and Tailnet metrics are healthy."
    elif not any(result.returncode != 255 for result in results.values()):
        status = "ssh_unreachable"
        diagnosis = "mac93 LAN SSH probe could not authenticate or connect; no remote diagnostic checks ran."
    elif not bridge_alive:
        status = "bridge_unreachable"
        diagnosis = "mac93 LAN SSH target responded, but the local bridge health check failed or timed out."
    elif tailscale_running and not exporter_running_tailnet_bound:
        status = "exporter_not_tailnet_bound"
        diagnosis = "mac93 Tailscale is running, but node_exporter is not confirmed as running and bound to the expected Tailnet address."
    elif tailscale_running and exporter_running_tailnet_bound and not metrics_tailnet_ok:
        status = "metrics_unreachable"
        diagnosis = "mac93 Tailscale and Tailnet-bound node_exporter are present, but the metrics endpoint did not return HTTP 200."
    else:
        status = "inconclusive"
        diagnosis = "mac93 read-only probes returned a mixed state; inspect compact probe results."

    return {
        "target": {
            "name": "mac93",
            "tailnet_ip": tailnet_ip,
            "exporter_port": exporter_port,
        },
        "status": status,
        "severity": "ok" if status == "healthy" else "warning",
        "dedupe_key": f"mac93:{status}:{tailscale.get('backend_state')}:{node_listen['tailnet_bound']}:{tailnet_http}",
        "diagnosis": diagnosis,
        "checks": {
            "bridge_alive": bridge_alive,
            "tailscale": tailscale,
            "node_exporter_process": node_process,
            "node_exporter_listen": node_listen,
            "metrics": {
                "local_http_code": local_http,
                "tailnet_http_code": tailnet_http,
                "local_ok": metrics_local_ok,
                "tailnet_ok": metrics_tailnet_ok,
            },
        },
        "probes": {name: result.compact() for name, result in results.items()},
        "safety": {
            "mode": "read_only",
            "transport": "ssh_lan_bridge",
            "forbidden_actions_used": False,
            "remote_commands": sorted(result.command for result in results.values()),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST, help="SSH target for the Mac-93 LAN bridge")
    parser.add_argument("--identity-file", default=DEFAULT_IDENTITY_FILE, help="SSH identity file for the Mac-93 LAN bridge")
    parser.add_argument("--tailnet-ip", default=DEFAULT_TAILNET_IP, help="Expected Mac-93 Tailscale IP")
    parser.add_argument("--exporter-port", type=int, default=DEFAULT_EXPORTER_PORT)
    parser.add_argument("--connect-timeout", type=int, default=5)
    parser.add_argument("--command-timeout", type=int, default=12)
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON for humans")
    args = parser.parse_args(argv)

    try:
        results = _run_all(
            args.host,
            args.tailnet_ip,
            args.exporter_port,
            args.connect_timeout,
            args.command_timeout,
            args.identity_file,
        )
        payload = diagnose(results, tailnet_ip=args.tailnet_ip, exporter_port=args.exporter_port)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        payload = {
            "target": {"name": "mac93", "tailnet_ip": args.tailnet_ip, "exporter_port": args.exporter_port},
            "status": "probe_failed",
            "severity": "warning",
            "dedupe_key": "mac93:probe_failed",
            "diagnosis": f"mac93 read-only diagnostic probe failed before all checks completed: {type(exc).__name__}",
            "error_type": type(exc).__name__,
            "safety": {"mode": "read_only", "transport": "ssh_lan_bridge", "forbidden_actions_used": False},
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2

    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if payload["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
