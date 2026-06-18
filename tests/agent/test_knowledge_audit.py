from __future__ import annotations

from pathlib import Path

from agent.knowledge_audit import build_weekly_audit_report


def _write_entry(root: Path, category: str, entry_id: str, title: str, body: str, *, status: str = "active", updated: str = "2026-01-01") -> Path:
    path = root / category / f"{entry_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
created: 2026-01-01
updated: {updated}
status: {status}
tags:
  - test
source: conversation
confidence: 0.9
supersedes: []
superseded_by: []
title: {title}
entry_id: {entry_id}
category: {category}
---

{body}
""",
        encoding="utf-8",
    )
    return path


def test_weekly_audit_finds_duplicates_conflicts_and_stale(tmp_path):
    root = tmp_path / "knowledge"
    _write_entry(root, "preferences", "a1", "Writing Style", "User prefers short answers.", updated="2026-01-01")
    _write_entry(root, "preferences", "a2", "Writing Style", "User prefers short answers.", updated="2026-01-02")
    _write_entry(root, "architecture", "b1", "Routing Policy", "Use deepseek only.", updated="2026-01-01", status="active")
    _write_entry(root, "architecture", "b2", "Routing Policy", "Use fallback providers.", updated="2026-01-03", status="superseded")
    _write_entry(root, "decisions", "c1", "Old Decision", "Keep old default.", updated="2025-01-01", status="active")

    report = build_weekly_audit_report(root, stale_after_days=30)
    data = report.to_dict()

    assert data["total_entries"] == 5
    assert data["duplicate_groups"]
    assert any(item["kind"] == "duplicate" for item in data["duplicate_groups"])
    assert data["conflict_groups"]
    assert any(item["kind"] == "conflict" for item in data["conflict_groups"])
    assert data["stale_candidates"]
    assert any(item["kind"] == "stale" for item in data["stale_candidates"])
    assert report.to_markdown().startswith("# Knowledge audit report")


def test_weekly_audit_counts_statuses(tmp_path):
    root = tmp_path / "knowledge"
    _write_entry(root, "preferences", "a1", "Writing Style", "Short answers.", status="active", updated="2026-01-01")
    _write_entry(root, "preferences", "a2", "Writing Style 2", "Long answers.", status="archived", updated="2026-01-01")

    report = build_weekly_audit_report(root, stale_after_days=30)
    data = report.to_dict()

    assert data["active_entries"] == 1
    assert data["archived_entries"] == 1
    assert data["stale_entries"] == 1
