"""Runtime preflight gate for Hermes fleet-change mutations.

This guard is intentionally small and lives outside individual tools so terminal,
file, and service mutations share the same fail-closed policy.  It only activates
when the current task is classified as a Hermes fleet change; ordinary work keeps
using the existing tool-specific guards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
from typing import Any, Mapping, Optional, List, Dict


FLEET_CONTEXT_MARKER_ENV = "HERMES_FLEET_CHANGE_CONTEXT"
PREFLIGHT_MARKER_ENV = "HERMES_FLEET_PREFLIGHT_MARKER"
CRON_FLEET_APPROVAL_ENV = "HERMES_CRON_FLEET_APPROVAL"

_MUTATION_FILE_TOOLS = {"write_file", "patch"}
_KNOWLEDGE_VAULT_DIR = (Path.home() / ".hermes" / "knowledge").resolve()
_MUTATION_SERVICE_TOOLS = {"ha_call_service"}
_READ_ONLY_TOOLS = {
    "read_file",
    "search_files",
    "web_search",
    "web_extract",
    "ha_get_state",
    "ha_list_entities",
    "ha_list_services",
}

_FLEET_CONTEXT_RE = re.compile(
    r"\b(hermes|gateway|bridge|fedor|archivarius|fleet|provider|routing|"
    r"config\.yaml|\.env|systemd|launchd|tailscale|amnezia|vps|mac\s*93|93)\b",
    re.IGNORECASE,
)

_TERMINAL_MUTATION_RE = re.compile(
    r"(?:"
    r"\b(?:systemctl|service|launchctl|restart|shutdown|reboot|poweroff|kill(?:all)?|pkill|"
    r"rm|unlink|rmdir|truncate|mv|cp|install|chmod|chown|tee|apply_patch)\b|"
    r"\bbrew\s+services\b|"
    r"\bdocker\s+(?:compose\s+)?(?:restart|stop|rm|down)\b|"
    r"\bhermes\s+(?:gateway\s+)?(?:restart|stop|update|config\s+set)\b|"
    r"\b(?:python\s+-c|perl\s+-pi|sed\s+-i)\b|"
    r"\bcat\s*>|>>?|"
    r"\bgit\s+(?:clean|reset|checkout|switch|pull|merge|rebase|commit|push)\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FleetPreflightDecision:
    blocked: bool
    message: str = ""
    classified: bool = False
    mutation: bool = False
    marker_path: str = ""


def _truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "fleet"}


def _contains_fleet_context(text: Optional[str]) -> bool:
    return bool(text and _FLEET_CONTEXT_RE.search(text))


def _path_is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent)
        return True
    except (OSError, ValueError):
        return False


def _file_args_target_only_knowledge_vault(args: Mapping[str, Any]) -> bool:
    candidates = [str(args.get(k) or "").strip() for k in ("path", "file_path")]
    targets = [Path(value).expanduser() for value in candidates if value]
    return bool(targets) and all(_path_is_inside(target, _KNOWLEDGE_VAULT_DIR) for target in targets)


def _shell_words(command: str) -> List[str]:
    try:
        import shlex

        return shlex.split(command)
    except Exception:
        return command.split()


def _strip_safe_prefix(words: List[str]) -> List[str]:
    remaining = list(words)
    while remaining:
        head = remaining[0]
        if "=" in head and not head.startswith("-"):
            key = head.split("=", 1)[0]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                remaining.pop(0)
                continue
        if head in {"env", "command"}:
            remaining.pop(0)
            continue
        break
    return remaining


def _split_ssh_remote_words(words: List[str]) -> List[str] | None:
    if not words or words[0] != "ssh":
        return None
    i = 1
    while i < len(words):
        token = words[i]
        if token == "--":
            i += 1
            break
        if token.startswith("-"):
            i += 1
            if token in {"-i", "-F", "-J", "-l", "-o", "-p"} and i < len(words):
                i += 1
            continue
        i += 1  # host
        break
    return words[i:] if i <= len(words) else []


def _trim_cd_prefix(words: List[str]) -> List[str]:
    if len(words) >= 4 and words[0] == "cd" and words[2] in {"&&", ";"}:
        return words[3:]
    return words


def _terminal_read_only_audit_command(command: str) -> bool:
    words = _strip_safe_prefix(_shell_words(command))
    remote = _split_ssh_remote_words(words)
    if remote is not None:
        words = remote
    words = _strip_safe_prefix(_trim_cd_prefix(words))
    if not words:
        return False
    if words[0] == "git" and len(words) >= 2:
        sub = words[1]
        if sub in {"rev-parse", "rev-list", "log", "show", "branch", "remote"}:
            return True
        if sub == "diff":
            return True
        if sub == "status":
            return "GIT_OPTIONAL_LOCKS=0" in command or "--no-optional-locks" in words
        return False
    if words[0] == "curl":
        lowered = [w.lower() for w in words]
        if any(w in lowered for w in ["-x", "--request"]):
            try:
                method = lowered[lowered.index("-x") + 1].upper()
            except Exception:
                try:
                    method = lowered[lowered.index("--request") + 1].upper()
                except Exception:
                    return False
            if method not in {"GET", "HEAD"}:
                return False
        if any(w in lowered for w in ["-d", "--data", "--data-raw", "--data-binary", "--form", "-f", "--upload-file"]):
            return False
        return any(part in command for part in ["/health", "/status", "/ready", "/live"])
    return False


def _terminal_policy_command_is_read_only(command: str) -> bool:
    normalized = " ".join(command.split())
    if " policy " not in f" {normalized} ":
        return False
    if re.search(r"\bpolicy\s+autoload\b", normalized) and re.search(r"\s--write\b", normalized):
        return False
    return bool(
        re.search(r"\bhermes(?:_cli\.main)?\s+policy\b", normalized)
        or re.search(r"\bpython\S*\s+-m\s+hermes_cli\.main\s+policy\b", normalized)
    )


def _terminal_contains_allowed_read_only_audit(command: str) -> bool:
    """Recognize conservative compound diagnostics as read-only.

    Fleet triage often needs harmless shell glue/redirection around commands
    like ssh, git status/diff, pgrep, df, and health checks.  The broad
    mutation regex catches redirection (`2>/dev/null`) and compound shells, so
    keep an explicit allow-list for audit commands while still rejecting
    lifecycle, install, file-write, and destructive git verbs.
    """

    lowered = command.lower()
    hard_denies = (
        r"\b(?:systemctl|service|launchctl|restart|shutdown|reboot|poweroff|kill(?:all)?|pkill)\b",
        r"\b(?:rm|unlink|rmdir|truncate|mv|cp|install|chmod|chown|tee|apply_patch)\b",
        r"\b(?:python\s+-c|perl\s+-pi|sed\s+-i)\b",
        r"\bcat\s*>",
        r"\bgit\s+(?:clean|reset|checkout|switch|pull|merge|rebase|commit|push|cherry-pick)\b",
        r"\b(?:apt|dnf|yum|brew|pip|uv\s+pip)\s+(?:install|remove|upgrade|update)\b",
    )
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in hard_denies):
        return False

    # Require a recognizable read-only audit anchor.
    if not re.search(r"\bssh\b", lowered) and not re.search(
        r"\bgit\s+(?:status|diff|rev-parse|rev-list|log|show|branch|remote)\b", lowered
    ):
        return False

    allowed_commands = {
        "ssh", "set", "echo", "cd", "date", "hostname", "id", "git",
        "df", "pgrep", "grep", "true", "false", "command", "test",
        "pwd", "printf", "uname", "lsblk", "findmnt", "pvs", "vgs",
        "lvs", "stat", "getent", "ps", "curl",
    }
    # Check only command-position words, not arguments/paths.
    for match in re.finditer(r"(?:^|[;&|()])\s*([A-Za-z_][A-Za-z0-9_.-]*)", command):
        word = match.group(1)
        next_char = command[match.end(1) : match.end(1) + 1]
        if next_char == "=" and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", word):
            continue
        if word not in allowed_commands:
            return False
    return True


def _terminal_is_mutation(args: Mapping[str, Any]) -> bool:
    command = str(args.get("command") or "")
    if not command.strip():
        return False
    if _terminal_policy_command_is_read_only(command):
        return False
    if _terminal_read_only_audit_command(command):
        return False
    if _terminal_contains_allowed_read_only_audit(command):
        return False
    return bool(_TERMINAL_MUTATION_RE.search(command))


def _is_mutating_tool(tool_name: str, args: Mapping[str, Any]) -> bool:
    if tool_name == "terminal":
        return _terminal_is_mutation(args)
    if tool_name in _MUTATION_FILE_TOOLS or tool_name in _MUTATION_SERVICE_TOOLS:
        return True
    return False


def _load_marker(path: str) -> Dict[str, Any] | None:
    try:
        marker = Path(path).expanduser()
        if not marker.is_file():
            return None
        data = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None



def _parse_iso_datetime(value: Optional[str]) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _cron_approval_from_context() -> Optional[Mapping[str, Any]]:
    try:
        from gateway.session_context import get_session_env

        raw = get_session_env(CRON_FLEET_APPROVAL_ENV, "")
    except Exception:
        raw = os.environ.get(CRON_FLEET_APPROVAL_ENV, "")
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    return data if isinstance(data, Mapping) else None


def _cron_approval_valid(data: Optional[Mapping[str, Any]], tool_name: str, args: Mapping[str, Any]) -> bool:
    if not data:
        return False
    expires_at = _parse_iso_datetime(str(data.get("expires_at") or ""))
    if not expires_at or expires_at <= datetime.now(timezone.utc):
        return False
    if str(data.get("job_id") or "") != str(os.environ.get("HERMES_CRON_JOB_ID") or ""):
        return False
    if tool_name != "terminal":
        return False
    command = str(args.get("command") or "")
    allowed_actions = {str(a).strip().lower() for a in data.get("allowed_actions") or []}
    if not allowed_actions:
        return False
    if "git_fetch" in allowed_actions and re.search(r"\bgit\s+fetch\b", command):
        pass
    elif "git_pull_ff_only" in allowed_actions and re.search(r"\bgit\s+pull\s+--ff-only\b", command):
        pass
    elif "install_editable" in allowed_actions and re.search(r"\buv\s+pip\s+install\s+-e\b|\bpip\s+install\s+-e\b", command):
        pass
    else:
        return False
    scopes = data.get("scopes") or {}
    hosts = [str(h) for h in scopes.get("hosts") or []]
    repos = [str(r) for r in scopes.get("repos") or []]
    if hosts and not any(host in command for host in hosts):
        return False
    if repos and not any(repo in command for repo in repos):
        return False
    return all(bool(str(data.get(k) or "").strip()) for k in ("approval", "evidence_target"))

def _marker_valid(data: Optional[Mapping[str, Any]]) -> bool:
    if not data:
        return False
    return all(
        bool(str(data.get(key) or "").strip())
        for key in ("policy_stage", "backup_plan", "approval", "evidence_target")
    )


def _classified_fleet_context(
    *,
    tool_name: str,
    args: Mapping[str, Any],
    user_task: Optional[str],
) -> bool:
    if tool_name in _MUTATION_FILE_TOOLS and _file_args_target_only_knowledge_vault(args):
        return False
    if _truthy(os.environ.get(FLEET_CONTEXT_MARKER_ENV)):
        return True
    if _contains_fleet_context(user_task):
        return True
    if tool_name == "terminal" and _contains_fleet_context(str(args.get("command") or "")):
        return True
    if tool_name in _MUTATION_FILE_TOOLS:
        haystack = "\n".join(str(args.get(k) or "") for k in ("path", "file_path", "patch"))
        return _contains_fleet_context(haystack)
    return False


def check_fleet_preflight(
    tool_name: str,
    args: Optional[Mapping[str, Any]],
    *,
    user_task: Optional[str] = None,
) -> FleetPreflightDecision:
    """Return a blocking decision for fleet-change mutation attempts.

    Read-only audit tools and ordinary non-fleet tasks are allowed.  Once a task
    is classified as Hermes fleet work, terminal/file/service mutations require a
    marker JSON with policy stage, backup plan, approval, and evidence target.
    """

    normalized_args: Mapping[str, Any] = args if isinstance(args, Mapping) else {}
    if tool_name in _READ_ONLY_TOOLS:
        return FleetPreflightDecision(blocked=False)

    classified = _classified_fleet_context(
        tool_name=tool_name,
        args=normalized_args,
        user_task=user_task,
    )
    mutation = _is_mutating_tool(tool_name, normalized_args)
    if not classified or not mutation:
        return FleetPreflightDecision(blocked=False, classified=classified, mutation=mutation)

    marker_path = str(os.environ.get(PREFLIGHT_MARKER_ENV) or "").strip()
    marker_data = _load_marker(marker_path) if marker_path else None
    if _marker_valid(marker_data) or _cron_approval_valid(_cron_approval_from_context(), tool_name, normalized_args):
        return FleetPreflightDecision(
            blocked=False,
            classified=True,
            mutation=True,
            marker_path=marker_path,
        )

    return FleetPreflightDecision(
        blocked=True,
        classified=True,
        mutation=True,
        marker_path=marker_path,
        message=(
            "Fleet-change mutation blocked: classified Hermes fleet context requires "
            "a valid preflight marker before writes/restarts/deletes. The marker must "
            "record policy_stage, backup_plan, approval, and evidence_target. "
            f"Set {PREFLIGHT_MARKER_ENV} to that marker path after explicit approval."
        ),
    )
