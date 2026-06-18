"""Runtime bootstrap for bridge-agents.

Keeps bridge discovery and runtime context generation out of the skill itself so
startup can preload the capability, refresh bridge metadata, and avoid asking the
user for credentials.
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import fcntl
import yaml
from urllib import request

from hermes_constants import get_config_path, get_hermes_home


BRIDGE_CONTEXT_DIR = get_hermes_home() / ".codex"
BRIDGE_CONTEXT_PATH = BRIDGE_CONTEXT_DIR / "session-bridge.md"
BRIDGE_BOOTSTRAP_LOCK = BRIDGE_CONTEXT_DIR / "bridge-bootstrap.lock"
BRIDGE_CONFIG_PATH = get_hermes_home() / "bridge.yaml"
BRIDGE_SCHEMA_VERSION = 1
BRIDGE_CONTEXT_TTL_SECONDS = 300


@dataclass(frozen=True)
class BridgeAgentInfo:
    name: str
    base_url: str
    health_url: str
    task_url: str
    status: str
    capabilities: list[str]


def load_bridge_runtime_config() -> dict:
    """Load bridge config from the runtime config source if present."""
    config_path = BRIDGE_CONFIG_PATH if BRIDGE_CONFIG_PATH.exists() else None
    if config_path is None:
        return {}
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _agent_dict_to_info(agent: dict) -> BridgeAgentInfo:
    base_url = str(agent.get("base_url") or "").rstrip("/")
    name = str(agent.get("name") or "unknown")
    return BridgeAgentInfo(
        name=name,
        base_url=base_url,
        health_url=str(agent.get("health_url") or f"{base_url}/health"),
        task_url=str(agent.get("task_url") or f"{base_url}/task"),
        status=str(agent.get("status") or "unknown"),
        capabilities=[str(item) for item in (agent.get("capabilities") or []) if str(item).strip()],
    )


def discover_bridge_agents() -> list[BridgeAgentInfo]:
    """Return configured bridge agents from runtime config."""
    cfg = load_bridge_runtime_config()
    agents = cfg.get("agents") if isinstance(cfg, dict) else None
    if not isinstance(agents, list):
        return []
    result: list[BridgeAgentInfo] = []
    for agent in agents:
        if isinstance(agent, dict):
            result.append(_agent_dict_to_info(agent))
    return result


def _check_health(url: str, timeout: float = 2.0) -> str:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            payload = response.read().decode("utf-8", "replace")
        return "ok" if payload else "ok-empty"
    except Exception as exc:
        return f"down:{type(exc).__name__}"


def _format_context(agents: Iterable[BridgeAgentInfo], *, health: dict[str, str]) -> str:
    lines = [
        "# Runtime Bridge Agents Context",
        "",
        f"schema_version: {BRIDGE_SCHEMA_VERSION}",
        f"generated_at: {datetime.now(timezone.utc).isoformat()}",
        f"config_path: {get_config_path()}",
        f"ttl_seconds: {BRIDGE_CONTEXT_TTL_SECONDS}",
        "",
        "## Available agents",
    ]
    any_agents = False
    for agent in agents:
        any_agents = True
        agent_health = health.get(agent.name, agent.status)
        lines.extend([
            f"- name: {agent.name}",
            f"  base_url: {agent.base_url}",
            f"  health: {agent.health_url}",
            f"  task: {agent.task_url}",
            f"  status: {agent.status}",
            f"  live_health: {agent_health}",
            f"  capabilities: {', '.join(agent.capabilities) if agent.capabilities else '[]'}",
        ])
    if not any_agents:
        lines.append("- none configured")
    lines.extend([
        "",
        "## Rules",
        "- Use bridge-agents for Fёdor (VPS) and 93-й (Mac Mini).",
        "- Do not ask the user for credentials.",
        "- Do not route bridge tasks through Telegram.",
    ])
    return "\n".join(lines) + "\n"


def _runtime_context_stale(path: Path) -> bool:
    if not path.exists():
        return True
    age = time.time() - path.stat().st_mtime
    return age > BRIDGE_CONTEXT_TTL_SECONDS


def write_runtime_context() -> Path:
    """Write the runtime context atomically and return its path."""
    BRIDGE_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    agents = discover_bridge_agents()
    health = {agent.name: _check_health(agent.health_url) for agent in agents}
    content = _format_context(agents, health=health)
    fd, tmp_path = tempfile.mkstemp(prefix=".session-bridge.", dir=str(BRIDGE_CONTEXT_DIR))
    os.close(fd)
    tmp_file = Path(tmp_path)
    try:
        tmp_file.write_text(content, encoding="utf-8")
        os.replace(tmp_file, BRIDGE_CONTEXT_PATH)
    finally:
        try:
            if tmp_file.exists():
                tmp_file.unlink()
        except Exception:
            pass
    return BRIDGE_CONTEXT_PATH


def load_runtime_context() -> str:
    try:
        return BRIDGE_CONTEXT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def bridge_status() -> list[dict[str, str]]:
    """Return a compact status list for bridge agents."""
    agents = discover_bridge_agents()
    return [
        {
            "name": agent.name,
            "base_url": agent.base_url,
            "health": agent.health_url,
            "task": agent.task_url,
            "status": _check_health(agent.health_url),
        }
        for agent in agents
    ]


def ensure_bridge_runtime_context() -> Path:
    BRIDGE_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    BRIDGE_BOOTSTRAP_LOCK.touch(exist_ok=True)
    with BRIDGE_BOOTSTRAP_LOCK.open("r+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            if _runtime_context_stale(BRIDGE_CONTEXT_PATH):
                return write_runtime_context()
            return BRIDGE_CONTEXT_PATH
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
