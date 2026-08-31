from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "vm_hermes_update_rehearsal.py"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, path: str, content: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _make_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)

    live = tmp_path / "live"
    subprocess.run(["git", "clone", "-q", str(remote), str(live)], check=True)
    _git(live, "config", "user.email", "test@example.com")
    _git(live, "config", "user.name", "Test User")
    _commit(live, "README.md", "base\n", "base")
    _git(live, "branch", "-M", "main")
    _git(live, "push", "-q", "-u", "origin", "HEAD:main")

    target = tmp_path / "target"
    subprocess.run(["git", "clone", "-q", "--branch", "main", str(remote), str(target)], check=True)
    _git(target, "config", "user.email", "test@example.com")
    _git(target, "config", "user.name", "Test User")
    upstream_head = _commit(target, "upstream.txt", "upstream\n", "upstream change")
    _git(target, "push", "-q", "origin", "HEAD:main")

    local_commit = _commit(live, "local.txt", "local\n", "local patchkit commit")
    return live, remote, local_commit


def test_rehearsal_uses_scratch_not_live_and_emits_ready_report(tmp_path: Path):
    live, _remote, local_commit = _make_repo(tmp_path)
    scratch = tmp_path / "scratch"
    releases = tmp_path / "releases"

    before_head = _git(live, "rev-parse", "HEAD")
    before_status = _git(live, "status", "--short", "--branch")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(live),
            "--scratch-root",
            str(scratch),
            "--release-root",
            str(releases),
            "--release-name",
            "unit-rehearsal",
            "--skip-tests",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    after_head = _git(live, "rev-parse", "HEAD")
    after_status = _git(live, "status", "--short", "--branch")
    assert after_head == before_head
    assert after_status == before_status

    report_path = releases / "unit-rehearsal" / "readiness.json"
    markdown_path = releases / "unit-rehearsal" / "readiness.md"
    dry_run_log = releases / "unit-rehearsal" / "dry-run.log"
    assert report_path.exists()
    assert markdown_path.exists()
    assert dry_run_log.exists()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "READY"
    assert report["live_checkout_modified"] is False
    assert str(report["scratch_worktree"]).startswith(str(scratch))
    assert report["target_head"]
    assert report["carried_commits"] == [local_commit]
    assert report["conflicts"] == []
    assert report["tests"] == [
        {"name": "provider routing", "status": "SKIPPED", "command": "uv run --extra dev python -m pytest tests/providers tests/hermes_cli/test_config.py -q"},
        {"name": "ask_expert", "status": "SKIPPED", "command": "uv run --extra dev python -m pytest tests/agent/test_native_ask_expert.py tests/tools/test_ask_expert_native_overlay.py -q"},
        {"name": "cron native activation", "status": "SKIPPED", "command": "uv run --extra dev python -m pytest tests/cron/test_cron_native_skill_activation.py -q"},
        {"name": "schema smoke", "status": "SKIPPED", "command": "uv run --extra dev hermes config check"},
    ]


def test_dry_run_writes_plan_without_creating_scratch_clone(tmp_path: Path):
    live, _remote, _local_commit = _make_repo(tmp_path)
    scratch = tmp_path / "scratch"
    releases = tmp_path / "releases"

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(live),
            "--scratch-root",
            str(scratch),
            "--release-root",
            str(releases),
            "--release-name",
            "dryrun",
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    log = (releases / "dryrun" / "dry-run.log").read_text(encoding="utf-8")
    report = json.loads((releases / "dryrun" / "readiness.json").read_text(encoding="utf-8"))
    assert "DRY RUN: would create scratch clone" in log
    assert report["status"] == "BLOCKED"
    assert report["dry_run"] is True
    assert not (scratch / "dryrun" / "clone").exists()
