from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from hermes_cli import os_maintenance_classifier as omc

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "os_maintenance"


def test_safe_worker_allowlist_packages_are_approved_from_simulation_output():
    apt_output = (FIXTURES / "safe_worker_allowlist.apt").read_text(encoding="utf-8")

    decision = omc.classify_apt_simulation(apt_output, host="web04")

    assert decision["decision"] == "allow"
    assert decision["highest_class"] == "safe_ordinary"
    packages = cast(list[dict[str, Any]], decision["packages"])
    policy = cast(dict[str, Any], decision["policy"])
    assert [pkg["name"] for pkg in packages] == ["tzdata", "ca-certificates"]
    assert all(pkg["class"] == "safe_ordinary" for pkg in packages)
    assert "apt-get -s" in policy["default_mode"]
    json.dumps(decision)


def test_denylisted_stack_package_blocks_even_when_security_origin():
    apt_output = (FIXTURES / "blocked_denylist.apt").read_text(encoding="utf-8")

    decision = omc.classify_apt_simulation(apt_output, host="web08")

    assert decision["decision"] == "block"
    assert decision["highest_class"] == "blocked"
    packages = cast(list[dict[str, Any]], decision["packages"])
    assert [pkg["name"] for pkg in packages] == [
        "openssh-server",
        "tailscale",
        "docker-ce",
    ]
    assert all(pkg["class"] == "blocked" for pkg in packages)
    assert "openssh" in packages[0]["reasons"]
    assert "tailscale" in packages[1]["reasons"]
    assert "container runtime" in packages[2]["reasons"]


def test_unallowlisted_ordinary_package_is_risky_and_requires_review():
    apt_output = """
Inst jq [1.7.1-3build1] (1.7.1-3ubuntu0.1 Ubuntu:24.04/noble-updates [amd64])
Conf jq (1.7.1-3ubuntu0.1 Ubuntu:24.04/noble-updates [amd64])
"""

    decision = omc.classify_apt_simulation(apt_output, host="worker-1")

    assert decision["decision"] == "review"
    assert decision["highest_class"] == "risky"
    packages = cast(list[dict[str, Any]], decision["packages"])
    assert packages[0]["class"] == "risky"
    assert "not in worker low-risk allowlist" in packages[0]["reasons"]


def test_reboot_required_marker_blocks_report():
    decision = omc.classify_apt_simulation("", host="vps-new", reboot_required=True)

    assert decision["decision"] == "block"
    assert decision["highest_class"] == "reboot_required"
    assert decision["reboot_required"] is True
    assert "reboot-required marker present" in decision["reasons"]


def test_security_allowlisted_package_requires_review_not_auto_allow():
    apt_output = """
Inst ca-certificates [20240203] (20240203ubuntu0.24.04.1 Ubuntu:24.04/noble-security [all])
"""

    decision = omc.classify_apt_simulation(apt_output, host="web04")

    assert decision["decision"] == "review"
    assert decision["highest_class"] == "security"
    packages = cast(list[dict[str, Any]], decision["packages"])
    assert packages[0]["class"] == "security"


def test_human_summary_mentions_host_decision_and_package_counts():
    apt_output = """
Inst tzdata [2025a] (2025b Ubuntu:24.04/noble-updates [all])
Inst tailscale [1.80.0] (1.82.0 Tailscale:stable [amd64])
"""

    decision = omc.classify_apt_simulation(apt_output, host="web08")
    summary = omc.format_human_summary(decision)

    assert "web08" in summary
    assert "block" in summary
    assert "safe_ordinary=1" in summary
    assert "blocked=1" in summary
    assert "tailscale" in summary
