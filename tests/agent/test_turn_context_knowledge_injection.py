from __future__ import annotations

from types import SimpleNamespace

from agent.memory_manager import MemoryManager
from agent.prefetch_knowledge import SilverbulletKnowledgeProvider
from agent.turn_context import build_turn_context


def test_build_turn_context_injects_ext_prefetch_cache(tmp_path):
    vault = tmp_path / "knowledge"
    section = vault / "manual"
    section.mkdir(parents=True)
    (section / "kb-100.md").write_text(
        """---
entry_id: kb-100
category: manual
status: active
source: silverbullet://knowledge/manual/kb-100.md
confidence: 0.95
---
Headroom and routerai are documented in the KB.
""",
        encoding="utf-8",
    )

    mm = MemoryManager()
    mm.add_provider(SilverbulletKnowledgeProvider(vault_root=str(vault)))

    class DummyGuardrails:
        def reset_for_turn(self):
            pass

    class DummyTodoStore:
        def has_items(self):
            return False

    class DummyInterruptState:
        def _set_interrupt(self, *args, **kwargs):
            pass

    class DummyScrubber:
        def reset(self):
            pass

    class DummyAgent:
        def __init__(self):
            self.session_id = "sess-knowledge"
            self.platform = "cli"
            self.provider = "routerai"
            self.model = "deepseek/deepseek-v4-flash"
            self.base_url = "http://127.0.0.1:8788/v1"
            self.api_key = ""
            self.api_mode = "chat_completions"
            self.max_iterations = 1
            self.compression_enabled = False
            self._memory_manager = mm
            self._ensure_db_session = lambda: None
            self._restore_primary_runtime = lambda: None
            self._cleanup_dead_connections = lambda: False
            self._compression_warning = None
            self._replay_compression_warning = lambda: None
            self._tool_guardrails = DummyGuardrails()
            self._emit_status = lambda *a, **k: None
            self._todo_store = DummyTodoStore()
            self._hydrate_todo_store = lambda *a, **k: None
            self._tool_guardrail_halt_decision = None
            self.iteration_budget = None
            self._user_turn_count = 0
            self._memory_nudge_interval = 0
            self._turns_since_memory = 0
            self._iters_since_skill = 0
            self.quiet_mode = False
            self.valid_tool_names = set()
            self._memory_store = None
            self._safe_print = lambda *a, **k: None
            self._cached_system_prompt = None
            self._memory_write_origin = "assistant_tool"
            self._stream_context_scrubber = DummyScrubber()
            self._stream_think_scrubber = DummyScrubber()

        def __getattr__(self, name):
            if name in {"_cached_system_prompt", "_compression_warning", "_memory_store"}:
                return None
            if name in {"quiet_mode"}:
                return False
            if name in {"valid_tool_names"}:
                return set()
            if name in {"_safe_print", "_ensure_db_session", "_restore_primary_runtime", "_cleanup_dead_connections", "_replay_compression_warning", "_emit_status", "_hydrate_todo_store"}:
                return lambda *a, **k: None
            return lambda *a, **k: None

    agent = DummyAgent()

    context = build_turn_context(
        agent=agent,
        user_message="headroom routerai",
        system_message=None,
        conversation_history=[],
        task_id=None,
        stream_callback=None,
        persist_user_message=None,
        restore_or_build_system_prompt=lambda *a, **k: None,
        install_safe_stdio=lambda: None,
        sanitize_surrogates=lambda s: s,
        summarize_user_message_for_log=lambda s: s,
        set_session_context=lambda *a, **k: None,
        set_current_write_origin=lambda *a, **k: None,
        ra=lambda: DummyInterruptState(),
    )

    assert context.ext_prefetch_cache
    assert "<knowledge-context>" in context.ext_prefetch_cache
    assert "Headroom and routerai" in context.ext_prefetch_cache
