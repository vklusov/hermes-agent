#!/usr/bin/env python3
"""Run read-only apt maintenance dry-run classification over SSH hosts."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path

from hermes_cli.os_maintenance_classifier import classify_apt_simulation, format_human_summary

HOSTS = {
    "vps-new": ("root", "2.27.50.82"),
    "web04": ("root", "144.31.202.75"),
    "web06": ("wwolfy", "87.242.117.142"),
    "web08": ("root", "31.76.40.14"),
}


def _ssh_command(user: str, host: str, key: str, remote_command: str) -> list[str]:
    return [
        "ssh",
        "-i",
        key,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=no",
        f"{user}@{host}",
        remote_command,
    ]


def run_host(name: str, user: str, host: str, key: str) -> dict[str, object]:
    remote = "apt-get -s dist-upgrade; rc=$?; test -e /var/run/reboot-required; rb=$?; printf '\n__HERMES_APT_RC=%s REBOOT_REQUIRED=%s\n' \"$rc\" \"$rb\"; exit $rc"
    proc = subprocess.run(
        _ssh_command(user, host, key, remote),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=150,
        check=False,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    reboot_required = "REBOOT_REQUIRED=0" in combined
    if proc.returncode != 0:
        return {
            "host": name,
            "target": f"{user}@{host}",
            "reachable": False,
            "error": combined.strip() or f"ssh/apt simulation exit {proc.returncode}",
            "returncode": proc.returncode,
        }
    decision = classify_apt_simulation(combined, host=name, reboot_required=reboot_required)
    decision["target"] = f"{user}@{host}"
    decision["reachable"] = True
    decision["human_summary"] = format_human_summary(decision)
    return decision


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default=str(Path.home() / ".ssh" / "id_ed25519_archivarius"))
    parser.add_argument("--out", default="reports/os-maintenance-dry-run.json")
    parser.add_argument("hosts", nargs="*", default=sorted(HOSTS))
    args = parser.parse_args()

    results = []
    for name in args.hosts:
        if name not in HOSTS:
            raise SystemExit(f"unknown host {name!r}; known: {', '.join(sorted(HOSTS))}")
        user, host = HOSTS[name]
        results.append(run_host(name, user, host, args.key))

    report = {"mode": "read-only apt simulation", "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    for item in results:
        if item.get("reachable"):
            print(item["human_summary"])
        else:
            print(f"Host {item['host']}: unreachable/error: {item.get('error')}")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
