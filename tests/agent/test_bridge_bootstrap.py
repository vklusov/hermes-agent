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


def test_send_bridge_task_posts_json_and_uses_named_key(monkeypatch, tmp_path):
    monkeypatch.setattr(bb, "BRIDGE_CONFIG_PATH", tmp_path / "bridge.yaml")
    (tmp_path / "bridge.yaml").write_text(
        "agents:\n"
        "  - name: Fedor\n"
        "    base_url: http://127.0.0.1:8001\n"
        "    task_url: http://127.0.0.1:8001/task\n"
        "    health_url: http://127.0.0.1:8001/health\n"
        "    status: configured\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BRIDGE_API_KEY_FEDOR", "fedor-secret")
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"accepted","task_id":"abc123"}'

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.headers)
        seen["body"] = req.data.decode("utf-8")
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(bb.request, "urlopen", fake_urlopen)

    result = bb.send_bridge_task("fedor", "проверить серверы", deliver="origin", timeout=3)

    assert seen["url"] == "http://127.0.0.1:8001/task"
    assert seen["headers"]["X-api-key"] == "fedor-secret"
    assert seen["timeout"] == 3
    assert '"task": "проверить серверы"' in seen["body"]
    assert '"deliver": "origin"' in seen["body"]
    assert result["status"] == "accepted"
    assert result["task_id"] == "abc123"
    assert result["agent"] == "Fedor"


def test_send_bridge_task_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(bb, "BRIDGE_CONFIG_PATH", tmp_path / "bridge.yaml")
    monkeypatch.setattr(bb, "_BRIDGE_ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("BRIDGE_API_KEY_FEDOR", raising=False)
    (tmp_path / "bridge.yaml").write_text(
        "agents:\n"
        "  - name: fedor\n"
        "    base_url: http://127.0.0.1:8001\n"
        "    task_url: http://127.0.0.1:8001/task\n"
        "    health_url: http://127.0.0.1:8001/health\n"
        "    status: configured\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("", encoding="utf-8")

    try:
        bb.send_bridge_task("fedor", "test")
    except RuntimeError as exc:
        assert "BRIDGE_API_KEY_FEDOR" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
