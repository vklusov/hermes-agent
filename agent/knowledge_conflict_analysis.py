"""Conflict analysis for the knowledge vault.

Step 9 of the knowledge pipeline: detect duplicates, contradictory entries,
stale near-duplicates, and potential conflicts against an external memory
source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import re
from typing import Any, Iterable, Literal

ConflictType = Literal[
    "duplicate",
    "contradiction",
    "stale_pair",
    "memory_conflict",
]


@dataclass(slots=True)
class KnowledgeConflictFinding:
    conflict_type: ConflictType
    severity: Literal["low", "medium", "high"]
    message: str
    entry_ids: tuple[str, ...] = ()
    memory_fact: str | None = None
    category: str | None = None
    title: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_type": self.conflict_type,
            "severity": self.severity,
            "message": self.message,
            "entry_ids": list(self.entry_ids),
            "memory_fact": self.memory_fact,
            "category": self.category,
            "title": self.title,
            "details": dict(self.details),
        }


@dataclass(slots=True)
class KnowledgeConflictReport:
    active_entries: int
    duplicates: list[KnowledgeConflictFinding] = field(default_factory=list)
    contradictions: list[KnowledgeConflictFinding] = field(default_factory=list)
    stale_pairs: list[KnowledgeConflictFinding] = field(default_factory=list)
    memory_conflicts: list[KnowledgeConflictFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_entries": self.active_entries,
            "duplicates": [item.to_dict() for item in self.duplicates],
            "contradictions": [item.to_dict() for item in self.contradictions],
            "stale_pairs": [item.to_dict() for item in self.stale_pairs],
            "memory_conflicts": [item.to_dict() for item in self.memory_conflicts],
            "counts": {
                "duplicates": len(self.duplicates),
                "contradictions": len(self.contradictions),
                "stale_pairs": len(self.stale_pairs),
                "memory_conflicts": len(self.memory_conflicts),
            },
        }

    def to_markdown(self) -> str:
        parts = ["# Knowledge conflict analysis", ""]
        parts.append(f"- active entries: {self.active_entries}")
        parts.append(f"- duplicates: {len(self.duplicates)}")
        parts.append(f"- contradictions: {len(self.contradictions)}")
        parts.append(f"- stale pairs: {len(self.stale_pairs)}")
        parts.append(f"- memory conflicts: {len(self.memory_conflicts)}")
        parts.append("")
        for heading, items in [
            ("Duplicates", self.duplicates),
            ("Contradictions", self.contradictions),
            ("Stale pairs", self.stale_pairs),
            ("Memory conflicts", self.memory_conflicts),
        ]:
            parts.append(f"## {heading}")
            if not items:
                parts.append("- none")
            else:
                for item in items:
                    parts.append(f"- {item.message}")
            parts.append("")
        return "\n".join(parts).rstrip() + "\n"


@dataclass(slots=True)
class VaultEntry:
    entry_id: str
    category: str
    title: str
    content: str
    status: str
    updated: str
    created: str
    tags: tuple[str, ...]
    source: str
    confidence: float
    path: Path
    frontmatter: dict[str, Any] = field(default_factory=dict)

    def normalized_body(self) -> str:
        return _normalize(self.content)


class KnowledgeConflictAnalyzer:
    def __init__(self, vault_root: str | Path, stale_days: int = 30):
        self.vault_root = Path(vault_root)
        self.stale_days = stale_days

    def analyze(self, memory_facts: Iterable[str] | None = None) -> KnowledgeConflictReport:
        entries = self._load_entries()
        active_entries = [entry for entry in entries if entry.status == "active"]

        duplicates = self._find_duplicates(active_entries)
        contradictions = self._find_contradictions(active_entries)
        stale_pairs = self._find_stale_pairs(active_entries)
        memory_conflicts = self._find_memory_conflicts(active_entries, memory_facts or [])

        return KnowledgeConflictReport(
            active_entries=len(active_entries),
            duplicates=duplicates,
            contradictions=contradictions,
            stale_pairs=stale_pairs,
            memory_conflicts=memory_conflicts,
        )

    def _load_entries(self) -> list[VaultEntry]:
        entries: list[VaultEntry] = []
        for path in sorted(self.vault_root.rglob("*.md")):
            raw = path.read_text(encoding="utf-8")
            fm, body = _split_markdown(raw)
            entry_id = str(fm.get("entry_id") or path.stem)
            updated = str(fm.get("updated") or fm.get("created") or date.today().isoformat())
            created = str(fm.get("created") or updated)
            tags = tuple(str(tag) for tag in (fm.get("tags") or []))
            entry = VaultEntry(
                entry_id=entry_id,
                category=str(fm.get("category") or path.parent.name),
                title=str(fm.get("title") or _infer_title(body)),
                content=body,
                status=str(fm.get("status") or "active"),
                updated=updated,
                created=created,
                tags=tags,
                source=str(fm.get("source") or "conversation"),
                confidence=float(fm.get("confidence") or 0.0),
                path=path,
                frontmatter=fm,
            )
            entries.append(entry)
        return entries

    def _find_duplicates(self, entries: list[VaultEntry]) -> list[KnowledgeConflictFinding]:
        buckets: dict[tuple[str, str], list[VaultEntry]] = {}
        for entry in entries:
            buckets.setdefault((entry.category, entry.normalized_body()), []).append(entry)
        findings: list[KnowledgeConflictFinding] = []
        for (category, _), group in buckets.items():
            if len(group) < 2:
                continue
            ids = tuple(sorted(item.entry_id for item in group))
            findings.append(
                KnowledgeConflictFinding(
                    conflict_type="duplicate",
                    severity="medium",
                    message=f"Duplicate entries in {category}: {', '.join(ids)}",
                    entry_ids=ids,
                    category=category,
                    details={"paths": [str(item.path) for item in group]},
                )
            )
        return findings

    def _find_contradictions(self, entries: list[VaultEntry]) -> list[KnowledgeConflictFinding]:
        findings: list[KnowledgeConflictFinding] = []
        for i, left in enumerate(entries):
            for right in entries[i + 1 :]:
                if left.category != right.category:
                    continue
                if _contradicts(left.content, right.content):
                    findings.append(
                        KnowledgeConflictFinding(
                            conflict_type="contradiction",
                            severity="high",
                            message=f"Contradiction between {left.entry_id} and {right.entry_id} in {left.category}",
                            entry_ids=(left.entry_id, right.entry_id),
                            category=left.category,
                            details={
                                "left": left.content,
                                "right": right.content,
                            },
                        )
                    )
        return findings

    def _find_stale_pairs(self, entries: list[VaultEntry]) -> list[KnowledgeConflictFinding]:
        findings: list[KnowledgeConflictFinding] = []
        by_key: dict[tuple[str, str], list[VaultEntry]] = {}
        for entry in entries:
            by_key.setdefault((entry.category, entry.title.lower()), []).append(entry)
        for (category, title), group in by_key.items():
            if len(group) < 2:
                continue
            for i, older in enumerate(sorted(group, key=lambda item: _parse_date(item.updated))):
                for newer in group[i + 1 :]:
                    if older.entry_id == newer.entry_id:
                        continue
                    if older.status != "active" or newer.status != "active":
                        continue
                    if older.normalized_body() == newer.normalized_body():
                        continue
                    if _similar(older.content, newer.content) >= 0.35:
                        findings.append(
                            KnowledgeConflictFinding(
                                conflict_type="stale_pair",
                                severity="medium",
                                message=f"Potential supersede pair in {category}/{title}: {older.entry_id} -> {newer.entry_id}",
                                entry_ids=(older.entry_id, newer.entry_id),
                                category=category,
                                title=title,
                                details={
                                    "older_updated": older.updated,
                                    "newer_updated": newer.updated,
                                    "paths": [str(older.path), str(newer.path)],
                                },
                            )
                        )
        return findings

    def _find_memory_conflicts(self, entries: list[VaultEntry], memory_facts: Iterable[str]) -> list[KnowledgeConflictFinding]:
        findings: list[KnowledgeConflictFinding] = []
        for fact in memory_facts:
            fact_norm = _normalize(fact)
            for entry in entries:
                if _topic_overlap(fact_norm, entry.normalized_body()) < 0.2:
                    continue
                if _contradicts(fact, entry.content):
                    findings.append(
                        KnowledgeConflictFinding(
                            conflict_type="memory_conflict",
                            severity="high",
                            message=f"Memory fact conflicts with vault entry {entry.entry_id}",
                            entry_ids=(entry.entry_id,),
                            memory_fact=fact,
                            category=entry.category,
                            title=entry.title,
                            details={"entry_path": str(entry.path)},
                        )
                    )
        return findings


def analyze_knowledge_conflicts(vault_root: str | Path, memory_facts: Iterable[str] | None = None, stale_days: int = 30) -> KnowledgeConflictReport:
    return KnowledgeConflictAnalyzer(vault_root=vault_root, stale_days=stale_days).analyze(memory_facts=memory_facts)


def _split_markdown(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    body = text[end + 5 :]
    fm: dict[str, Any] = {}
    key: str | None = None
    for raw_line in fm_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and key is not None:
            fm.setdefault(key, []).append(line[5:].strip())
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            fm[key] = []
            continue
        if value in {"true", "false"}:
            fm[key] = value == "true"
        else:
            try:
                fm[key] = int(value)
            except ValueError:
                try:
                    fm[key] = float(value)
                except ValueError:
                    fm[key] = value
    return fm, body.strip()


def _infer_title(content: str) -> str:
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    return first_line.lstrip("# ")[:80] if first_line else "Untitled"


def _normalize(text: str) -> str:
    text = re.sub(r"\b(the|a|an)\b", " ", text.lower())
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]+", text.lower()))


def _topic_overlap(left: str, right: str) -> float:
    a = _tokenize(left)
    b = _tokenize(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _similar(left: str, right: str) -> float:
    a = _tokenize(left)
    b = _tokenize(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def _parse_date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min


_CONTRADICTION_PAIRS = [
    ("always", "never"),
    ("enabled", "disabled"),
    ("enable", "disable"),
    ("on", "off"),
    ("true", "false"),
    ("allow", "disallow"),
    ("prefer", "avoid"),
    ("short", "detailed"),
    ("concise", "verbose"),
    ("concise", "never"),
    ("verbose", "never"),
]


def _contradicts(left: str, right: str) -> bool:
    l = _normalize(left)
    r = _normalize(right)
    if l == r:
        return False
    for a, b in _CONTRADICTION_PAIRS:
        if (a in l and b in r) or (b in l and a in r):
            return True
    return False
