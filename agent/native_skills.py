"""Native skill activation helpers.

Native skills are skill IDs with executable bootstrap behavior. They are small
and explicit so prompt-only skills cannot accidentally gain code execution.
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from hermes_constants import get_hermes_home


@dataclass(frozen=True)
class NativeSkillActivation:
    name: str
    mode: str
    detail: str


_NATIVE_SKILLS = {
    "bridge-agents": {
        "module": None,
        "mode": "bootstrap+prompt",
        "detail": "bridge runtime context",
        "bootstrap": "_bootstrap_bridge_agents",
    },
    "ask-expert": {
        "module": "tools.ask_expert",
        "mode": "tool",
        "detail": "ask_expert route=custom:anymodel/cc/claude-sonnet-5",
    },
}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _bootstrap_bridge_agents() -> None:
    """Write the non-secret bridge runtime context used by preloaded prompts."""

    now = int(time.time())
    path = get_hermes_home() / ".codex" / "session-bridge.md"
    content = f"""# Bridge Agents Runtime Context

Generated: {now}

## Agents

| Agent | Base URL | Health | Task |
|---|---|---|---|
| Fedor VPS | http://100.92.229.56:8001 | http://100.92.229.56:8001/health | http://100.92.229.56:8001/task |
| Mac 93 | http://192.168.1.93:8002 | http://192.168.1.93:8002/health | http://192.168.1.93:8002/task |

## Routing Rules

- Use HTTP bridge for tasks explicitly addressed to Fedor/VPS or 93/Mac.
- Check the documented health endpoint before task dispatch.
- Do not use Telegram as fallback for bridge-addressed tasks.
- Do not write API keys or other secrets into this file or prompts.
"""
    _atomic_write(path, content)


_BOOTSTRAP_HANDLERS: dict[str, Callable[[], None]] = {
    "_bootstrap_bridge_agents": _bootstrap_bridge_agents,
}


def activate_native_skill(skill_name: str) -> NativeSkillActivation | None:
    normalized = (skill_name or "").strip()
    spec = _NATIVE_SKILLS.get(normalized)
    if spec is None:
        return None
    module = spec.get("module")
    if module:
        __import__(module)
    bootstrap_name = spec.get("bootstrap")
    if bootstrap_name:
        _BOOTSTRAP_HANDLERS[bootstrap_name]()
    return NativeSkillActivation(
        name=normalized,
        mode=spec["mode"],
        detail=spec["detail"],
    )


def activate_auto_preloaded_native_skills(skill_names: Iterable[str]) -> list[NativeSkillActivation]:
    activations: list[NativeSkillActivation] = []
    seen: set[str] = set()
    for skill_name in skill_names:
        normalized = (skill_name or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        activation = activate_native_skill(normalized)
        if activation is not None:
            activations.append(activation)
    return activations


def format_native_skill_log(activations: Iterable[NativeSkillActivation]) -> str:
    return ",".join(f"{activation.name}:{activation.mode}" for activation in activations)
