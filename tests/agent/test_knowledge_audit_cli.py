from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agent.knowledge_audit import build_weekly_audit_report, render_weekly_audit_report


def test_render_weekly_audit_report_contains_summary(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "note.md").write_text(
        """---
created: 2026-06-01
updated: 2026-06-01
status: active
tags: [test]
source: conversation
confidence: 0.9
---

hello world
""",
        encoding="utf-8",
    )

    report = build_weekly_audit_report(root)
    rendered = render_weekly_audit_report(report)

    assert "weekly knowledge audit" in rendered
    assert "total:" in rendered
    assert "active:" in rendered


def test_weekly_audit_script_can_run(tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "note.md").write_text(
        """---
created: 2026-06-01
updated: 2026-06-01
status: active
tags: [test]
source: conversation
confidence: 0.9
---

hello world
""",
        encoding="utf-8",
    )

    script = Path("/home/wwolfy/.hermes/hermes-agent/scripts/run_weekly_audit.py")
    result = subprocess.run(
        [sys.executable, str(script), "--vault-root", str(root), "--stale-after-days", "30"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "weekly knowledge audit" in result.stdout
    assert "total:" in result.stdout
