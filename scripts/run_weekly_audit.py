#!/usr/bin/env python3
"""Run the weekly knowledge audit and print a compact report.

This is intentionally small so it can be reused by cron or by manual checks.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.knowledge_audit import build_weekly_audit_report, render_weekly_audit_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the weekly knowledge audit")
    parser.add_argument(
        "--vault-root",
        default=str(Path.home() / ".hermes" / "knowledge"),
        help="Path to the knowledge vault root",
    )
    parser.add_argument(
        "--stale-after-days",
        type=int,
        default=30,
        help="Mark entries older than this as stale candidates",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_weekly_audit_report(args.vault_root, stale_after_days=args.stale_after_days)
    print(render_weekly_audit_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
