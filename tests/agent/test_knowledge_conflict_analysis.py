from __future__ import annotations

from pathlib import Path

from agent.knowledge_conflict_analysis import analyze_knowledge_conflicts


def _write_entry(root: Path, category: str, entry_id: str, title: str, body: str, *, status: str = "active", updated: str = "2026-06-01"):
    path = root / category / f"{entry_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
created: 2026-06-01
updated: {updated}
status: {status}
tags:
  - {category}
source: conversation
confidence: 0.9
supersedes: []
superseded_by: []
title: {title}
category: {category}
---

# {title}

{body}
""",
        encoding="utf-8",
    )
    return path


def test_detects_duplicates_contradictions_and_stale_pairs(tmp_path):
    vault = tmp_path / "knowledge"
    _write_entry(vault, "preferences", "a1", "Reply style", "User prefers concise replies.")
    _write_entry(vault, "preferences", "a2", "Reply style", "User prefers concise replies.")
    _write_entry(vault, "preferences", "a3", "Reply style", "User never wants concise replies.", updated="2026-06-05")
    _write_entry(vault, "preferences", "a4", "Reply style", "User prefers concise replies.", updated="2026-06-06")

    report = analyze_knowledge_conflicts(vault)

    assert report.active_entries == 4
    assert report.to_dict()["counts"]["duplicates"] >= 1
    assert report.to_dict()["counts"]["contradictions"] >= 1
    assert report.to_dict()["counts"]["stale_pairs"] >= 1
    assert "Knowledge conflict analysis" in report.to_markdown()


def test_detects_memory_conflicts(tmp_path):
    vault = tmp_path / "knowledge"
    _write_entry(vault, "preferences", "b1", "Reply style", "User prefers verbose replies.")

    report = analyze_knowledge_conflicts(vault, memory_facts=["User prefers concise replies."])

    assert report.to_dict()["counts"]["memory_conflicts"] == 1
    assert report.memory_conflicts[0].memory_fact == "User prefers concise replies."
