from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent import bridge_bootstrap as bb


def test_write_runtime_context_includes_agents_and_rules(monkeypatch, tmp_path):
    monkeypatch.setattr(bb, "BRIDGE_CONTEXT_DIR", tmp_path)
    monkeypatch.setattr(bb, "BRIDGE_CONTEXT_PATH", tmp_path / "session-bridge.md")
    monkeypatch.setattr(bb, "BRIDGE_CONFIG_PATH", tmp_path / "bridge.yaml")
    (tmp_path / "bridge.yaml").write_text(
        "agents:\n"
        "  - name: fedor\n"
        "    base_url: http://127.0.0.1:8001\n"
        "    status: ok\n"
        "    capabilities:\n"
        "      - cron\n"
        "      - internet\n"
        "  - name: mac-93\n"
        "    base_url: http://127.0.0.1:8002\n"
        "    status: ok\n"
        "    capabilities:\n"
        "      - macos\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bb, "_check_health", lambda url, timeout=2.0: "ok")

    path = bb.write_runtime_context()

    assert path == tmp_path / "session-bridge.md"
    text = path.read_text(encoding="utf-8")
    assert "schema_version: 1" in text
    assert "ttl_seconds: 300" in text
    assert "name: fedor" in text
    assert "name: mac-93" in text
    assert "live_health: ok" in text
    assert "Do not ask the user for credentials." in text
    assert "Do not route bridge tasks through Telegram." in text


def test_discover_bridge_agents_reads_runtime_config(monkeypatch, tmp_path):
    monkeypatch.setattr(bb, "BRIDGE_CONFIG_PATH", tmp_path / "bridge.yaml")
    (tmp_path / "bridge.yaml").write_text(
        "agents:\n"
        "  - name: fedor\n"
        "    base_url: http://127.0.0.1:8001\n"
        "    status: ok\n"
        "    capabilities: [cron, internet]\n",
        encoding="utf-8",
    )

    agents = bb.discover_bridge_agents()

    assert len(agents) == 1
    assert agents[0].name == "fedor"
    assert agents[0].task_url == "http://127.0.0.1:8001/task"
    assert agents[0].health_url == "http://127.0.0.1:8001/health"
    assert agents[0].capabilities == ["cron", "internet"]


def test_ensure_bridge_runtime_context_refreshes_stale_file(monkeypatch, tmp_path):
    monkeypatch.setattr(bb, "BRIDGE_CONTEXT_DIR", tmp_path)
    monkeypatch.setattr(bb, "BRIDGE_CONTEXT_PATH", tmp_path / "session-bridge.md")
    monkeypatch.setattr(bb, "BRIDGE_BOOTSTRAP_LOCK", tmp_path / "bridge-bootstrap.lock")
    monkeypatch.setattr(bb, "BRIDGE_CONTEXT_TTL_SECONDS", 0)
    monkeypatch.setattr(bb, "_check_health", lambda url, timeout=2.0: "ok")
    monkeypatch.setattr(bb, "discover_bridge_agents", lambda: [])
    (tmp_path / "session-bridge.md").write_text("old", encoding="utf-8")

    path = bb.ensure_bridge_runtime_context()

    assert path == tmp_path / "session-bridge.md"
    assert "Runtime Bridge Agents Context" in path.read_text(encoding="utf-8")
