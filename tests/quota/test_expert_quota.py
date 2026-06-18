from quota import expert_quota


def test_register_and_allow_under_limit(monkeypatch):
    monkeypatch.setattr(expert_quota, '_today_key', lambda: '2026-06-05')
    expert_quota.reset_quota_state()

    assert expert_quota.can_call_expert() is True
    expert_quota.register_expert_call()
    assert expert_quota.can_call_expert() is True


def test_blocks_after_limit(monkeypatch):
    monkeypatch.setattr(expert_quota, '_today_key', lambda: '2026-06-05')
    expert_quota.reset_quota_state()

    for _ in range(expert_quota.MAX_EXPERT_CALLS_PER_DAY):
        expert_quota.register_expert_call()

    assert expert_quota.can_call_expert() is False
    status = expert_quota.quota_status()
    assert status['allowed'] is False
    assert status['used'] == expert_quota.MAX_EXPERT_CALLS_PER_DAY


def test_reset_quota_state(monkeypatch):
    monkeypatch.setattr(expert_quota, '_today_key', lambda: '2026-06-05')
    expert_quota.reset_quota_state()
    expert_quota.register_expert_call()
    expert_quota.reset_quota_state()
    assert expert_quota.can_call_expert() is True
