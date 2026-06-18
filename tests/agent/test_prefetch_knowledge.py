"""Tests for the knowledge pre-search provider."""

from __future__ import annotations

import pytest

from agent.knowledge_layer import KnowledgeEntry, KnowledgeSearchHit
from agent.prefetch_knowledge import (
    KnowledgePrefetchConfig,
    SilverbulletKnowledgeProvider,
)


class TestKnowledgePrefetchConfig:
    def test_defaults(self):
        cfg = KnowledgePrefetchConfig()
        assert cfg.top_k == 3
        assert cfg.max_context_tokens == 600
        assert cfg.max_summary_chars == 400
        assert cfg.min_score == 0.5

    def test_from_dict(self):
        cfg = KnowledgePrefetchConfig.from_dict(
            {"top_k": 5, "max_context_tokens": 1200, "max_summary_chars": 800, "min_score": 0.7}
        )
        assert cfg.top_k == 5
        assert cfg.max_context_tokens == 1200
        assert cfg.max_summary_chars == 800
        assert cfg.min_score == 0.7

    def test_from_dict_partial(self):
        cfg = KnowledgePrefetchConfig.from_dict({"top_k": 2})
        assert cfg.top_k == 2
        assert cfg.max_context_tokens == 600  # default


class TestSilverbulletKnowledgeProvider:
    def test_implements_memory_provider_contract(self):
        p = SilverbulletKnowledgeProvider()
        assert p.name == "silverbullet_knowledge"
        assert p.is_available()

    def test_initialize(self):
        p = SilverbulletKnowledgeProvider()
        p.initialize()  # must not raise

    def test_prefetch_empty_query(self):
        p = SilverbulletKnowledgeProvider()
        assert p.prefetch("") == ""
        assert p.prefetch("   ") == ""

    def test_prefetch_no_hits(self):
        p = SilverbulletKnowledgeProvider()
        result = p.prefetch("something that won't match")
        assert result == ""  # _search_vault returns [] until SB is wired

    def test_no_tools_exposed(self):
        p = SilverbulletKnowledgeProvider()
        assert p.get_tool_schemas() == []

    def test_handle_tool_call_raises(self):
        p = SilverbulletKnowledgeProvider()
        with pytest.raises(ValueError, match="has no tool"):
            p.handle_tool_call("memory", {})

    def test_render_hits_empty(self):
        p = SilverbulletKnowledgeProvider()
        assert p._render_hits([]) == ""

    def test_render_hits_single(self):
        p = SilverbulletKnowledgeProvider(config=KnowledgePrefetchConfig(top_k=5))
        hits = [
            KnowledgeSearchHit(
                entry_id="kb-001",
                category="preferences",
                score=0.93,
                reason="Matches communication style.",
                excerpt="User prefers short factual answers.",
                source="silverbullet://knowledge/preferences/kb-001.md",
                tags=("communication",),
                tokens=42,
            )
        ]
        result = p._render_hits(hits)
        assert result.startswith("<knowledge-context>")
        assert result.endswith("</knowledge-context>")
        assert "prefers short factual" in result
        assert "kb-001" in result
        assert "Matches communication style" in result

    def test_render_hits_multiple_with_budget(self):
        top_k = 10
        p = SilverbulletKnowledgeProvider(
            config=KnowledgePrefetchConfig(top_k=top_k, max_context_tokens=600)
        )
        hits = [
            KnowledgeSearchHit(
                entry_id=f"kb-{i:03d}",
                category="test",
                score=0.9,
                reason="Test hit.",
                excerpt="A" * 50,
                source=f"silverbullet://kb-{i:03d}.md",
            )
            for i in range(top_k)
        ]
        result = p._render_hits(hits)
        # Should have at least 1 hit rendered, but budget-limited
        assert "<knowledge-context>" in result
        assert "kb-000" in result

    def test_prefetch_honors_top_k(self):
        """top_k limit is applied in prefetch(), not _render_hits()."""
        p = SilverbulletKnowledgeProvider(config=KnowledgePrefetchConfig(top_k=2))
        # Override _search_vault to return more than top_k hits
        hits = [
            KnowledgeSearchHit(
                entry_id=f"kb-{i:03d}", category="test", score=0.9,
                reason="Hit", excerpt="Entry " + str(i),
                source=f"silverbullet://kb-{i:03d}.md",
            )
            for i in range(5)
        ]
        # Monkey-patch to avoid having to wire SB
        p._search_vault = lambda q: hits  # type: ignore[method-assign]
        result = p.prefetch("test query")
        # Should have exactly 2 entries (top_k=2), not 5
        lines = result.split("\n")
        entry_lines = [l for l in lines if l.strip().startswith("[")]
        assert len(entry_lines) == 2

    def test_prefetch_filters_by_min_score(self):
        """min_score threshold is applied in prefetch()."""
        p = SilverbulletKnowledgeProvider()
        hits = [
            KnowledgeSearchHit(
                entry_id=f"kb-{i:03d}", category="test",
                score=0.2 + i * 0.1,  # 0.2, 0.3, 0.4, 0.5, 0.6
                reason="Hit", excerpt="Entry",
                source=f"silverbullet://kb-{i:03d}.md",
            )
            for i in range(5)
        ]
        p._search_vault = lambda q: hits  # type: ignore[method-assign]
        result = p.prefetch("test query")
        # Only entries with score >= 0.5 (indices 3,4) should appear
        assert "kb-000" not in result  # 0.2 < 0.5
        assert "kb-001" not in result  # 0.3 < 0.5
        assert "kb-002" not in result  # 0.4 < 0.5
        assert "kb-003" in result  # 0.5 == 0.5 → kept
        assert "kb-004" in result  # 0.6 > 0.5 → kept

    def test_prefetch_returns_fenced_knowledge_block(self, tmp_path):
        vault = tmp_path / "knowledge"
        section = vault / "manual"
        section.mkdir(parents=True)
        (section / "kb-100.md").write_text(
            """---
entry_id: kb-100
category: manual
status: active
source: silverbullet://knowledge/manual/kb-100.md
confidence: 0.95
---
Headroom and routerai are documented in the KB.
""",
            encoding="utf-8",
        )
        p = SilverbulletKnowledgeProvider(vault_root=str(vault))
        rendered = p.prefetch("headroom routerai")
        assert rendered.startswith("<knowledge-context>")
        assert "kb-100" in rendered
        from agent.memory_manager import build_memory_context_block

        fenced = build_memory_context_block(rendered)
        assert fenced.startswith("<memory-context>")
        assert "Headroom and routerai" in fenced

    def test_local_vault_search_reads_markdown(self, tmp_path):
        vault = tmp_path / "knowledge"
        section = vault / "preferences"
        section.mkdir(parents=True)
        (section / "kb-001.md").write_text(
            """---
entry_id: kb-001
category: preferences
status: active
tags:
  - communication
source: silverbullet://knowledge/preferences/kb-001.md
confidence: 0.92
---
User prefers short factual answers and concise summaries.
""",
            encoding="utf-8",
        )
        (section / "kb-002.md").write_text(
            """---
entry_id: kb-002
category: preferences
status: archived
tags:
  - communication
source: silverbullet://knowledge/preferences/kb-002.md
confidence: 0.4
---
User prefers long answers.
""",
            encoding="utf-8",
        )
        p = SilverbulletKnowledgeProvider(vault_root=str(vault))
        hits = p._search_vault("short factual long answers")
        assert len(hits) == 1
        assert hits[0].entry_id == "kb-001"
        assert hits[0].category == "preferences"
        assert "short factual" in hits[0].excerpt.lower()
        assert all(hit.entry_id != "kb-002" for hit in hits)

    def test_local_vault_search_missing_root_returns_empty(self, tmp_path):
        p = SilverbulletKnowledgeProvider(vault_root=str(tmp_path / "missing"))
        assert p._search_vault("anything") == []

    def test_render_hits_truncates_and_wraps_context(self):
        p = SilverbulletKnowledgeProvider(
            config=KnowledgePrefetchConfig(top_k=3, max_context_tokens=20, max_summary_chars=40)
        )
        hits = [
            KnowledgeSearchHit(
                entry_id="kb-001",
                category="preferences",
                score=0.91,
                reason="Relevant",
                excerpt="X" * 200,
                source="silverbullet://knowledge/preferences/kb-001.md",
            )
        ]
        rendered = p._render_hits(hits)
        assert rendered.startswith("<knowledge-context>")
        assert rendered.endswith("</knowledge-context>")
        assert "..." not in rendered
        assert len(rendered) <= p._config.max_context_tokens * 4
        assert "kb-001" in rendered
        assert rendered.count("\n") <= 2

    def test_search_fn_exception_degrades_to_empty(self):
        def boom(query: str):
            raise RuntimeError("boom")

        p = SilverbulletKnowledgeProvider(search_fn=boom)
        assert p._search_vault("anything") == []

    def test_save_update_archive_are_routed(self, tmp_path):
        vault = tmp_path / "knowledge"
        p = SilverbulletKnowledgeProvider(vault_root=str(vault), silverbullet_url="http://127.0.0.1:3002")
        p._use_filesystem_vault = True
        entry = KnowledgeEntry(
            entry_id="kb-002",
            category="preferences",
            content="User prefers concise answers.",
            title="Preference",
            tags=("communication",),
            source="conversation",
            confidence=0.9,
        )

        saved = p.save_knowledge_entry(entry)
        path = vault / "preferences" / "kb-002.md"
        assert path.exists()
        assert saved["path"] == str(path)

        updated = p.update_knowledge_entry("kb-002", {"confidence": 0.95})
        assert updated["path"] == str(path)
        archived = p.archive_knowledge_entry("kb-002")
        assert archived["path"] == str(path)
        assert "status: archived" in path.read_text(encoding="utf-8")
