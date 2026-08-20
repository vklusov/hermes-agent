"""Tests for native routine worker registration and routing."""

from __future__ import annotations

import model_tools
from tools.registry import registry
from tools.routine_worker import build_routine_delegate_args, resolve_routine_route
from toolsets import resolve_toolset


def test_routine_worker_is_registered_and_exposed():
    assert registry.get_entry("routine_worker") is not None
    assert "routine_worker" in resolve_toolset("delegation")
    assert "routine_worker" in resolve_toolset("hermes-cli")

    definitions = model_tools.get_tool_definitions(
        enabled_toolsets=["delegation"],
        quiet_mode=True,
        skip_tool_search_assembly=True,
    )
    names = {definition["function"]["name"] for definition in definitions}
    assert "delegate_task" in names
    assert "routine_worker" in names


def test_cheap_flash_route_can_target_cpa_vps_when_configured(monkeypatch):
    import tools.routine_worker as routine_worker_module

    cfg = {"cheap_flash": {"provider": "custom:cpa-vps", "model": "pilot-smoke"}}
    route = resolve_routine_route("cheap_flash", config=cfg)
    assert route["provider"] == "custom:cpa-vps"
    assert route["model"] == "pilot-smoke"

    monkeypatch.setattr(routine_worker_module, "_load_routine_worker_config", lambda: cfg)

    delegate_args = build_routine_delegate_args(
        {"task_type": "cheap_flash", "objective": "Return exactly OK"}
    )
    assert delegate_args["provider"] == "custom:cpa-vps"
    assert delegate_args["model"] == "pilot-smoke"
