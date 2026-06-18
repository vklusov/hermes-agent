from context.expert_context import build_expert_context


def test_build_expert_context_is_compact():
    history = [
        {"role": "user", "content": "first message"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second message"},
    ]
    result = build_expert_context(history, "Design a resilient queue system")

    assert result.startswith("Summary:")
    assert "Current task:" in result
    assert "first message" in result or "second message" in result
    assert "assistant" not in result.lower()


def test_build_expert_context_does_not_echo_full_history():
    history = [{"role": "user", "content": f"line {i}"} for i in range(50)]
    result = build_expert_context(history, "Complex refactor")

    assert len(result) < 2000
    assert result.count("line ") < 20


def test_build_expert_context_handles_long_task_and_list_content():
    history = [
        {"role": "user", "content": [{"text": "first chunk"}, {"text": "second chunk"}]},
        {"role": "assistant", "content": [{"text": "assistant chunk"}]},
    ]
    long_task = "x" * 2000
    result = build_expert_context(history, long_task)

    assert result.startswith("Summary:")
    assert "first chunk" in result
    assert "second chunk" in result
    assert "assistant chunk" in result
    assert len(result) < 2200
