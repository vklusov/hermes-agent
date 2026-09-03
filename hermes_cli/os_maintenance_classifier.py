"""Low-risk apt maintenance classifier.

The default runtime path is intentionally read-only: it uses apt simulation
(`apt-get -s dist-upgrade`) and never installs, removes, or reboots anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence, cast

DEFAULT_SIMULATION_COMMAND = ("apt-get", "-s", "dist-upgrade")
REBOOT_REQUIRED_PATH = Path("/var/run/reboot-required")

# Explicit Tier-B worker allowlist: ordinary non-service, low-blast-radius data
# or trust-store packages observed in routine worker maintenance. Everything not
# listed here requires review even if it is otherwise ordinary.
WORKER_LOW_RISK_ALLOWLIST = frozenset(
    {
        "ca-certificates",
        "distro-info-data",
        "publicsuffix",
        "tzdata",
        "ubuntu-advantage-tools",
        "ubuntu-pro-client",
        "ubuntu-pro-client-l10n",
    }
)

# Denylist families: Tier-C packages and service stacks that can break access,
# networking, public edge, databases, container runtime, or require reboots.
DENYLIST_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), reason)
    for pattern, reason in (
        (r"^(linux-|linux$|linux_)", "kernel"),
        (r"^(libc6|libc-bin|libc-dev-bin|libc6-dev|glibc|linux-libc-dev)$", "libc"),
        (r"^(openssh|ssh|libssl|openssl)", "openssh"),
        (r"^(docker|containerd|runc|podman|buildah|skopeo)", "container runtime"),
        (r"^(tailscale|tailscaled)$", "tailscale"),
        (
            r"^(netplan|network-manager|systemd-networkd|ifupdown|iptables|nftables|ufw|iproute2|wireguard|resolvconf|systemd-resolved)",
            "network stack",
        ),
        (r"^(caddy|nginx|apache2|haproxy|traefik)", "public edge/reverse proxy"),
        (r"^(postgresql|mysql|mariadb|mongodb|redis|sqlite3|etcd|influxdb)", "database"),
        (r"^(systemd|dbus|udev)", "core service manager/reboot risk"),
    )
)

APT_INST_RE = re.compile(
    r"^Inst\s+(?P<name>\S+)(?:\s+\[(?P<installed>[^\]]+)\])?\s+\((?P<candidate>.*)\)\s*$"
)
APT_REMOVAL_RE = re.compile(r"^(Remv|RemvConf|Purg)\s+(?P<name>\S+)")

_CLASS_RANK = {
    "safe_ordinary": 0,
    "risky": 1,
    "security": 2,
    "reboot_required": 3,
    "blocked": 4,
}
_DECISION_BY_CLASS = {
    "safe_ordinary": "allow",
    "risky": "review",
    "security": "review",
    "reboot_required": "block",
    "blocked": "block",
}


@dataclass(frozen=True)
class AptPackage:
    name: str
    installed: str | None
    candidate: str
    origin: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_apt_simulation(output: str) -> list[AptPackage]:
    """Extract packages from apt-get simulation output."""
    packages: list[AptPackage] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        removal = APT_REMOVAL_RE.match(line)
        if removal:
            name = removal.group("name")
            packages.append(
                AptPackage(
                    name=name,
                    installed=None,
                    candidate="removal/purge simulated",
                    origin="removal",
                )
            )
            continue
        match = APT_INST_RE.match(line)
        if not match:
            continue
        candidate = match.group("candidate") or ""
        packages.append(
            AptPackage(
                name=match.group("name"),
                installed=match.group("installed"),
                candidate=candidate,
                origin=candidate,
            )
        )
    return packages


def _denylist_reasons(package_name: str) -> list[str]:
    return [reason for pattern, reason in DENYLIST_PATTERNS if pattern.search(package_name)]


def _is_security_origin(origin: str) -> bool:
    return "-security" in origin.lower() or " security" in origin.lower()


def classify_package(package: AptPackage) -> dict[str, object]:
    reasons = _denylist_reasons(package.name)
    if package.origin == "removal":
        reasons.append("apt simulation includes removal/purge")
    if reasons:
        package_class = "blocked"
    elif _is_security_origin(package.origin):
        package_class = "security"
        reasons.append("security pocket/origin")
    elif package.name in WORKER_LOW_RISK_ALLOWLIST:
        package_class = "safe_ordinary"
        reasons.append("worker low-risk allowlist")
    else:
        package_class = "risky"
        reasons.append("not in worker low-risk allowlist")

    return {
        "name": package.name,
        "installed": package.installed,
        "candidate": package.candidate,
        "origin": package.origin,
        "class": package_class,
        "reasons": reasons,
    }


def _highest_class(classes: Iterable[str]) -> str:
    highest = "safe_ordinary"
    for cls in classes:
        if _CLASS_RANK[cls] > _CLASS_RANK[highest]:
            highest = cls
    return highest


def classify_apt_simulation(
    output: str,
    *,
    host: str | None = None,
    reboot_required: bool = False,
    command: Sequence[str] = DEFAULT_SIMULATION_COMMAND,
) -> dict[str, object]:
    packages = [classify_package(pkg) for pkg in parse_apt_simulation(output)]
    classes = [str(pkg["class"]) for pkg in packages]
    reasons: list[str] = []
    if reboot_required:
        classes.append("reboot_required")
        reasons.append("reboot-required marker present")
    highest = _highest_class(classes) if classes else ("reboot_required" if reboot_required else "safe_ordinary")
    counts = Counter(classes)
    decision = _DECISION_BY_CLASS[highest]
    return {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "host": host or socket.gethostname(),
        "decision": decision,
        "highest_class": highest,
        "reboot_required": bool(reboot_required),
        "packages": packages,
        "counts": dict(sorted(counts.items())),
        "reasons": reasons,
        "policy": {
            "default_mode": "apt simulation only: apt-get -s dist-upgrade",
            "simulation_command": list(command),
            "worker_low_risk_allowlist": sorted(WORKER_LOW_RISK_ALLOWLIST),
            "denylist": [reason for _, reason in DENYLIST_PATTERNS],
            "gates": {
                "allow": "Tier B candidate only: all packages are explicit worker low-risk allowlist and no reboot marker",
                "review": "security or non-allowlisted ordinary updates require human/policy review",
                "block": "denylisted packages, simulated removals, or reboot-required marker stop automation",
            },
        },
    }


def format_human_summary(decision: dict[str, object]) -> str:
    counts = cast(dict[str, int], decision.get("counts") or {})
    count_bits = ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"
    package_entries = cast(list[dict[str, Any]], decision.get("packages") or [])
    package_names = [str(pkg.get("name")) for pkg in package_entries]
    packages = ", ".join(package_names) if package_names else "no pending packages parsed"
    reasons = cast(list[object], decision.get("reasons") or [])
    suffix = f" Reasons: {', '.join(map(str, reasons))}." if reasons else ""
    return (
        f"Host {decision.get('host')}: decision={decision.get('decision')}, "
        f"highest_class={decision.get('highest_class')}, counts: {count_bits}. "
        f"Packages: {packages}.{suffix}"
    )


def run_apt_simulation(command: Sequence[str] = DEFAULT_SIMULATION_COMMAND) -> str:
    proc = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"apt simulation failed with exit code {proc.returncode}: {combined.strip()}")
    return combined


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m hermes_cli.os_maintenance_classifier",
        description="Classify apt-get simulation output for low-risk worker OS maintenance gates.",
    )
    parser.add_argument("--input", "-i", help="Read apt simulation output from file instead of running apt-get -s")
    parser.add_argument("--host", help="Host label for report (default: local hostname)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only")
    parser.add_argument(
        "--reboot-required",
        action="store_true",
        help="Force reboot-required marker in the decision (useful for fixtures)",
    )
    parser.add_argument(
        "--ignore-reboot-marker",
        action="store_true",
        help="Do not read /var/run/reboot-required when running local simulation",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.input:
        output = Path(args.input).read_text(encoding="utf-8")
        marker = bool(args.reboot_required)
    else:
        output = run_apt_simulation()
        marker = bool(args.reboot_required) or (
            not args.ignore_reboot_marker and REBOOT_REQUIRED_PATH.exists()
        )
    decision = classify_apt_simulation(output, host=args.host, reboot_required=marker)
    if args.json:
        print(json.dumps(decision, indent=2, sort_keys=True))
    else:
        print(format_human_summary(decision))
        print(json.dumps(decision, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
