#!/usr/bin/env python3
"""Non-mutating VM Hermes scratch update rehearsal.

This script prepares evidence for a later human-approved live activation. It
never checks out or rewrites the live repository. Reconciliation happens in a
scratch clone/worktree, and the result is a READY/BLOCKED report under the
fleet release evidence directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_SCRATCH_ROOT = Path.home() / ".hermes" / "fleet" / "scratch"
DEFAULT_RELEASE_ROOT = Path.home() / ".hermes" / "fleet" / "releases"
DEFAULT_TESTS = [
    (
        "provider routing",
        "uv run --extra dev python -m pytest tests/providers tests/hermes_cli/test_config.py -q",
    ),
    (
        "ask_expert",
        "uv run --extra dev python -m pytest tests/agent/test_native_ask_expert.py tests/tools/test_ask_expert_native_overlay.py -q",
    ),
    (
        "cron native activation",
        "uv run --extra dev python -m pytest tests/cron/test_cron_native_skill_activation.py -q",
    ),
    (
        "schema smoke",
        "uv run --extra dev hermes config check",
    ),
]


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str


class Recorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lines: list[str] = []

    def log(self, message: str) -> None:
        line = message.rstrip()
        self._lines.append(line)
        print(line)

    def command(self, cwd: Path, *args: str, check: bool = True) -> CommandResult:
        cmd = [*args]
        self.log(f"$ (cd {cwd} && {' '.join(cmd)})")
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if result.stdout:
            self._lines.append(result.stdout.rstrip())
        if result.stderr:
            self._lines.append(result.stderr.rstrip())
        self._lines.append(f"[exit {result.returncode}]")
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, output=result.stdout, stderr=result.stderr
            )
        return CommandResult(" ".join(cmd), result.returncode, result.stdout, result.stderr)

    def save(self) -> None:
        self.path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")


def _git(rec: Recorder, repo: Path, *args: str, check: bool = True) -> CommandResult:
    return rec.command(repo, "git", *args, check=check)


def _git_text(rec: Recorder, repo: Path, *args: str, check: bool = True) -> str:
    return _git(rec, repo, *args, check=check).stdout.strip()


def _safe_release_name() -> str:
    return time.strftime("vm-hermes-rehearsal-%Y%m%dT%H%M%SZ", time.gmtime())


def _parse_ahead_behind(status_line: str) -> dict[str, int]:
    result = {"ahead": 0, "behind": 0}
    if "[" not in status_line or "]" not in status_line:
        return result
    bracket = status_line.split("[", 1)[1].split("]", 1)[0]
    for part in bracket.split(","):
        part = part.strip()
        if part.startswith("ahead "):
            result["ahead"] = int(part.split()[1])
        elif part.startswith("behind "):
            result["behind"] = int(part.split()[1])
    return result


def _split_local_commits(rec: Recorder, repo: Path) -> list[str]:
    output = _git_text(rec, repo, "rev-list", "--reverse", "@{u}..HEAD", check=False)
    return [line for line in output.splitlines() if line.strip()]


def _changed_files(rec: Recorder, repo: Path) -> list[str]:
    output = _git_text(rec, repo, "status", "--porcelain=v1", check=False)
    return [line for line in output.splitlines() if line.strip()]


def _run_test(rec: Recorder, scratch_repo: Path, name: str, command: str, skip: bool) -> dict[str, Any]:
    if skip:
        rec.log(f"SKIP test: {name} :: {command}")
        return {"name": name, "status": "SKIPPED", "command": command}

    result = subprocess.run(command, cwd=scratch_repo, shell=True, capture_output=True, text=True)
    rec.log(f"$ (cd {scratch_repo} && {command})")
    if result.stdout:
        rec.log(result.stdout.rstrip())
    if result.stderr:
        rec.log(result.stderr.rstrip())
    rec.log(f"[exit {result.returncode}]")
    return {
        "name": name,
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "command": command,
        "returncode": result.returncode,
    }


def _write_reports(report: dict[str, Any], release_dir: Path) -> None:
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / "readiness.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tests_md = "\n".join(
        f"- {item['name']}: {item['status']} — `{item['command']}`" for item in report["tests"]
    )
    conflicts_md = "none" if not report["conflicts"] else "\n".join(f"- {c}" for c in report["conflicts"])
    carried_md = "none" if not report["carried_commits"] else "\n".join(f"- `{c}`" for c in report["carried_commits"])
    md = f"""# VM Hermes scratch update rehearsal

Status: {report['status']}

- Live repo: `{report['live_repo']}`
- Live checkout modified: `{report['live_checkout_modified']}`
- Scratch worktree: `{report['scratch_worktree']}`
- Base HEAD: `{report['base_head']}`
- Target HEAD: `{report['target_head']}`
- Dry run: `{report['dry_run']}`

## Carried commits
{carried_md}

## Conflicts
{conflicts_md}

## Focused tests
{tests_md}

## Evidence
- JSON: `{release_dir / 'readiness.json'}`
- Dry-run log: `{release_dir / 'dry-run.log'}`
"""
    (release_dir / "readiness.md").write_text(md, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    release_name = args.release_name or _safe_release_name()
    scratch_dir = (args.scratch_root / release_name).resolve()
    scratch_repo = scratch_dir / "clone"
    release_dir = (args.release_root / release_name).resolve()
    rec = Recorder(release_dir / "dry-run.log")

    report: dict[str, Any] = {
        "schema": 1,
        "status": "BLOCKED",
        "dry_run": bool(args.dry_run),
        "live_repo": str(repo),
        "scratch_worktree": str(scratch_repo),
        "release_dir": str(release_dir),
        "base_head": None,
        "target_head": None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
        "carried_commits": [],
        "dirty_entries": [],
        "conflicts": [],
        "tests": [],
        "live_checkout_modified": False,
        "script": str(Path(__file__).resolve()),
    }

    before_head = _git_text(rec, repo, "rev-parse", "HEAD")
    before_status = _git_text(rec, repo, "status", "--short", "--branch")
    report["base_head"] = before_head
    status_first = before_status.splitlines()[0] if before_status else ""
    report.update(_parse_ahead_behind(status_first))
    report["dirty_entries"] = _changed_files(rec, repo)
    report["upstream"] = _git_text(rec, repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    report["carried_commits"] = _split_local_commits(rec, repo)

    _git(rec, repo, "fetch", "--dry-run", "--prune", "origin", check=False)
    main_head = _git_text(rec, repo, "ls-remote", "origin", "refs/heads/main", check=False)
    head_ref = _git_text(rec, repo, "ls-remote", "origin", "HEAD", check=False)
    if main_head:
        target_head = main_head.split()[0]
    elif head_ref:
        target_head = head_ref.split()[0]
    else:
        target_head = _git_text(rec, repo, "rev-parse", "@{u}")
    report["target_head"] = target_head
    if not report["carried_commits"] and before_head != target_head:
        merge_base = _git_text(rec, repo, "merge-base", before_head, target_head, check=False)
        if merge_base:
            report["carried_commits"] = [
                line
                for line in _git_text(rec, repo, "rev-list", "--reverse", f"{merge_base}..{before_head}", check=False).splitlines()
                if line.strip()
            ]

    if args.dry_run:
        rec.log(f"DRY RUN: would create scratch clone at {scratch_repo}")
        rec.log("DRY RUN: would fetch origin/main and cherry-pick carried commits")
        for name, command in DEFAULT_TESTS:
            report["tests"].append({"name": name, "status": "PLANNED", "command": command})
        after_head = _git_text(rec, repo, "rev-parse", "HEAD")
        after_status = _git_text(rec, repo, "status", "--short", "--branch")
        report["live_checkout_modified"] = after_head != before_head or after_status != before_status
        _write_reports(report, release_dir)
        rec.save()
        return 0

    if scratch_repo.exists():
        raise SystemExit(f"scratch clone already exists: {scratch_repo}")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    rec.command(scratch_dir, "git", "clone", "--no-checkout", str(repo), str(scratch_repo))
    _git(rec, scratch_repo, "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*")
    _git(rec, scratch_repo, "config", "user.email", "hermes-rehearsal@example.invalid")
    _git(rec, scratch_repo, "config", "user.name", "Hermes Update Rehearsal")
    _git(rec, scratch_repo, "checkout", "-B", "update-rehearsal", target_head)

    for commit in report["carried_commits"]:
        result = _git(rec, scratch_repo, "cherry-pick", "--allow-empty", commit, check=False)
        if result.returncode != 0:
            report["conflicts"].append(commit)
            _git(rec, scratch_repo, "cherry-pick", "--abort", check=False)
            break

    for name, command in DEFAULT_TESTS:
        report["tests"].append(_run_test(rec, scratch_repo, name, command, args.skip_tests))

    failed_tests = [t for t in report["tests"] if t["status"] == "FAIL"]
    report["status"] = "READY" if not report["conflicts"] and not failed_tests else "BLOCKED"

    after_head = _git_text(rec, repo, "rev-parse", "HEAD")
    after_status = _git_text(rec, repo, "status", "--short", "--branch")
    report["live_checkout_modified"] = after_head != before_head or after_status != before_status
    if report["live_checkout_modified"]:
        report["status"] = "BLOCKED"

    _write_reports(report, release_dir)
    rec.save()
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run non-mutating VM Hermes update rehearsal in scratch clone."
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Live checkout to inspect read-only")
    parser.add_argument("--scratch-root", type=Path, default=DEFAULT_SCRATCH_ROOT)
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    parser.add_argument("--release-name", default="")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; do not create scratch clone")
    parser.add_argument("--skip-tests", action="store_true", help="Create rehearsal report without running focused tests")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
