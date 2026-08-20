"""Runtime preflight gate for Hermes fleet-change mutations.

This guard is intentionally small and lives outside individual tools so terminal,
file, and service mutations share the same fail-closed policy.  It only activates
when the current task is classified as a Hermes fleet change; ordinary work keeps
using the existing tool-specific guards.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
from typing import Any, Mapping


FLEET_CONTEXT_MARKER_ENV = "HERMES_FLEET_CHANGE_CONTEXT"
PREFLIGHT_MARKER_ENV = "HERMES_FLEET_PREFLIGHT_MARKER"

_MUTATION_FILE_TOOLS = {"write_file", "patch"}
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


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "fleet"}


def _contains_fleet_context(text: str | None) -> bool:
    return bool(text and _FLEET_CONTEXT_RE.search(text))


def _terminal_is_mutation(args: Mapping[str, Any]) -> bool:
    command = str(args.get("command") or "")
    if not command.strip():
        return False
    return bool(_TERMINAL_MUTATION_RE.search(command))


def _is_mutating_tool(tool_name: str, args: Mapping[str, Any]) -> bool:
    if tool_name == "terminal":
        return _terminal_is_mutation(args)
    if tool_name in _MUTATION_FILE_TOOLS or tool_name in _MUTATION_SERVICE_TOOLS:
        return True
    return False


def _load_marker(path: str) -> dict[str, Any] | None:
    try:
        marker = Path(path).expanduser()
        if not marker.is_file():
            return None
        data = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _marker_valid(data: Mapping[str, Any] | None) -> bool:
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
    user_task: str | None,
) -> bool:
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
    args: Mapping[str, Any] | None,
    *,
    user_task: str | None = None,
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
    if _marker_valid(marker_data):
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
