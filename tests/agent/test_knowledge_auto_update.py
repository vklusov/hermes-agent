from __future__ import annotations

from agent.knowledge_auto_update import KnowledgeAutoUpdatePolicy, decide_auto_update
from agent.knowledge_layer import KnowledgeEntry, KnowledgeExtractionCandidate


def _entry(content: str, *, status: str = "active", entry_id: str = "e1", category: str = "preferences") -> KnowledgeEntry:
    return KnowledgeEntry(
        entry_id=entry_id,
        category=category,
        content=content,
        title="Sample title",
        tags=("test",),
        source="conversation",
        confidence=0.9,
        status=status,  # type: ignore[arg-type]
        created="2026-06-01",
        updated="2026-06-01",
        supersedes=(),
        superseded_by=(),
        metadata={},
    )


def _candidate(content: str, *, action: str = "save", confidence: float = 0.9, category: str = "preferences") -> KnowledgeExtractionCandidate:
    return KnowledgeExtractionCandidate(
        type="preference",
        content=content,
        category=category,
        confidence=confidence,
        action=action,  # type: ignore[arg-type]
        reason="test",
        metadata={"source": "conversation"},
    )


def test_policy_returns_no_change_for_low_confidence():
    policy = KnowledgeAutoUpdatePolicy()
    result = policy.decide(_candidate("User likes short answers.", confidence=0.2), _entry("User likes short answers."))

    assert result.decision == "no_change"
    assert result.patch == {}


def test_policy_returns_no_change_for_low_confidence_new_candidate():
    policy = KnowledgeAutoUpdatePolicy()
    result = policy.decide(_candidate("User likes short answers.", confidence=0.2), None)

    assert result.decision == "no_change"
    assert result.patch == {}


def test_policy_returns_suggest_update_for_mid_confidence():
    policy = KnowledgeAutoUpdatePolicy()
    result = policy.decide(_candidate("User likes concise responses.", confidence=0.6), _entry("User likes short answers."))

    assert result.decision == "suggest_update"
    assert result.patch
    assert result.existing_entry_id == "e1"


def test_policy_applies_high_confidence_update_for_similar_content():
    policy = KnowledgeAutoUpdatePolicy()
    result = policy.decide(_candidate("User likes short answers.", confidence=0.95), _entry("User likes short answers a lot."))

    assert result.decision == "apply_update"
    assert result.patch["status"] == "active"
    assert result.patch["entry_id"] == "e1"


def test_policy_archives_only_as_suggestion():
    policy = KnowledgeAutoUpdatePolicy()
    result = policy.decide(_candidate("Archive old note.", action="archive", confidence=0.99), _entry("Archive old note."))

    assert result.decision == "suggest_update"
    assert result.patch["status"] == "archived"


def test_decide_auto_update_helper_matches_policy():
    result = decide_auto_update(_candidate("New note.", confidence=0.9), None)
    assert result.decision in {"apply_update", "suggest_update"}
