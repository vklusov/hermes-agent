"""Dry-run backup retention inventory and verifier.

The proposed retention policy is intentionally advisory. This module reports
backup freshness, missing required classes, and oversized artifacts, but never
deletes or prunes anything. Deletion remains a Tier C human-approved action
until a policy is explicitly approved and wired separately.
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from hermes_constants import get_hermes_home
from hermes_cli.backup import _format_size

POLICY_RESOURCE = "backup_retention_policy.yaml"
SECRET_NAME_RE = re.compile(
    r"(^|[/\\])(?:\.env|auth\.json|.*(?:secret|token|credential|password|key).*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BackupArtifact:
    """One retention-verifier inventory row."""

    backup_class: str
    path: Path
    size_bytes: int
    mtime: datetime
    kind: str

    @property
    def age_days(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.mtime).total_seconds() / 86400)


def default_policy_path() -> Path:
    """Return the packaged proposed policy path."""

    return Path(str(resources.files("hermes_cli.data") / POLICY_RESOURCE))


def load_retention_policy(path: Optional[Path] = None) -> dict[str, Any]:
    """Load a backup retention policy YAML document."""

    policy_path = Path(path).expanduser() if path is not None else default_policy_path()
    with policy_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"retention policy must be a mapping: {policy_path}")
    classes = data.get("classes")
    if not isinstance(classes, dict):
        raise ValueError(f"retention policy has no classes mapping: {policy_path}")
    return data


def _expand_pattern(pattern: str, hermes_home: Path) -> str:
    expanded = pattern.replace("$HERMES_HOME", str(hermes_home))
    expanded = expanded.replace("${HERMES_HOME}", str(hermes_home))
    return os.path.expanduser(os.path.expandvars(expanded))


def _is_secret_shaped(path: Path) -> bool:
    return bool(SECRET_NAME_RE.search(path.as_posix()))


def _redact_path(path: Path, hermes_home: Path) -> str:
    """Return a safe display path without exposing secret-bearing filenames."""

    try:
        display = "$HERMES_HOME/" + path.resolve().relative_to(hermes_home.resolve()).as_posix()
    except (OSError, ValueError):
        display = str(path.expanduser())
    if _is_secret_shaped(path):
        return str(path.parent / "<redacted-sensitive-name>")
    return display


def _artifact_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file() and not child.is_symlink():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def _iter_matches(patterns: Iterable[str], hermes_home: Path) -> Iterable[Path]:
    seen: set[Path] = set()
    for pattern in patterns:
        for raw in glob.glob(_expand_pattern(pattern, hermes_home)):
            path = Path(raw)
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path.absolute()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path


def collect_backup_inventory(
    policy: Optional[dict[str, Any]] = None,
    *,
    hermes_home: Optional[Path] = None,
) -> tuple[list[BackupArtifact], list[dict[str, Any]]]:
    """Collect backup artifacts described by *policy*.

    Returns ``(artifacts, class_summaries)``. It only stats filesystem entries;
    no files are opened, deleted, or modified.
    """

    loaded_policy = policy or load_retention_policy()
    home = hermes_home or get_hermes_home()
    artifacts: list[BackupArtifact] = []
    summaries: list[dict[str, Any]] = []

    for class_name, class_policy in sorted(loaded_policy.get("classes", {}).items()):
        if not isinstance(class_policy, dict):
            continue
        matched: list[BackupArtifact] = []
        for path in _iter_matches(class_policy.get("patterns", []), home):
            if not path.exists() or path.is_symlink():
                continue
            try:
                stat_result = path.stat()
            except OSError:
                continue
            artifact = BackupArtifact(
                backup_class=class_name,
                path=path,
                size_bytes=_artifact_size(path),
                mtime=datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc),
                kind="dir" if path.is_dir() else "file",
            )
            artifacts.append(artifact)
            matched.append(artifact)
        newest = max((item.mtime for item in matched), default=None)
        summaries.append(
            {
                "class": class_name,
                "count": len(matched),
                "required": bool(class_policy.get("required", False)),
                "newest": newest.isoformat() if newest else None,
                "total_size_bytes": sum(item.size_bytes for item in matched),
                "prune_exempt": bool(class_policy.get("prune_exempt", False)),
                "deletion_tier": class_policy.get("deletion_tier", "C"),
            }
        )
    return artifacts, summaries


def verify_backup_retention(
    policy: Optional[dict[str, Any]] = None,
    *,
    hermes_home: Optional[Path] = None,
) -> dict[str, Any]:
    """Return a dry-run verifier report for the proposed retention policy."""

    loaded_policy = policy or load_retention_policy()
    home = hermes_home or get_hermes_home()
    artifacts, summaries = collect_backup_inventory(loaded_policy, hermes_home=home)
    by_class: dict[str, list[BackupArtifact]] = {}
    for artifact in artifacts:
        by_class.setdefault(artifact.backup_class, []).append(artifact)

    findings: list[dict[str, Any]] = []
    for class_name, class_policy in sorted(loaded_policy.get("classes", {}).items()):
        if not isinstance(class_policy, dict):
            continue
        class_items = by_class.get(class_name, [])
        if class_policy.get("required") and not class_items:
            findings.append(
                {
                    "severity": "warning",
                    "class": class_name,
                    "code": "missing_required_class",
                    "message": f"required backup class {class_name!r} has no artifacts",
                }
            )
        max_staleness_days = class_policy.get("max_staleness_days")
        if class_items and max_staleness_days is not None:
            newest = max(item.mtime for item in class_items)
            age_days = (datetime.now(timezone.utc) - newest).total_seconds() / 86400
            if age_days > float(max_staleness_days):
                findings.append(
                    {
                        "severity": "warning",
                        "class": class_name,
                        "code": "stale_class",
                        "message": (
                            f"newest {class_name!r} artifact is {age_days:.1f} days old "
                            f"(limit {float(max_staleness_days):.1f})"
                        ),
                    }
                )
        max_item_bytes = class_policy.get("max_item_bytes")
        if max_item_bytes is not None:
            for item in class_items:
                if item.size_bytes > int(max_item_bytes):
                    findings.append(
                        {
                            "severity": "warning",
                            "class": class_name,
                            "code": "oversized_artifact",
                            "path": _redact_path(item.path, home),
                            "size_bytes": item.size_bytes,
                            "message": (
                                f"artifact exceeds class size limit: {_format_size(item.size_bytes)} "
                                f"> {_format_size(int(max_item_bytes))}"
                            ),
                        }
                    )

    protected_classes = [
        name
        for name, class_policy in loaded_policy.get("classes", {}).items()
        if isinstance(class_policy, dict) and class_policy.get("prune_exempt")
    ]
    return {
        "schema_version": loaded_policy.get("schema_version"),
        "policy_status": loaded_policy.get("policy_status", "proposed"),
        "mode": "dry_run",
        "deletion_tier": loaded_policy.get("safety", {}).get("deletion_tier", "C"),
        "deletion_performed": False,
        "hermes_home": str(home),
        "classes": summaries,
        "artifact_count": len(artifacts),
        "total_size_bytes": sum(item.size_bytes for item in artifacts),
        "protected_classes": protected_classes,
        "findings": findings,
    }


def format_retention_report(report: dict[str, Any]) -> str:
    """Render a compact human-readable dry-run report."""

    lines = [
        "Backup retention verifier (dry-run)",
        f"Policy status: {report.get('policy_status')}",
        f"Deletion tier: {report.get('deletion_tier')} (performed: {report.get('deletion_performed')})",
        f"Hermes home: {report.get('hermes_home')}",
        f"Inventory: {report.get('artifact_count', 0)} artifact(s), {_format_size(int(report.get('total_size_bytes') or 0))}",
        "",
        "Classes:",
    ]
    for item in report.get("classes", []):
        newest = item.get("newest") or "none"
        flags = []
        if item.get("required"):
            flags.append("required")
        if item.get("prune_exempt"):
            flags.append("prune-exempt")
        flag_text = f" ({', '.join(flags)})" if flags else ""
        lines.append(
            f"  - {item['class']}: {item['count']} item(s), "
            f"{_format_size(int(item.get('total_size_bytes') or 0))}, newest={newest}, "
            f"deletion_tier={item.get('deletion_tier')}{flag_text}"
        )
    findings = report.get("findings", [])
    lines.append("")
    if findings:
        lines.append("Findings:")
        for finding in findings:
            path = f" [{finding['path']}]" if finding.get("path") else ""
            lines.append(
                f"  - {finding.get('severity', 'info').upper()} "
                f"{finding.get('code')}: {finding.get('message')}{path}"
            )
    else:
        lines.append("Findings: none")
    lines.append("")
    lines.append("No deletion performed. Pruning remains Tier C until policy approval.")
    return "\n".join(lines)


def retention_report_json(report: dict[str, Any]) -> str:
    """Serialize a report deterministically for tests and evidence."""

    return json.dumps(report, indent=2, sort_keys=True)
