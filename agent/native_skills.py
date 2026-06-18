"""Native skill activation helpers.

This module bridges skill preload metadata to concrete runtime handlers so
selected skills can be activated natively instead of relying on prompt text
alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent.bridge_bootstrap import ensure_bridge_runtime_context
from tools import routine_worker as routine_worker_module


@dataclass(frozen=True)
class NativeSkillActivation:
    name: str
    mode: str
    detail: str


@dataclass(frozen=True)
class NativeSkillSpec:
    name: str
    mode: str
    activate: Callable[[], NativeSkillActivation]


def _activate_bridge_agents() -> NativeSkillActivation:
    path = ensure_bridge_runtime_context()
    return NativeSkillActivation(
        name="bridge-agents",
        mode="bootstrap+prompt",
        detail=str(path),
    )


def _activate_routine_worker() -> NativeSkillActivation:
    _ = routine_worker_module.routine_worker
    return NativeSkillActivation(
        name="routine-worker",
        mode="tool+routing",
        detail="routine_worker registered for gpt-5.4-mini",
    )


_NATIVE_SKILLS: dict[str, NativeSkillSpec] = {
    "bridge-agents": NativeSkillSpec(
        name="bridge-agents",
        mode="bootstrap+prompt",
        activate=_activate_bridge_agents,
    ),
    "routine-worker": NativeSkillSpec(
        name="routine-worker",
        mode="tool+routing",
        activate=_activate_routine_worker,
    ),
}


def resolve_native_skill(name: str) -> NativeSkillSpec | None:
    return _NATIVE_SKILLS.get((name or "").strip())


def activate_native_skill(name: str) -> NativeSkillActivation | None:
    spec = resolve_native_skill(name)
    if spec is None:
        return None
    return spec.activate()


def activate_auto_preloaded_native_skills(skills: list[str]) -> list[NativeSkillActivation]:
    activations: list[NativeSkillActivation] = []
    seen: set[str] = set()
    for skill_name in skills:
        normalized = (skill_name or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        activation = activate_native_skill(normalized)
        if activation is not None:
            activations.append(activation)
    return activations


def format_native_skill_log(activations: list[NativeSkillActivation]) -> str:
    return ", ".join(f"{item.name}:{item.mode}" for item in activations)
