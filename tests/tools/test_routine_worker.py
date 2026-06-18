import types

from policy.routine_policy import should_use_routine_worker
from tools import routine_worker as routine_worker_module


def test_tool_is_registered():
    from tools.registry import registry

    assert 'routine_worker' in registry._tools
    tool = registry._tools['routine_worker']
    assert tool.toolset == 'hermes-routine-worker'
    assert tool.description.startswith('Run a small')


def test_policy_allows_simple_routine_task(monkeypatch):
    monkeypatch.setattr('policy.routine_policy.should_call_expert', lambda task, context: False)
    assert should_use_routine_worker('Summarize this short note into bullets', '') is True


def test_policy_rejects_expert_or_high_risk_task(monkeypatch):
    monkeypatch.setattr('policy.routine_policy.should_call_expert', lambda task, context: True)
    assert should_use_routine_worker('Design a multi-agent architecture', '') is False
    monkeypatch.setattr('policy.routine_policy.should_call_expert', lambda task, context: False)
    assert should_use_routine_worker('Delete production credentials after deploy', '') is False


def test_routine_worker_rejects_when_policy_rejects(monkeypatch):
    monkeypatch.setattr(routine_worker_module, 'should_use_routine_worker', lambda task, context: False)
    monkeypatch.setattr(
        routine_worker_module,
        'call_llm',
        lambda *a, **k: (_ for _ in ()).throw(AssertionError('should not call llm')),
    )

    result = routine_worker_module.routine_worker('Design architecture')
    assert 'rejected' in result.lower()


def test_routine_worker_uses_gpt54_mini_on_neurogate(monkeypatch):
    monkeypatch.setattr(routine_worker_module, 'should_use_routine_worker', lambda task, context: True)

    captured = {}

    class DummyChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class DummyResponse:
        choices = [DummyChoice('routine answer')]

    def fake_call_llm(*, provider=None, messages, model):
        captured['provider'] = provider
        captured['model'] = model
        captured['messages'] = messages
        return DummyResponse()

    monkeypatch.setattr(routine_worker_module, 'call_llm', fake_call_llm)

    result = routine_worker_module.routine_worker('Summarize this', context='short text')

    assert result == 'routine answer'
    assert captured['provider'] == 'custom:neurogate'
    assert captured['model'] == 'gpt-5.4-mini'
    assert 'routine worker' in captured['messages'][0]['content'].lower()


def test_registry_dispatch_invokes_routine_worker_handler(monkeypatch):
    from tools.registry import registry

    entry = registry.get_entry('routine_worker')
    assert entry is not None

    captured = {}

    def fake_routine_worker(task, context=''):
        captured['task'] = task
        captured['context'] = context
        return 'worker answer'

    monkeypatch.setattr(routine_worker_module, 'routine_worker', fake_routine_worker)

    result = entry.handler(task='Summarize', context='abc')

    assert result == 'worker answer'
    assert captured == {'task': 'Summarize', 'context': 'abc'}
