from __future__ import annotations

import pytest

from agent.knowledge_layer import (
    KnowledgeAction,
    KnowledgeEntry,
    KnowledgeExtractionCandidate,
    KnowledgeLayer,
    KnowledgeSearchHit,
    KnowledgeStatus,
)


class TestKnowledgeLayerTypes:
    def test_entry_renders_yaml_frontmatter(self):
        entry = KnowledgeEntry(
            entry_id="kb-001",
            category="preferences",
            title="Concise responses",
            content="User prefers short factual answers.",
            tags=("architecture", "communication"),
            source="conversation",
            confidence=0.87,
            supersedes=("kb-000",),
        )

        markdown = entry.to_markdown()

        assert markdown.startswith("---\n")
        assert "created:" in markdown
        assert "updated:" in markdown
        assert "status: active" in markdown
        assert "tags:" in markdown
        assert "- architecture" in markdown
        assert "category: preferences" in markdown
        assert "# Concise responses" in markdown
        assert "User prefers short factual answers." in markdown

    def test_candidate_payload_is_json_ready(self):
        candidate = KnowledgeExtractionCandidate(
            type="preference",
            content="User prefers short factual answers.",
            category="preferences",
            confidence=0.91,
            action="save",
            reason="Stable user preference",
            metadata={"source_turn": 12},
        )

        payload = candidate.to_dict()

        assert payload["type"] == "preference"
        assert payload["action"] == "save"
        assert payload["metadata"]["source_turn"] == 12
        assert isinstance(candidate.action, str)

    def test_search_hit_payload_is_json_ready(self):
        hit = KnowledgeSearchHit(
            entry_id="kb-001",
            category="preferences",
            score=0.93,
            reason="Matches the user's communication style.",
            excerpt="User prefers short factual answers.",
            source="silverbullet://knowledge/preferences/kb-001.md",
            tags=("communication",),
            tokens=42,
        )

        payload = hit.to_dict()

        assert payload["entry_id"] == "kb-001"
        assert payload["score"] == 0.93
        assert payload["tokens"] == 42

    def test_knowledge_layer_is_abstract_contract(self):
        with pytest.raises(TypeError):
            KnowledgeLayer()

    def test_action_literal_kept_in_scope(self):
        assert KnowledgeAction.__args__ == ("save", "ignore", "update", "archive")

    def test_status_literal_has_all_lifecycle_states(self):
        assert KnowledgeStatus.__args__ == (
            "active", "superseded", "stale", "archived"
        )

    def test_entry_default_status_is_active(self):
        entry = KnowledgeEntry(
            entry_id="kb-010",
            category="test",
            content="Test",
        )
        assert entry.status == "active"

    def test_entry_supersedes_links(self):
        old = KnowledgeEntry(
            entry_id="kb-001",
            category="decisions",
            content="Use provider A",
        )
        new = KnowledgeEntry(
            entry_id="kb-002",
            category="decisions",
            content="Use provider B",
            supersedes=("kb-001",),
        )
        assert "kb-001" in new.supersedes

    def test_entry_superseded_by_link(self):
        old = KnowledgeEntry(
            entry_id="kb-001",
            category="decisions",
            content="Use provider A",
            superseded_by=("kb-002",),
        )
        assert "kb-002" in old.superseded_by

    def test_entry_markdown_includes_status_and_supersedes(self):
        entry = KnowledgeEntry(
            entry_id="kb-003",
            category="architecture",
            content="Old decision",
            title="Old arch",
            status="superseded",
            superseded_by=("kb-004",),
        )
        md = entry.to_markdown()
        assert "status: superseded" in md
        assert "superseded_by:" in md
        assert "- kb-004" in md

    def test_entry_markdown_includes_supersedes(self):
        entry = KnowledgeEntry(
            entry_id="kb-004",
            category="architecture",
            content="New decision",
            title="New arch",
            supersedes=("kb-003",),
        )
        md = entry.to_markdown()
        assert "supersedes:" in md
        assert "- kb-003" in md

    def test_entry_can_be_stale(self):
        entry = KnowledgeEntry(
            entry_id="kb-005",
            category="infrastructure",
            content="Old server config",
            status="stale",
        )
        assert entry.status == "stale"

    def test_entry_can_be_archived(self):
        entry = KnowledgeEntry(
            entry_id="kb-006",
            category="incidents",
            content="Resolved incident",
            status="archived",
        )
        assert entry.status == "archived"


# ── Concrete test store ──────────────────────────────────────────────────


class InMemoryKnowledgeLayer(KnowledgeLayer):
    """An in-memory store that implements all lifecycle methods for testing."""

    def __init__(self) -> None:
        self._store: dict[str, KnowledgeEntry] = {}

    def search_knowledge(
        self,
        query: str,
        top_k: int = 5,
        max_tokens: int = 500,
    ) -> list[KnowledgeSearchHit]:
        return []

    def extract_knowledge_candidates(
        self,
        conversation,
        response,
    ) -> list[KnowledgeExtractionCandidate]:
        return []

    def save_knowledge_entry(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        self._store[entry.entry_id] = entry
        return entry

    def update_knowledge_entry(
        self, entry_id: str, patch
    ) -> KnowledgeEntry:
        old = self._store[entry_id]
        kwargs = dict(old.__dataclass_fields__)
        for key, val in dict(patch).items():
            setattr(old, key, val)
        old.updated = old.updated  # keep caller's value if set
        return old

    def archive_knowledge_entry(self, entry_id: str) -> KnowledgeEntry:
        entry = self._store[entry_id]
        object.__setattr__(entry, "status", "archived")
        return entry

    def supersede_entry(
        self, entry_id: str, successor_id: str
    ) -> tuple[KnowledgeEntry, KnowledgeEntry]:
        old = self._store[entry_id]
        new = self._store[successor_id]
        # Patch old entry: status → superseded, link to successor
        object.__setattr__(old, "status", "superseded")
        object.__setattr__(
            old, "superseded_by", old.superseded_by + (successor_id,)
        )
        # Patch new entry: link to predecessor
        object.__setattr__(
            new, "supersedes", new.supersedes + (entry_id,)
        )
        return old, new

    def mark_stale(self, entry_id: str) -> KnowledgeEntry:
        entry = self._store[entry_id]
        object.__setattr__(entry, "status", "stale")
        return entry

    def get_active_entries(self) -> list[KnowledgeEntry]:
        return [e for e in self._store.values() if e.status == "active"]

    def get_superseding_chain(
        self, entry_id: str
    ) -> list[KnowledgeEntry]:
        """Follow supersedes backward and superseded_by forward."""
        chain: list[KnowledgeEntry] = []
        # Walk backward to oldest ancestor
        cursor = self._store[entry_id]
        ancestors: list[KnowledgeEntry] = []
        while cursor.supersedes:
            parent_id = cursor.supersedes[0]
            cursor = self._store[parent_id]
            ancestors.append(cursor)
        chain.extend(reversed(ancestors))
        # Add current
        chain.append(self._store[entry_id])
        # Walk forward
        cursor = self._store[entry_id]
        while cursor.superseded_by:
            child_id = cursor.superseded_by[0]
            cursor = self._store[child_id]
            chain.append(cursor)
        return chain

    def get_lifecycle_history(
        self, entry_id: str
    ) -> list[KnowledgeEntry]:
        return self.get_superseding_chain(entry_id)


class TestKnowledgeLayerLifecycle:
    """Tests for lifecycle management on a concrete KnowledgeLayer store."""

    @pytest.fixture
    def store(self) -> InMemoryKnowledgeLayer:
        s = InMemoryKnowledgeLayer()
        s.save_knowledge_entry(
            KnowledgeEntry(
                entry_id="v1",
                category="decisions",
                content="Use provider A",
                title="Provider choice v1",
            )
        )
        s.save_knowledge_entry(
            KnowledgeEntry(
                entry_id="v2",
                category="decisions",
                content="Use provider B",
                title="Provider choice v2",
            )
        )
        s.save_knowledge_entry(
            KnowledgeEntry(
                entry_id="v3",
                category="decisions",
                content="Use provider C",
                title="Provider choice v3",
            )
        )
        s.save_knowledge_entry(
            KnowledgeEntry(
                entry_id="stale-entry",
                category="infrastructure",
                content="Old IP",
                title="Server IP old",
            )
        )
        s.save_knowledge_entry(
            KnowledgeEntry(
                entry_id="archived-entry",
                category="incidents",
                content="Resolved",
                title="Incident old",
            )
        )
        return s

    def test_supersede_entry_marks_old_as_superseded(self, store):
        store.supersede_entry("v1", "v2")
        assert store._store["v1"].status == "superseded"

    def test_supersede_entry_links_both_directions(self, store):
        store.supersede_entry("v1", "v2")
        assert "v2" in store._store["v1"].superseded_by
        assert "v1" in store._store["v2"].supersedes

    def test_mark_stale_changes_status(self, store):
        store.mark_stale("stale-entry")
        assert store._store["stale-entry"].status == "stale"

    def test_archive_entry_changes_status(self, store):
        store.archive_knowledge_entry("archived-entry")
        assert store._store["archived-entry"].status == "archived"

    def test_get_active_returns_only_active(self, store):
        store.supersede_entry("v1", "v2")
        active = store.get_active_entries()
        ids = {e.entry_id for e in active}
        assert "v2" in ids  # still active
        assert "v3" in ids  # still active
        assert "v1" not in ids  # superseded

    def test_superseding_chain_in_order(self, store):
        store.supersede_entry("v1", "v2")
        store.supersede_entry("v2", "v3")
        chain = store.get_superseding_chain("v1")
        assert [e.entry_id for e in chain] == ["v1", "v2", "v3"]

    def test_superseding_chain_from_middle(self, store):
        store.supersede_entry("v1", "v2")
        store.supersede_entry("v2", "v3")
        chain = store.get_superseding_chain("v2")
        assert [e.entry_id for e in chain] == ["v1", "v2", "v3"]

    def test_lifecycle_history_returns_chain(self, store):
        store.supersede_entry("v1", "v2")
        history = store.get_lifecycle_history("v1")
        assert len(history) == 2

    def test_stale_entry_not_in_active(self, store):
        store.mark_stale("stale-entry")
        active = store.get_active_entries()
        assert "stale-entry" not in {e.entry_id for e in active}

    def test_archived_entry_not_in_active(self, store):
        store.archive_knowledge_entry("archived-entry")
        active = store.get_active_entries()
        assert "archived-entry" not in {e.entry_id for e in active}
