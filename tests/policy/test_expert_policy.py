from policy.expert_policy import should_call_expert


def test_allows_architecture_tasks():
    assert should_call_expert(
        "Design an architecture for a multi-agent orchestration layer",
        "Need advice on trade-offs and failure modes",
    ) is True


def test_allows_security_tasks():
    assert should_call_expert(
        "Review the security posture of the authentication flow",
        "Looking for vulnerability and hardening analysis",
    ) is True


def test_rejects_routine_chat():
    assert should_call_expert(
        "Chat with me about today",
        "small talk",
    ) is False


def test_rejects_simple_translation():
    assert should_call_expert(
        "Translate this sentence to English",
        "routine translation",
    ) is False


def test_rejects_template_code():
    assert should_call_expert(
        "Write a simple CRUD endpoint",
        "boilerplate code only",
    ) is False
