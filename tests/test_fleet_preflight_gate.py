from datetime import datetime, timedelta, timezone
import json

import model_tools
import agent.fleet_preflight as fleet_preflight
from agent.fleet_preflight import PREFLIGHT_MARKER_ENV


def _decode(result):
    return json.loads(result)


def test_fleet_restart_blocks_without_preflight(monkeypatch):
    called = False

    def fake_dispatch(*_args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "terminal",
            {"command": "hermes gateway restart"},
            user_task="Hermes fleet rollout on Archivarius",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is False
    assert result["error_type"] == "fleet_preflight_required"
    assert result["classified_fleet_context"] is True
    assert "policy_stage" in result["error"]


def test_fleet_read_only_audit_stays_allowed(monkeypatch):
    called = False

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name, "args": args})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "terminal",
            {"command": "git status --short --branch"},
            user_task="Hermes fleet read-only audit on Fedor",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True


def test_policy_preflight_with_restart_intent_stays_read_only(monkeypatch):
    called = False

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name, "args": args})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "terminal",
            {
                "command": (
                    "python3 -m hermes_cli.main policy preflight --intent "
                    "'change Hermes provider routing and restart gateway' --output json"
                )
            },
            user_task="Hermes fleet policy smoke",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True


def test_ordinary_non_fleet_write_stays_allowed(monkeypatch, tmp_path):
    called = False
    target = tmp_path / "note.txt"

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name, "path": args.get("path")})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "write_file",
            {"path": str(target), "content": "hello"},
            user_task="write a local project note",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True



def test_ordinary_write_under_hermes_named_temp_dir_stays_allowed(monkeypatch, tmp_path):
    called = False
    hermes_named_dir = tmp_path / "hermes-not-fleet"
    hermes_named_dir.mkdir()
    target = hermes_named_dir / "note.txt"

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name, "path": args.get("path")})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "write_file",
            {"path": str(target), "content": "hello"},
            user_task="write a local project note",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True

def test_knowledge_vault_daily_write_stays_allowed(monkeypatch):
    called = False
    target = "/home/wwolfy/.hermes/knowledge/daily/2026-08-21.md"

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name, "path": args.get("path")})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "write_file",
            {"path": target, "content": "# daily"},
            user_task="Hermes KB daily log maintenance",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True


def test_service_mutation_blocks_in_fleet_context(monkeypatch):
    called = False

    def fake_dispatch(*_args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True})

    monkeypatch.delenv(PREFLIGHT_MARKER_ENV, raising=False)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "ha_call_service",
            {"domain": "script", "service": "turn_on", "entity_id": "script.gateway_restart"},
            user_task="Hermes fleet gateway restart through Home Assistant",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is False
    assert result["error_type"] == "fleet_preflight_required"


def test_preflight_guard_errors_fail_closed_for_mutations(monkeypatch):
    called = False

    def broken_guard(*_args, **_kwargs):
        raise RuntimeError("boom")

    def fake_dispatch(*_args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True})

    monkeypatch.setattr(fleet_preflight, "check_fleet_preflight", broken_guard)
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "write_file",
            {"path": "/tmp/example.txt", "content": "hello"},
            user_task="ordinary write",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is False
    assert result["error_type"] == "fleet_preflight_guard_error"


def test_valid_preflight_marker_allows_fleet_mutation(monkeypatch, tmp_path):
    called = False
    marker = tmp_path / "fleet-preflight.json"
    marker.write_text(
        json.dumps(
            {
                "policy_stage": "Fedor canary",
                "backup_plan": "timestamped config backup",
                "approval": "Vadim approved this exact restart",
                "evidence_target": str(tmp_path / "evidence.md"),
            }
        ),
        encoding="utf-8",
    )

    def fake_dispatch(name, args, **_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True, "tool": name})

    monkeypatch.setenv(PREFLIGHT_MARKER_ENV, str(marker))
    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)

    result = _decode(
        model_tools.handle_function_call(
            "terminal",
            {"command": "systemctl restart hermes-gateway.service"},
            user_task="Hermes fleet rollout on Fedor",
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
        )
    )

    assert called is True
    assert result["ok"] is True



def test_read_only_ssh_git_audit_allowed_without_marker(monkeypatch):
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)

    decision = fleet_preflight.check_fleet_preflight(
        "terminal",
        {
            "command": "ssh fedor cd /srv/hermes && GIT_OPTIONAL_LOCKS=0 git status --short --branch"
        },
        user_task="daily remote Hermes update audit",
    )

    assert decision.blocked is False
    assert decision.classified is True
    assert decision.mutation is False


def test_git_pull_still_blocked_without_cron_approval(monkeypatch):
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)
    monkeypatch.delenv("HERMES_CRON_FLEET_APPROVAL", raising=False)
    monkeypatch.delenv("HERMES_CRON_JOB_ID", raising=False)

    decision = fleet_preflight.check_fleet_preflight(
        "terminal",
        {"command": "ssh fedor cd /srv/hermes && git pull --ff-only"},
        user_task="daily remote Hermes update",
    )

    assert decision.blocked is True
    assert decision.classified is True
    assert decision.mutation is True


def test_scoped_cron_approval_allows_only_matching_pull(monkeypatch):
    approval = {
        "job_id": "af5d2a9913d1",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "allowed_actions": ["git_pull_ff_only"],
        "scopes": {"hosts": ["fedor"], "repos": ["/srv/hermes"]},
        "approval": "operator approved daily remote update",
        "evidence_target": "/home/wwolfy/.hermes/fleet/releases/cron/evidence.md",
    }
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.setenv("HERMES_CRON_JOB_ID", "af5d2a9913d1")
    monkeypatch.setenv("HERMES_CRON_FLEET_APPROVAL", json.dumps(approval))

    allowed = fleet_preflight.check_fleet_preflight(
        "terminal",
        {"command": "ssh fedor cd /srv/hermes && git pull --ff-only"},
        user_task="daily remote Hermes update",
    )
    denied_host = fleet_preflight.check_fleet_preflight(
        "terminal",
        {"command": "ssh mac93 cd /srv/hermes && git pull --ff-only"},
        user_task="daily remote Hermes update",
    )
    denied_action = fleet_preflight.check_fleet_preflight(
        "terminal",
        {"command": "ssh fedor cd /srv/hermes && systemctl --user restart hermes-gateway.service"},
        user_task="daily remote Hermes update",
    )

    assert allowed.blocked is False
    assert denied_host.blocked is True
    assert denied_action.blocked is True


def test_expired_cron_approval_blocks_mutation(monkeypatch):
    approval = {
        "job_id": "af5d2a9913d1",
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "allowed_actions": ["git_pull_ff_only"],
        "scopes": {"hosts": ["fedor"], "repos": ["/srv/hermes"]},
        "approval": "operator approved daily remote update",
        "evidence_target": "/home/wwolfy/.hermes/fleet/releases/cron/evidence.md",
    }
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.setenv("HERMES_CRON_JOB_ID", "af5d2a9913d1")
    monkeypatch.setenv("HERMES_CRON_FLEET_APPROVAL", json.dumps(approval))

    decision = fleet_preflight.check_fleet_preflight(
        "terminal",
        {"command": "ssh fedor cd /srv/hermes && git pull --ff-only"},
        user_task="daily remote Hermes update",
    )

    assert decision.blocked is True



def test_session_context_cron_fleet_approval_is_task_local(monkeypatch):
    from gateway.session_context import clear_session_vars, get_session_env, set_session_vars

    approval = json.dumps({"job_id": "job-a"})
    monkeypatch.setenv("HERMES_CRON_FLEET_APPROVAL", "ambient")
    tokens = set_session_vars(cron_fleet_approval=approval)
    try:
        assert get_session_env("HERMES_CRON_FLEET_APPROVAL") == approval
    finally:
        clear_session_vars(tokens)
    assert get_session_env("HERMES_CRON_FLEET_APPROVAL") == ""

def test_compound_read_only_fedor_triage_allowed_without_marker(monkeypatch):
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)

    command = (
        "ssh -i /home/wwolfy/.ssh/id_ed25519_archivarius "
        "-o BatchMode=yes root@100.92.229.56 "
        "'set -euo pipefail; "
        "cd /usr/local/lib/hermes-agent; "
        "GIT_OPTIONAL_LOCKS=0 git status --short --branch; "
        "git diff --check -- gateway/shutdown_forensics.py 2>/dev/null || true; "
        "pgrep -af hermes || true; "
        "df -h /'"
    )

    decision = fleet_preflight.check_fleet_preflight(
        "terminal",
        {"command": command},
        user_task="Fedor Hermes dirty WIP read-only triage",
    )

    assert decision.blocked is False
    assert decision.classified is True
    assert decision.mutation is False


def test_compound_read_only_allowlist_still_blocks_git_pull(monkeypatch):
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)

    decision = fleet_preflight.check_fleet_preflight(
        "terminal",
        {
            "command": (
                "ssh root@100.92.229.56 "
                "'cd /usr/local/lib/hermes-agent; git pull --ff-only'"
            )
        },
        user_task="Fedor Hermes dirty WIP triage",
    )

    assert decision.blocked is True
    assert decision.classified is True
    assert decision.mutation is True

def test_scoped_cron_approval_allows_rollout_publish_and_upstream(monkeypatch):
    approval = {
        "job_id": "af5d2a9913d1",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "allowed_actions": [
            "git_publish_rollout_branch",
            "git_set_upstream_rollout_branch",
        ],
        "scopes": {
            "hosts": ["fedor"],
            "repos": ["/usr/local/lib/hermes-agent"],
        },
        "approval": "operator approved rollout branch publication",
        "evidence_target": "/home/wwolfy/.hermes/fleet/releases/patchkit/evidence.md",
    }
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.setenv("HERMES_CRON_JOB_ID", "af5d2a9913d1")
    monkeypatch.setenv("HERMES_CRON_FLEET_APPROVAL", json.dumps(approval))

    publish = fleet_preflight.check_fleet_preflight(
        "terminal",
        {
            "command": (
                "ssh fedor cd /usr/local/lib/hermes-agent && "
                "git push origin HEAD:refs/heads/rollout/runtime-provider-patchkit-20260826-fedor"
            )
        },
        user_task="daily remote Hermes update",
    )
    upstream = fleet_preflight.check_fleet_preflight(
        "terminal",
        {
            "command": (
                "ssh fedor cd /usr/local/lib/hermes-agent && "
                "git branch --set-upstream-to=origin/rollout/runtime-provider-patchkit-20260826-fedor "
                "rollout/runtime-provider-patchkit-20260826-fedor"
            )
        },
        user_task="daily remote Hermes update",
    )
    force_push = fleet_preflight.check_fleet_preflight(
        "terminal",
        {
            "command": (
                "ssh fedor cd /usr/local/lib/hermes-agent && "
                "git push --force origin HEAD:refs/heads/rollout/runtime-provider-patchkit-20260826-fedor"
            )
        },
        user_task="daily remote Hermes update",
    )
    main_push = fleet_preflight.check_fleet_preflight(
        "terminal",
        {
            "command": "ssh fedor cd /usr/local/lib/hermes-agent && git push origin HEAD:refs/heads/main"
        },
        user_task="daily remote Hermes update",
    )

    assert publish.blocked is False
    assert upstream.blocked is False
    assert force_push.blocked is True
    assert main_push.blocked is True

def test_read_only_service_status_allowed_without_marker(monkeypatch):
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)

    for command in (
        "ssh root@100.92.229.56 'systemctl --user status hermes-gateway'",
        "ssh vadimklusov@192.168.1.93 'launchctl list | grep com.hermes'",
        "ssh root@100.92.229.56 'systemctl --user is-active hermes-gateway'",
        "ssh vadimklusov@192.168.1.93 'launchctl print system/com.hermes.gateway'",
    ):
        decision = fleet_preflight.check_fleet_preflight(
            "terminal",
            {"command": command},
            user_task="daily remote Hermes update postflight",
        )
        assert decision.blocked is False
        assert decision.classified is True
        assert decision.mutation is False


def test_service_lifecycle_verbs_still_blocked_without_marker(monkeypatch):
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)
    monkeypatch.delenv("HERMES_CRON_FLEET_APPROVAL", raising=False)
    monkeypatch.delenv("HERMES_CRON_JOB_ID", raising=False)

    for command in (
        "ssh root@100.92.229.56 'systemctl --user restart hermes-gateway'",
        "ssh root@100.92.229.56 'systemctl --user stop hermes-gateway'",
        "ssh vadimklusov@192.168.1.93 'launchctl kickstart -k system/com.hermes.gateway'",
        "ssh vadimklusov@192.168.1.93 'launchctl load /Library/LaunchDaemons/com.hermes.gateway.plist'",
        "ssh root@100.92.229.56 'systemctl'",
    ):
        decision = fleet_preflight.check_fleet_preflight(
            "terminal",
            {"command": command},
            user_task="daily remote Hermes update",
        )
        assert decision.blocked is True
        assert decision.classified is True
        assert decision.mutation is True


def test_read_only_commands_with_redirects_allowed_without_marker(monkeypatch):
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)

    for command in (
        # redirects must NOT be treated as file-write mutations
        "git ls-remote https://github.com/NousResearch/hermes-agent.git 2>&1",
        "git ls-remote https://github.com/NousResearch/hermes-agent.git 2>/dev/null",
        "curl -sSf http://100.92.229.56:8001/health 2>&1",
        "curl -sSf http://192.168.1.93:8002/health 2>/dev/null",
        # read-only find/ls
        "find ~/.hermes/fleet -maxdepth 1 -type f 2>&1",
        "ls -la ~/.hermes/fleet/ 2>&1",
        # quoted ssh service query with trailing redirect glue
        "ssh root@100.92.229.56 'systemctl --user status hermes-gateway' 2>&1",
        "ssh vadimklusov@192.168.1.93 'launchctl list' 2>&1",
        # git -C read-only forms over ssh
        "ssh root@100.92.229.56 'git -C /usr/local/lib/hermes-agent status --porcelain=v2 --branch' 2>&1",
        "ssh root@100.92.229.56 'git -C /usr/local/lib/hermes-agent rev-list --count HEAD' 2>&1",
        # private ssh diagnostics
        "ssh -i ~/.ssh/id_ed25519_archivarius root@100.92.229.56 'echo hi' 2>&1",
    ):
        decision = fleet_preflight.check_fleet_preflight(
            "terminal",
            {"command": command},
            user_task="daily remote Hermes update audit",
        )
        assert decision.blocked is False, command
        assert decision.classified is True, command
        assert decision.mutation is False, command


def test_git_dash_c_and_destructive_find_still_blocked(monkeypatch):
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)
    monkeypatch.delenv("HERMES_CRON_FLEET_APPROVAL", raising=False)
    monkeypatch.delenv("HERMES_CRON_JOB_ID", raising=False)

    for command in (
        # git -C forms must still be recognized as mutations
        "git -C /usr/local/lib/hermes-agent pull --ff-only",
        "git -C /usr/local/lib/hermes-agent reset --hard HEAD",
        "git --git-dir=/usr/local/lib/hermes-agent/.git pull --ff-only",
        "ssh root@100.92.229.56 'git -C /usr/local/lib/hermes-agent pull --ff-only'",
        "git -C /usr/local/lib/hermes-agent push origin HEAD:refs/heads/rollout/test",
        # destructive find forms
        "find /tmp -delete",
        "find /tmp -name '*.pyc' -delete",
        "find /usr/local/lib/hermes-agent -name '*.pyc' -exec rm {} \\;",
        "find /tmp -name '*.tmp' -ok rm {} \\;",
        # ordinary redirect mutation still blocked
        "echo x > /tmp/out.txt",
        "cat > /tmp/out.txt",
    ):
        decision = fleet_preflight.check_fleet_preflight(
            "terminal",
            {"command": command},
            user_task="daily remote Hermes update",
        )
        assert decision.blocked is True, command
        assert decision.classified is True, command
        assert decision.mutation is True, command


def test_mutation_regex_has_no_accidental_empty_alternation(monkeypatch):
    monkeypatch.delenv("HERMES_FLEET_PREFLIGHT_MARKER", raising=False)
    monkeypatch.delenv("HERMES_FLEET_CHANGE_CONTEXT", raising=False)

    # An accidental trailing '|' before ')' in the alternation used to match
    # ANY command as a mutation; a benign non-fleet command must stay clean.
    decision = fleet_preflight.check_fleet_preflight(
        "terminal",
        {"command": "echo hello"},
        user_task="compose a friendly message",
    )
    assert decision.mutation is False
    assert decision.blocked is False
