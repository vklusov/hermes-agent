from pathlib import Path


def test_gateway_startup_activates_native_auto_skills_before_platform_connect():
    source = Path('gateway/run.py').read_text(encoding='utf-8')

    startup_idx = source.index('logger.info("Starting Hermes Gateway...")')
    activation_idx = source.index('_activate_gateway_auto_skills(_startup_auto_skills)')
    connect_idx = source.index('logger.info("Connecting to %s...", platform.value)')

    assert startup_idx < activation_idx < connect_idx
    assert 'load_skill_auto_preload()' in source[startup_idx:activation_idx]
    activation_block = source[startup_idx:connect_idx].lower()
    assert 'routine_worker must be registered before tool schemas' in activation_block
    assert "bridge bootstrap context" in activation_block
