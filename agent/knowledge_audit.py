"""Weekly knowledge vault audit utilities.

Phase 7 of the Hermes knowledge pipeline: detect duplicates, conflicts,
stale entries, and supersede candidates in the Silverbullet-backed vault.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import hashlib
import json
import re


@dataclass(slots=True)
class KnowledgeAuditFinding:
    kind: str
    entry_ids: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    category: str = ""
    title: str = ""
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "entry_ids": list(self.entry_ids),
            "paths": list(self.paths),
            "category": self.category,
            "title": self.title,
            "reason": self.reason,
            "details": dict(self.details),
        }


@dataclass(slots=True)
class KnowledgeAuditReport:
    generated_at: str
    vault_root: str
    total_entries: int
    active_entries: int
    stale_entries: int
    archived_entries: int
    duplicate_groups: list[KnowledgeAuditFinding] = field(default_factory=list)
    conflict_groups: list[KnowledgeAuditFinding] = field(default_factory=list)
    supersede_candidates: list[KnowledgeAuditFinding] = field(default_factory=list)
    stale_candidates: list[KnowledgeAuditFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "vault_root": self.vault_root,
            "total_entries": self.total_entries,
            "active_entries": self.active_entries,
            "stale_entries": self.stale_entries,
            "archived_entries": self.archived_entries,
            "duplicate_groups": [x.to_dict() for x in self.duplicate_groups],
            "conflict_groups": [x.to_dict() for x in self.conflict_groups],
            "supersede_candidates": [x.to_dict() for x in self.supersede_candidates],
            "stale_candidates": [x.to_dict() for x in self.stale_candidates],
        }

    def to_markdown(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        return f"# Knowledge audit report\n\n```json\n{payload}\n```\n"


class KnowledgeVaultAuditor:
    def __init__(self, vault_root: str | Path, stale_after_days: int = 30) -> None:
        self.vault_root = Path(vault_root).expanduser()
        self.stale_after_days = stale_after_days

    def audit(self) -> KnowledgeAuditReport:
        entries = list(self._iter_entries())
        total = len(entries)
        active = sum(1 for e in entries if e["status"] == "active")
        archived = sum(1 for e in entries if e["status"] == "archived")
        stale = sum(1 for e in entries if e["status"] in {"stale", "superseded", "archived"})

        duplicate_groups = self._find_duplicate_groups(entries)
        conflict_groups = self._find_conflicts(entries)
        supersede_candidates = self._find_supersede_candidates(entries)
        stale_candidates = self._find_stale_candidates(entries)

        return KnowledgeAuditReport(
            generated_at=date.today().isoformat(),
            vault_root=str(self.vault_root),
            total_entries=total,
            active_entries=active,
            stale_entries=stale,
            archived_entries=archived,
            duplicate_groups=duplicate_groups,
            conflict_groups=conflict_groups,
            supersede_candidates=supersede_candidates,
            stale_candidates=stale_candidates,
        )

    def _iter_entries(self) -> list[dict[str, Any]]:
        if not self.vault_root.exists():
            return []
        entries = []
        for path in self.vault_root.rglob("*.md"):
            try:
                entry = self._parse_entry(path)
            except Exception:
                continue
            entries.append(entry)
        return entries

    def _parse_entry(self, path: Path) -> dict[str, Any]:
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = self._split_markdown(raw)
        frontmatter.setdefault("entry_id", path.stem)
        frontmatter.setdefault("category", path.parent.name)
        frontmatter.setdefault("status", "active")
        frontmatter.setdefault("updated", frontmatter.get("created", date.today().isoformat()))
        frontmatter.setdefault("title", path.stem)
        frontmatter.setdefault("source", "unknown")
        frontmatter.setdefault("confidence", 0.0)
        frontmatter["tags"] = self._ensure_list(frontmatter.get("tags"))
        frontmatter["supersedes"] = self._ensure_list(frontmatter.get("supersedes"))
        frontmatter["superseded_by"] = self._ensure_list(frontmatter.get("superseded_by"))
        frontmatter["path"] = str(path)
        frontmatter["body"] = body.strip()
        return frontmatter

    def _split_markdown(self, text: str) -> tuple[dict[str, Any], str]:
        if not text.startswith("---\n"):
            return {}, text
        end = text.find("\n---\n", 4)
        if end == -1:
            return {}, text
        frontmatter = self._parse_frontmatter(text[4:end])
        body = text[end + 5 :]
        return frontmatter, body

    def _parse_frontmatter(self, text: str) -> dict[str, Any]:
        data: dict[str, Any] = {}
        current_key: str | None = None
        for line in text.splitlines():
            if line.startswith("  - ") and current_key:
                data.setdefault(current_key, []).append(line[4:].strip())
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            current_key = key
            if value == "":
                data[key] = []
            elif value.startswith("[") and value.endswith("]"):
                try:
                    data[key] = json.loads(value)
                except Exception:
                    data[key] = value
            else:
                data[key] = self._coerce_scalar(value)
        return data

    def _coerce_scalar(self, value: str) -> Any:
        lowered = value.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        try:
            if "." in value:
                return float(value)
            return int(value)
        except Exception:
            return value

    def _ensure_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, tuple):
            return [str(v) for v in value]
        if value == "":
            return []
        return [str(value)]

    def _normalized_body(self, body: str) -> str:
        return re.sub(r"\s+", " ", body).strip().lower()

    def _content_hash(self, body: str) -> str:
        return hashlib.sha1(self._normalized_body(body).encode("utf-8")).hexdigest()

    def _find_duplicate_groups(self, entries: list[dict[str, Any]]) -> list[KnowledgeAuditFinding]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            buckets.setdefault(self._content_hash(entry["body"]), []).append(entry)

        findings: list[KnowledgeAuditFinding] = []
        for bucket in buckets.values():
            if len(bucket) < 2:
                continue
            if len({e.get("entry_id") for e in bucket}) < 2:
                continue
            findings.append(
                KnowledgeAuditFinding(
                    kind="duplicate",
                    entry_ids=[str(e["entry_id"]) for e in bucket],
                    paths=[str(e["path"]) for e in bucket],
                    category=str(bucket[0].get("category") or ""),
                    title=str(bucket[0].get("title") or ""),
                    reason="Same normalized body text across multiple notes.",
                )
            )
        return findings

    def _find_conflicts(self, entries: list[dict[str, Any]]) -> list[KnowledgeAuditFinding]:
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in entries:
            key = (str(entry.get("category") or ""), self._normalize_title(str(entry.get("title") or "")))
            buckets.setdefault(key, []).append(entry)

        findings: list[KnowledgeAuditFinding] = []
        for (category, title), bucket in buckets.items():
            if len(bucket) < 2:
                continue
            bodies = {self._normalized_body(e["body"]) for e in bucket}
            statuses = {str(e.get("status") or "active") for e in bucket}
            if len(bodies) > 1 and len(statuses) > 1:
                findings.append(
                    KnowledgeAuditFinding(
                        kind="conflict",
                        entry_ids=[str(e["entry_id"]) for e in bucket],
                        paths=[str(e["path"]) for e in bucket],
                        category=category,
                        title=title,
                        reason="Same title/category, different content and lifecycle status.",
                        details={"statuses": sorted(statuses)},
                    )
                )
        return findings

    def _find_supersede_candidates(self, entries: list[dict[str, Any]]) -> list[KnowledgeAuditFinding]:
        findings: list[KnowledgeAuditFinding] = []
        for finding in self._find_conflicts(entries):
            active_entries = [e for e in entries if str(e.get("entry_id")) in finding.entry_ids and str(e.get("status")) == "active"]
            stale_entries = [e for e in entries if str(e.get("entry_id")) in finding.entry_ids and str(e.get("status")) != "active"]
            if active_entries and stale_entries:
                findings.append(
                    KnowledgeAuditFinding(
                        kind="supersede_candidate",
                        entry_ids=finding.entry_ids,
                        paths=finding.paths,
                        category=finding.category,
                        title=finding.title,
                        reason="Active and non-active versions coexist for the same title/category.",
                        details=finding.details,
                    )
                )
        return findings

    def _find_stale_candidates(self, entries: list[dict[str, Any]]) -> list[KnowledgeAuditFinding]:
        cutoff = datetime.utcnow().date() - timedelta(days=self.stale_after_days)
        findings: list[KnowledgeAuditFinding] = []
        for entry in entries:
            status = str(entry.get("status") or "active")
            updated = str(entry.get("updated") or "")
            try:
                updated_date = datetime.fromisoformat(updated).date()
            except Exception:
                continue
            if status == "active" and updated_date < cutoff:
                findings.append(
                    KnowledgeAuditFinding(
                        kind="stale",
                        entry_ids=[str(entry.get("entry_id"))],
                        paths=[str(entry.get("path"))],
                        category=str(entry.get("category") or ""),
                        title=str(entry.get("title") or ""),
                        reason=f"Last updated before cutoff ({cutoff.isoformat()}).",
                        details={"updated": updated},
                    )
                )
        return findings

    def _normalize_title(self, title: str) -> str:
        return re.sub(r"\s+", " ", title).strip().lower()


def build_weekly_audit_report(vault_root: str | Path, stale_after_days: int = 30) -> KnowledgeAuditReport:
    return KnowledgeVaultAuditor(vault_root=vault_root, stale_after_days=stale_after_days).audit()


def render_weekly_audit_report(report: KnowledgeAuditReport) -> str:
    data = report.to_dict()
    lines = [
        f"weekly knowledge audit @ {data['generated_at']}",
        f"total: {data['total_entries']}",
        f"active: {data['active_entries']}",
        f"supersede candidates: {len(data['supersede_candidates'])}",
        f"stale: {data['stale_entries']}",
        f"archived: {data['archived_entries']}",
        f"duplicates: {len(data['duplicate_groups'])}",
        f"conflicts: {len(data['conflict_groups'])}",
    ]
    if data['duplicate_groups']:
        lines.append("duplicate groups:")
        for item in data['duplicate_groups']:
            lines.append(f"- {item}")
    if data['conflict_groups']:
        lines.append("conflict groups:")
        for item in data['conflict_groups']:
            lines.append(f"- {item}")
    if data['supersede_candidates']:
        lines.append("supersede candidates:")
        for item in data['supersede_candidates']:
            lines.append(f"- {item}")
    if data['stale_candidates']:
        lines.append("stale candidates:")
        for item in data['stale_candidates']:
            lines.append(f"- {item}")
    return "\n".join(lines)
