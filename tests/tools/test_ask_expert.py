import json
import types
from pathlib import Path

from agent.expert_router import evaluate_expert_need
from tools import ask_expert as ask_expert_module


def test_tool_is_registered():
    from tools.registry import registry

    assert 'ask_expert' in registry._tools
    tool = registry._tools['ask_expert']
    assert tool.toolset == 'hermes-ask-expert'
    assert tool.description.startswith('Escalate rare expert-level tasks')


def test_router_returns_routine_for_simple_tasks(monkeypatch):
    monkeypatch.setattr(ask_expert_module, 'should_call_expert', lambda task, context: False)
    result = evaluate_expert_need('Translate text', 'routine')
    assert result['need_expert'] is False
    assert result['reason'] == 'routine task'


def test_ask_expert_blocks_when_policy_rejects(monkeypatch):
    monkeypatch.setattr(ask_expert_module, 'should_call_expert', lambda task, context: False)
    monkeypatch.setattr(ask_expert_module, 'can_call_expert', lambda: True)
    monkeypatch.setattr(ask_expert_module, 'register_expert_call', lambda: None)
    monkeypatch.setattr(ask_expert_module, 'build_expert_context', lambda history, task: 'Summary: x\nCurrent task: y')
    monkeypatch.setattr(ask_expert_module, 'log_expert_call', lambda **kwargs: None)
    monkeypatch.setattr(ask_expert_module, 'DEFAULT_LOG_PATH', Path('/tmp/does-not-matter.log'), raising=False)

    called = {'value': False}

    def fake_call_llm(*args, **kwargs):
        called['value'] = True
        raise AssertionError('should not call llm')

    monkeypatch.setattr(ask_expert_module, 'call_llm', fake_call_llm)
    result = ask_expert_module.ask_expert('Translate this')
    assert 'policy' in result.lower()
    assert called['value'] is False


def test_ask_expert_blocks_when_quota_exceeded(monkeypatch):
    monkeypatch.setattr(ask_expert_module, 'should_call_expert', lambda task, context: True)
    monkeypatch.setattr(ask_expert_module, 'can_call_expert', lambda: False)
    monkeypatch.setattr(ask_expert_module, 'log_expert_call', lambda **kwargs: None)
    monkeypatch.setattr(ask_expert_module, 'call_llm', lambda *a, **k: (_ for _ in ()).throw(AssertionError('should not call llm')))

    result = ask_expert_module.ask_expert('Design a multi-agent architecture')
    assert 'quota exceeded' in result.lower()


def test_ask_expert_success_logs_and_registers(monkeypatch, tmp_path):
    log_path = tmp_path / 'expert_calls.log'
    monkeypatch.setattr(ask_expert_module, 'should_call_expert', lambda task, context: True)
    monkeypatch.setattr(ask_expert_module, 'can_call_expert', lambda: True)
    calls = {'registered': 0}

    def fake_register():
        calls['registered'] += 1

    monkeypatch.setattr(ask_expert_module, 'register_expert_call', fake_register)
    monkeypatch.setattr(ask_expert_module, 'build_expert_context', lambda history, task: 'Summary: compact\nCurrent task: ' + task)
    monkeypatch.setattr(ask_expert_module, 'DEFAULT_LOG_PATH', log_path, raising=False)

    class DummyChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class DummyResponse:
        def __init__(self, content):
            self.choices = [DummyChoice(content)]
            self.usage = types.SimpleNamespace(prompt_tokens=7, completion_tokens=11)

    captured = {}

    def fake_call_llm(*, provider=None, messages, model):
        captured['messages'] = messages
        captured['model'] = model
        captured['provider'] = provider
        return DummyResponse('expert answer')

    def fake_log_expert_call(**kwargs):
        log_path.write_text(json.dumps(kwargs), encoding='utf-8')
        return kwargs

    monkeypatch.setattr(ask_expert_module, 'log_expert_call', fake_log_expert_call)
    monkeypatch.setattr(ask_expert_module, 'call_llm', fake_call_llm)

    result = ask_expert_module.ask_expert('Design a multi-agent architecture')

    assert result == 'expert answer'
    assert captured['model'] == 'gpt-5.5'
    assert 'Summary: compact' in captured['messages'][0]['content']
    assert calls['registered'] == 1
    assert log_path.exists()
    payload = json.loads(log_path.read_text().strip())
    assert payload['allowed'] is True
    assert payload['request_tokens'] == 7
    assert payload['response_tokens'] == 11


def test_ask_expert_builds_minimal_prompt(monkeypatch):
    monkeypatch.setattr(ask_expert_module, 'should_call_expert', lambda task, context: True)
    monkeypatch.setattr(ask_expert_module, 'can_call_expert', lambda: True)
    monkeypatch.setattr(ask_expert_module, 'register_expert_call', lambda: None)
    monkeypatch.setattr(ask_expert_module, 'log_expert_call', lambda **kwargs: None)

    captured = {}

    class DummyChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class DummyResponse:
        def __init__(self):
            self.choices = [DummyChoice('synthetic expert answer')]
            self.usage = types.SimpleNamespace(prompt_tokens=3, completion_tokens=5)

    def fake_build_expert_context(history, task):
        captured['history'] = list(history)
        captured['task'] = task
        return 'Summary: synthetic\nCurrent task: ' + task

    def fake_call_llm(*, provider=None, messages, model):
        captured['messages'] = messages
        captured['model'] = model
        captured['provider'] = provider
        return DummyResponse()

    monkeypatch.setattr(ask_expert_module, 'build_expert_context', fake_build_expert_context)
    monkeypatch.setattr(ask_expert_module, 'call_llm', fake_call_llm)

    result = ask_expert_module.ask_expert('Design a multi-agent scheduler for bridge routing')

    assert result == 'synthetic expert answer'
    assert captured['model'] == 'gpt-5.5'
    assert captured['history'] == []
    assert captured['task'] == 'Design a multi-agent scheduler for bridge routing'
    assert len(captured['messages']) == 1
    prompt = captured['messages'][0]['content']
    assert prompt.startswith('You are GPT-5.5, an expert reasoning model inside Hermes.')
    assert 'Summary: synthetic' in prompt
    assert 'Current task: Design a multi-agent scheduler for bridge routing' in prompt


def test_registry_dispatch_invokes_ask_expert_handler(monkeypatch):
    from tools.registry import registry

    entry = registry.get_entry('ask_expert')
    assert entry is not None

    captured = {}

    def fake_ask_expert(task):
        captured['task'] = task
        return 'dispatched answer'

    monkeypatch.setattr(ask_expert_module, 'ask_expert', fake_ask_expert)

    result = entry.handler(task='Do expert synthesis on retry strategy')

    assert result == 'dispatched answer'
    assert captured['task'] == 'Do expert synthesis on retry strategy'
