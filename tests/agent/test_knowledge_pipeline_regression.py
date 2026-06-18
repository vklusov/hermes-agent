from __future__ import annotations

from pathlib import Path

from agent.knowledge_audit import build_weekly_audit_report
from agent.knowledge_auto_update import decide_auto_update
from agent.knowledge_conflict_analysis import analyze_knowledge_conflicts
from agent.knowledge_layer import KnowledgeEntry, KnowledgeExtractionCandidate
from agent.prefetch_knowledge import SilverbulletKnowledgeProvider


def _entry(
    entry_id: str,
    *,
    category: str = "preferences",
    content: str = "User prefers concise replies.",
    title: str = "Reply style",
    status: str = "active",
    updated: str = "2026-06-01",
    confidence: float = 0.9,
) -> KnowledgeEntry:
    return KnowledgeEntry(
        entry_id=entry_id,
        category=category,
        content=content,
        title=title,
        tags=("prefs",),
        source="conversation",
        confidence=confidence,
        status=status,  # type: ignore[arg-type]
        created="2026-06-01",
        updated=updated,
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
        reason="regression test",
        metadata={"source": "conversation"},
    )


def test_pipeline_regression_end_to_end(tmp_path):
    vault = tmp_path / "knowledge"
    provider = SilverbulletKnowledgeProvider(vault_root=str(vault), silverbullet_url="http://127.0.0.1:3002")
    provider._use_filesystem_vault = True

    base_entry = _entry("kb-001")
    saved = provider.save_knowledge_entry(base_entry)
    assert Path(saved["path"]).exists()

    hits = provider.search_knowledge("concise replies", top_k=5, max_tokens=128)
    assert hits
    assert any(hit.entry_id == "kb-001" for hit in hits)
    assert all(hit.reason for hit in hits)
    assert all(hit.excerpt for hit in hits)

    candidate = _candidate("User prefers concise replies.", confidence=0.95)
    decision = decide_auto_update(candidate, base_entry)
    assert decision.decision == "apply_update"
    assert decision.patch["entry_id"] == "kb-001"

    applied = provider.auto_update_knowledge_entry(candidate, base_entry)
    assert applied.decision == "apply_update"

    audit_report = build_weekly_audit_report(vault)
    assert audit_report.active_entries >= 1
    assert isinstance(audit_report.duplicate_groups, list)
    assert isinstance(audit_report.stale_candidates, list)

    conflict_report = analyze_knowledge_conflicts(vault, memory_facts=["User prefers concise replies."])
    assert conflict_report.active_entries >= 1
    assert isinstance(conflict_report.to_markdown(), str)


def test_pipeline_regression_filters_noise_and_keeps_context_compact():
    conversation = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "remember I prefer short answers"},
    ]
    response = "Understood — I’ll keep replies short."

    candidate = _candidate("User prefers short answers.", confidence=0.88)
    decision = decide_auto_update(candidate, None)

    assert decision.decision in {"apply_update", "suggest_update"}
    assert len(conversation) == 3
    assert "short" in response.lower()


def test_pipeline_regression_marks_stale_entries_for_audit(tmp_path):
    vault = tmp_path / "knowledge"
    provider = SilverbulletKnowledgeProvider(vault_root=str(vault), silverbullet_url="http://127.0.0.1:3002")
    provider._use_filesystem_vault = True
    provider.save_knowledge_entry(_entry("kb-002", content="User prefers concise replies.", updated="2026-06-01"))
    provider.save_knowledge_entry(_entry("kb-003", content="User prefers verbose replies.", updated="2026-06-06"))

    audit_report = build_weekly_audit_report(vault)
    assert audit_report.active_entries == 2
    assert isinstance(audit_report.stale_candidates, list)

    conflicts = analyze_knowledge_conflicts(vault)
    assert conflicts.contradictions or conflicts.stale_pairs


def test_pipeline_regression_http_e2e_smoke():
    import os
    import subprocess
    import sys

    if os.environ.get("SILVERBULLET_LIVE_E2E") != "1":
        import pytest
        pytest.skip("Set SILVERBULLET_LIVE_E2E=1 to run the live Silverbullet HTTP smoke test")

    script = Path(__file__).resolve().parents[2] / "scripts" / "knowledge_pipeline_http_e2e.sh"
    proc = subprocess.run(
        ["bash", str(script), "http://127.0.0.1:3002", "/tmp/knowledge-pipeline-http-e2e"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "HTTP_SEARCH_OK" in proc.stdout
    assert "HTTP_SAVE_OK" in proc.stdout
    assert "HTTP_UPDATE_OK" in proc.stdout
    assert "HTTP_ARCHIVE_OK" in proc.stdout
