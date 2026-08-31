import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "observability" / "mac93_tailnet_diagnostic.py"
spec = importlib.util.spec_from_file_location("mac93_tailnet_diagnostic", MODULE_PATH)
assert spec is not None
mac93 = importlib.util.module_from_spec(spec)
sys.modules["mac93_tailnet_diagnostic"] = mac93
assert spec.loader is not None
spec.loader.exec_module(mac93)


def _result(name, ok=True, stdout="", stderr="", returncode=0):
    return mac93.ProbeResult(
        name=name,
        ok=ok,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        command=f"read-only {name}",
    )


def test_diagnoses_stopped_tailscale_with_tailnet_bound_exporter():
    results = {
        "bridge": _result("bridge", stdout="ok"),
        "tailscale": _result("tailscale", stdout='{"BackendState":"Stopped","Self":{"Online":false,"TailscaleIPs":["100.124.204.124"]},"Health":[]}'),
        "node_process": _result("node_process", stdout="123 /opt/homebrew/bin/node_exporter --web.listen-address=100.124.204.124:9100\n"),
        "node_listen": _result(
            "node_listen",
            stdout="COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\nnode_expo 123 vadim 3u IPv4 0t0 TCP 100.124.204.124:9100 (LISTEN)\n",
        ),
        "metrics_local": _result("metrics_local", ok=False, returncode=7, stderr="curl failed"),
        "metrics_tailnet": _result("metrics_tailnet", ok=False, returncode=7, stderr="curl failed"),
    }

    payload = mac93.diagnose(results, tailnet_ip="100.124.204.124", exporter_port=9100)

    assert payload["status"] == "tailscale_stopped_exporter_present"
    assert payload["checks"]["tailscale"]["backend_state"] == "Stopped"
    assert payload["checks"]["node_exporter_process"]["running"] is True
    assert payload["checks"]["node_exporter_listen"]["tailnet_bound"] is True
    assert payload["safety"]["forbidden_actions_used"] is False
    assert "route" not in str(payload["probes"]).lower()


def test_healthy_when_tailnet_metrics_return_200():
    results = {
        "bridge": _result("bridge", stdout="ok"),
        "tailscale": _result("tailscale", stdout='{"BackendState":"Running","Self":{"Online":true,"TailscaleIPs":["100.124.204.124"]},"Health":[]}'),
        "node_process": _result("node_process", stdout="123 /opt/homebrew/bin/node_exporter --web.listen-address=100.124.204.124:9100\n"),
        "node_listen": _result(
            "node_listen",
            stdout="COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\nnode_expo 123 vadim 3u IPv4 0t0 TCP 100.124.204.124:9100 (LISTEN)\n",
        ),
        "metrics_local": _result("metrics_local", stdout="200"),
        "metrics_tailnet": _result("metrics_tailnet", stdout="200"),
    }

    payload = mac93.diagnose(results, tailnet_ip="100.124.204.124", exporter_port=9100)

    assert payload["status"] == "healthy"
    assert payload["severity"] == "ok"
    assert payload["checks"]["metrics"]["tailnet_ok"] is True


def test_remote_allowlist_rejects_lifecycle_commands():
    try:
        mac93._run_remote("bad", "host", ("tailscale", "up"), connect_timeout=1, timeout=1)
    except RuntimeError as exc:
        assert "refusing non-read-only" in str(exc)
    else:
        raise AssertionError("lifecycle command was not rejected")


def test_ssh_command_uses_batch_mode_identity_file_and_expected_host():
    cmd = mac93._ssh_command(
        "vadimklusov@192.168.1.93",
        ("/bin/echo", "ok"),
        connect_timeout=3,
        identity_file="~/.ssh/hermes_93",
    )

    assert cmd[:2] == ["ssh", "-o"]
    assert "BatchMode=yes" in cmd
    assert "IdentitiesOnly=yes" in cmd
    assert "ConnectTimeout=3" in cmd
    assert "vadimklusov@192.168.1.93" in cmd
    assert cmd[-1] == "/bin/echo ok"
