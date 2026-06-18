"""Knowledge layer interfaces for Hermes.

This module defines the shared contract for durable knowledge operations:
search, extract, save, update, and archive.  It is intentionally backend-
agnostic so later work can bind Silverbullet, files, or other stores behind a
single API surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Dict, List, Literal, Mapping, Sequence

from agent.knowledge_extractor import ExtractorConfig

KnowledgeAction = Literal["save", "ignore", "update", "archive"]
KnowledgeStatus = Literal["active", "superseded", "stale", "archived"]

_DEFAULT_STATUS: KnowledgeStatus = "active"
_DEFAULT_SOURCE = "conversation"


def _today() -> str:
    return date.today().isoformat()


@dataclass(slots=True)
class KnowledgeSearchHit:
    """A compact retrieval result for prompt-time context."""

    entry_id: str
    category: str
    score: float
    reason: str
    excerpt: str
    source: str = ""
    tags: tuple[str, ...] = ()
    tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class KnowledgeExtractionCandidate:
    """A post-response candidate for durable knowledge capture."""

    type: str
    content: str
    category: str
    confidence: float
    action: KnowledgeAction
    reason: str
    entry_id: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class KnowledgeEntry:
    """A durable knowledge record stored in markdown + frontmatter form."""

    entry_id: str
    category: str
    content: str
    title: str = ""
    tags: tuple[str, ...] = ()
    source: str = _DEFAULT_SOURCE
    confidence: float = 0.0
    status: KnowledgeStatus = _DEFAULT_STATUS
    created: str = field(default_factory=_today)
    updated: str = field(default_factory=_today)
    supersedes: tuple[str, ...] = ()
    superseded_by: tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)

    def frontmatter(self) -> Dict[str, Any]:
        data = {
            "created": self.created,
            "updated": self.updated,
            "status": self.status,
            "tags": list(self.tags),
            "source": self.source,
            "confidence": self.confidence,
            "supersedes": list(self.supersedes),
            "superseded_by": list(self.superseded_by),
        }
        if self.title:
            data["title"] = self.title
        if self.category:
            data["category"] = self.category
        if self.metadata:
            data.update(self.metadata)
        return data

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        payload["supersedes"] = list(self.supersedes)
        payload["superseded_by"] = list(self.superseded_by)
        return payload

    def to_markdown(self) -> str:
        """Render as a markdown document with YAML frontmatter."""

        def _render_value(value: Any, indent: int = 0) -> list[str]:
            pad = "  " * indent
            if isinstance(value, dict):
                lines: list[str] = []
                for key, item in value.items():
                    if isinstance(item, (dict, list, tuple)):
                        lines.append(f"{pad}{key}:")
                        lines.extend(_render_value(item, indent + 1))
                    else:
                        lines.append(f"{pad}{key}: {item}")
                return lines
            if isinstance(value, (list, tuple)):
                lines = []
                for item in value:
                    if isinstance(item, (dict, list, tuple)):
                        lines.append(f"{pad}-")
                        lines.extend(_render_value(item, indent + 1))
                    else:
                        lines.append(f"{pad}- {item}")
                return lines
            return [f"{pad}{value}"]

        lines = ["---"]
        for key, value in self.frontmatter().items():
            if isinstance(value, (dict, list, tuple)):
                lines.append(f"{key}:")
                lines.extend(_render_value(value, 1))
            else:
                lines.append(f"{key}: {value}")
        lines.append("---")
        lines.append("")
        if self.title:
            lines.append(f"# {self.title}")
            lines.append("")
        lines.append(self.content.rstrip())
        lines.append("")
        return "\n".join(lines)


class KnowledgeLayer(ABC):
    """Unified contract for durable knowledge backends."""

    @abstractmethod
    def search_knowledge(
        self,
        query: str,
        top_k: int,
        max_tokens: int,
    ) -> List[KnowledgeSearchHit]:
        """Return the most relevant durable knowledge hits for a query."""

    @abstractmethod
    def extract_knowledge_candidates(
        self,
        conversation: Sequence[Mapping[str, Any]],
        response: str,
    ) -> List[KnowledgeExtractionCandidate]:
        """Derive durable knowledge candidates from a turn."""

    @abstractmethod
    def save_knowledge_entry(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        """Persist a new durable knowledge entry."""

    @abstractmethod
    def update_knowledge_entry(
        self,
        entry_id: str,
        patch: Mapping[str, Any],
    ) -> KnowledgeEntry:
        """Apply a patch to an existing knowledge entry."""

    @abstractmethod
    def archive_knowledge_entry(self, entry_id: str) -> KnowledgeEntry:
        """Mark a knowledge entry as archived (status → 'archived')."""

    @abstractmethod
    def supersede_entry(
        self, entry_id: str, successor_id: str
    ) -> tuple[KnowledgeEntry, KnowledgeEntry]:
        """Create a supersedes relationship: *entry_id* is superseded by *successor_id*.
        Returns (old_entry, new_entry) where old.status == 'superseded' and
        old.superseded_by contains successor_id, while new.supersedes contains entry_id.
        """

    @abstractmethod
    def mark_stale(self, entry_id: str) -> KnowledgeEntry:
        """Mark an entry as stale (status → 'stale')."""

    @abstractmethod
    def get_active_entries(self) -> List[KnowledgeEntry]:
        """Return all entries with status == 'active'."""

    @abstractmethod
    def get_superseding_chain(
        self, entry_id: str
    ) -> List[KnowledgeEntry]:
        """Return the full supersedes chain starting from *entry_id*, ordered
        oldest → newest."""

    @abstractmethod
    def get_lifecycle_history(
        self, entry_id: str
    ) -> List[KnowledgeEntry]:
        """Return all versions / related entries in this entry's lifecycle,
        including supersedes predecessors, successors, and linked entries."""


__all__ = [
    "KnowledgeAction",
    "KnowledgeEntry",
    "KnowledgeExtractionCandidate",
    "KnowledgeLayer",
    "KnowledgeSearchHit",
    "KnowledgeStatus",
]
