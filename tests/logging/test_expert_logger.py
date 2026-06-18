import json
import importlib.util
from pathlib import Path

_LOGGER_PATH = Path(__file__).resolve().parents[2] / "logging" / "expert_logger.py"
_spec = importlib.util.spec_from_file_location(
    "hermes_expert_logger",
    _LOGGER_PATH,
)
_expert_logger = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_expert_logger)

DEFAULT_LOG_PATH = _expert_logger.DEFAULT_LOG_PATH
log_expert_call = _expert_logger.log_expert_call


def test_log_expert_call_writes_jsonl(tmp_path, monkeypatch):
    log_path = tmp_path / "expert_calls.log"
    monkeypatch.setattr(_expert_logger, "DEFAULT_LOG_PATH", log_path)

    entry = log_expert_call(
        reason="architecture review",
        allowed=True,
        context_length=120,
        summary_length=40,
        request_tokens=100,
        response_tokens=200,
        policy_result="allowed",
        quota_result="allowed",
    )

    assert log_path.exists()
    payload = json.loads(log_path.read_text().strip())
    assert payload["reason"] == "architecture review"
    assert payload["allowed"] is True
    assert payload["context_length"] == 120
    assert payload["summary_length"] == 40
    assert payload["policy_result"] == "allowed"
    assert payload["quota_result"] == "allowed"
    assert "timestamp" in payload
    assert entry["reason"] == "architecture review"


def test_default_log_path_points_to_logs_dir():
    assert str(DEFAULT_LOG_PATH).endswith("logs/expert_calls.log")
