from types import SimpleNamespace

import providers
from hermes_cli import runtime_provider as rp


def _no_env(name, default=""):
    return default


def test_named_custom_provider_honors_advertised_target_model(monkeypatch):
    """A named custom provider should not force its default over an advertised target."""
    config = {
        "model": {"provider": "custom:cockpit-codex", "default": "gpt-5.5"},
        "custom_providers": [
            {
                "name": "cockpit-codex",
                "base_url": "http://127.0.0.1:52047/v1",
                "api_key": "test-key",
                "model": "gpt-5.6-sol",
                "api_mode": "chat_completions",
                "models": {
                    "gpt-5.6-sol": {"context_length": 256000},
                    "gpt-5.6-luna": {"context_length": 256000},
                },
            }
        ],
    }
    monkeypatch.setattr(rp, "load_config", lambda: config)
    monkeypatch.setattr(rp, "_get_model_config", lambda: config["model"])
    monkeypatch.setattr(rp, "load_pool", lambda _pool_key: SimpleNamespace(has_credentials=lambda: False))
    monkeypatch.setattr(rp, "_getenv", _no_env)

    resolved = rp.resolve_runtime_provider(
        requested="custom:cockpit-codex",
        target_model="gpt-5.6-luna",
    )

    assert resolved["provider"] == "custom"
    assert resolved["model"] == "gpt-5.6-luna"


def test_named_custom_provider_falls_back_to_default_for_unknown_target(monkeypatch):
    config = {
        "model": {"provider": "custom:cockpit-codex", "default": "gpt-5.5"},
        "custom_providers": [
            {
                "name": "cockpit-codex",
                "base_url": "http://127.0.0.1:52047/v1",
                "api_key": "test-key",
                "model": "gpt-5.6-sol",
                "api_mode": "chat_completions",
                "models": ["gpt-5.6-sol", {"id": "gpt-5.6-luna"}],
            }
        ],
    }
    monkeypatch.setattr(rp, "load_config", lambda: config)
    monkeypatch.setattr(rp, "_get_model_config", lambda: config["model"])
    monkeypatch.setattr(rp, "load_pool", lambda _pool_key: SimpleNamespace(has_credentials=lambda: False))
    monkeypatch.setattr(rp, "_getenv", _no_env)

    resolved = rp.resolve_runtime_provider(
        requested="custom:cockpit-codex",
        target_model="not-advertised",
    )

    assert resolved["model"] == "gpt-5.6-sol"


def test_provider_profile_per_model_api_mode_overrides_configured_mode(monkeypatch):
    """Provider profiles can declare per-model api_mode for custom providers."""
    class Profile:
        def get_model_api_mode(self, model):
            if model == "gpt-5.6-luna":
                return "anthropic_messages"
            return None

    config = {
        "model": {
            "provider": "custom:cockpit-codex",
            "default": "gpt-5.6-luna",
            "api_mode": "chat_completions",
        },
        "custom_providers": [
            {
                "name": "cockpit-codex",
                "base_url": "http://127.0.0.1:52047/v1",
                "api_key": "test-key",
                "model": "gpt-5.6-luna",
                "api_mode": "chat_completions",
                "models": ["gpt-5.6-luna"],
            }
        ],
    }
    monkeypatch.setattr(rp, "load_config", lambda: config)
    monkeypatch.setattr(rp, "_get_model_config", lambda: config["model"])
    monkeypatch.setattr(rp, "load_pool", lambda _pool_key: SimpleNamespace(has_credentials=lambda: False))
    monkeypatch.setattr(rp, "_getenv", _no_env)
    monkeypatch.setattr(providers, "get_provider_profile", lambda provider: Profile() if provider == "cockpit-codex" else None)

    resolved = rp.resolve_runtime_provider(
        requested="custom:cockpit-codex",
        target_model="gpt-5.6-luna",
    )

    assert resolved["api_mode"] == "anthropic_messages"
