from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from hermes_cli.backup_retention import (
    collect_backup_inventory,
    format_retention_report,
    load_retention_policy,
    verify_backup_retention,
)
from hermes_cli.subcommands.backup import build_backup_parser


def _touch(path: Path, *, age_days: int = 0, size: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))


def test_bundled_policy_is_proposed_and_keeps_deletion_tier_c() -> None:
    policy = load_retention_policy()

    assert policy["policy_status"] == "proposed"
    assert policy["safety"]["deletion_tier"] == "C"
    assert policy["safety"]["deletion_default"] == "never"
    assert policy["classes"]["release_evidence"]["prune_exempt"] is True
    assert policy["classes"]["release_evidence"]["retention"]["evidence_exception"] == "keep_forever"
    assert {"daily", "weekly", "monthly"} <= set(policy["retention_defaults"])


def test_inventory_reports_current_backup_classes_without_opening_secrets(tmp_path, monkeypatch) -> None:
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    _touch(tmp_path / "hermes-backup-2026-08-01-000000.zip", size=3)
    _touch(home / "backups" / "pre-update-2026-08-02-000000.zip", size=4)
    _touch(home / "backups" / "pre-migration-2026-08-03-000000.zip", size=5)
    _touch(home / "state-snapshots" / "20260803-010000" / "manifest.json", size=6)
    _touch(home / "fleet" / "releases" / "r1" / "evidence.md", size=7)

    artifacts, summaries = collect_backup_inventory(hermes_home=home)
    counts = {item["class"]: item["count"] for item in summaries}

    assert len(artifacts) == 5
    assert counts["manual_full_backup"] == 1
    assert counts["pre_update_backup"] == 1
    assert counts["pre_migration_backup"] == 1
    assert counts["quick_state_snapshot"] == 1
    assert counts["release_evidence"] == 1


def test_verifier_reports_stale_and_oversized_but_never_deletes(tmp_path, monkeypatch) -> None:
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    old_zip = home / "backups" / "pre-update-2026-01-01-000000.zip"
    _touch(old_zip, age_days=60, size=16)
    policy = {
        "schema_version": 1,
        "policy_status": "proposed",
        "safety": {"deletion_tier": "C"},
        "classes": {
            "pre_update_backup": {
                "patterns": ["$HERMES_HOME/backups/pre-update-*.zip"],
                "required": True,
                "max_staleness_days": 30,
                "max_item_bytes": 8,
                "deletion_tier": "C",
            }
        },
    }

    report = verify_backup_retention(policy, hermes_home=home)
    codes = {finding["code"] for finding in report["findings"]}

    assert report["mode"] == "dry_run"
    assert report["deletion_performed"] is False
    assert old_zip.exists()
    assert codes == {"stale_class", "oversized_artifact"}
    assert "No deletion performed" in format_retention_report(report)


def test_retention_report_redacts_secret_shaped_paths(tmp_path, monkeypatch) -> None:
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    secret_named = home / "backups" / "token-backup.zip"
    _touch(secret_named, size=16)
    policy = {
        "schema_version": 1,
        "policy_status": "proposed",
        "safety": {"deletion_tier": "C"},
        "classes": {
            "sensitive_named_backup": {
                "patterns": ["$HERMES_HOME/backups/*.zip"],
                "max_item_bytes": 1,
                "deletion_tier": "C",
            }
        },
    }

    report = verify_backup_retention(policy, hermes_home=home)

    assert report["findings"][0]["code"] == "oversized_artifact"
    assert "token-backup" not in report["findings"][0]["path"]
    assert "<redacted-sensitive-name>" in report["findings"][0]["path"]


def test_retention_report_cli_flags_are_registered() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_backup_parser(subparsers, cmd_backup=lambda args: None)

    args = parser.parse_args(["backup", "--retention-report", "--retention-policy", "policy.yaml"])

    assert args.retention_report is True
    assert args.retention_policy == "policy.yaml"


def test_cmd_backup_retention_report_prints_dry_run(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "policy_status": "proposed",
                "safety": {"deletion_tier": "C"},
                "classes": {
                    "pre_update_backup": {
                        "patterns": ["$HERMES_HOME/backups/pre-update-*.zip"],
                        "required": False,
                        "deletion_tier": "C",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    from hermes_cli.main import cmd_backup

    cmd_backup(SimpleNamespace(retention_report=True, retention_policy=str(policy_path), quick=False))
    out = capsys.readouterr().out

    assert "Backup retention verifier (dry-run)" in out
    assert "No deletion performed" in out
