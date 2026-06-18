from __future__ import annotations

from pathlib import Path

from agent.knowledge_layer import KnowledgeEntry, KnowledgeExtractionCandidate
from agent.prefetch_knowledge import SilverbulletKnowledgeProvider


def test_auto_update_uses_policy_gate(tmp_path):
    vault = tmp_path / "knowledge"
    provider = SilverbulletKnowledgeProvider(vault_root=str(vault), silverbullet_url="http://127.0.0.1:3002")
    provider._use_filesystem_vault = True

    entry = KnowledgeEntry(
        entry_id="pref-2",
        category="preferences",
        content="User prefers short answers.",
        title="Reply style",
        tags=("prefs",),
        source="conversation",
        confidence=0.9,
        status="active",
        created="2026-06-01",
        updated="2026-06-01",
        supersedes=(),
        superseded_by=(),
        metadata={},
    )
    provider.save_knowledge_entry(entry)

    candidate = KnowledgeExtractionCandidate(
        type="preference",
        content="User prefers short answers.",
        category="preferences",
        confidence=0.95,
        action="save",
        reason="test",
        metadata={"source": "conversation"},
    )

    result = provider.auto_update_knowledge_entry(candidate, entry)
    assert result.decision == "apply_update"
    assert result.patch["entry_id"] == "pref-2"
    assert Path(vault / "preferences" / "pref-2.md").exists()


def test_auto_update_does_not_apply_low_confidence_new_candidate(tmp_path):
    vault = tmp_path / "knowledge"
    provider = SilverbulletKnowledgeProvider(vault_root=str(vault), silverbullet_url="http://127.0.0.1:3002")
    provider._use_filesystem_vault = True

    candidate = KnowledgeExtractionCandidate(
        type="preference",
        content="User prefers short answers.",
        category="preferences",
        confidence=0.2,
        action="save",
        reason="test",
        metadata={"source": "conversation"},
    )

    result = provider.auto_update_knowledge_entry(candidate, None)
    assert result.decision == "no_change"
    assert result.patch == {}
    assert not Path(vault / "preferences" / "unknown.md").exists()
