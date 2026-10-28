"""Exercise the real CLI startup gate with isolated persisted configuration."""
import json
from unittest.mock import Mock

import pytest


@pytest.fixture
def startup(monkeypatch, tmp_path):
    from agent_harness import auto_setup, main, plugin_loader
    from agent_harness.core import agent_cron, config
    from agent_harness.core.pipeline import config_manager, session_store
    import uvicorn

    path = tmp_path / 'config.json'
    monkeypatch.setattr(config_manager, 'CONFIG_PATH', path)
    monkeypatch.setattr(config, 'DISABLE_AUTH', False)
    for name in ('LLAMA_API', 'DEEPSEEK_API', 'CLOUD_API_DIRECT'):
        monkeypatch.setattr(config, name, '')
    monkeypatch.setattr(config, '_settings', config.Settings(
        _env_file=None, llama_api='', deepseek_api='', cloud_api=''))
    monkeypatch.setattr(session_store, 'init_store', lambda: None)
    monkeypatch.setattr(session_store, 'session_count', lambda: 0)
    for name, value in [('_get_db', None), ('needs_initial_admin', False),
                        ('user_count', 1), ('cleanup_expired_sessions', None)]:
        monkeypatch.setattr(main._auth_db, name, Mock(return_value=value))
    scheduler = Mock()
    scheduler.list_tasks.return_value = []
    monkeypatch.setattr(agent_cron, 'get_scheduler', lambda: scheduler)
    monkeypatch.setattr(plugin_loader, 'load_plugins', lambda: [])
    discovery = Mock(return_value=None)
    runner = Mock()
    monkeypatch.setattr(auto_setup, 'auto_setup', discovery)
    monkeypatch.setattr(uvicorn, 'run', runner)
    return main.main, path, config, discovery, runner


def test_authenticated_startup_accepts_saved_config_without_discovery(startup):
    run, path, _, discovery, runner = startup
    path.write_text(json.dumps({'llm': {
        'api_url': 'http://127.0.0.1:19999/v1', 'model': 'saved-model'}}), encoding='utf-8')
    run()
    discovery.assert_not_called()
    runner.assert_called_once()


def test_authenticated_startup_rereads_config_after_auto_setup(startup):
    run, path, _, discovery, runner = startup

    def discover():
        path.write_text(json.dumps({'llm': {
            'api_url': 'http://127.0.0.1:19999/v1', 'model': 'discovered-model'}}), encoding='utf-8')

    discovery.side_effect = discover
    run()
    discovery.assert_called_once()
    runner.assert_called_once()


@pytest.mark.parametrize('content', [None, '{invalid json', '{"llm": {}}'])
def test_authenticated_startup_without_a_backend_still_fails_closed(startup, content):
    run, path, _, discovery, runner = startup
    if content is not None:
        path.write_text(content, encoding='utf-8')
    with pytest.raises(SystemExit) as exc:
        run()
    assert exc.value.code == 1
    discovery.assert_called_once()
    runner.assert_not_called()


def test_cloud_environment_endpoint_is_used_by_startup_and_requests(startup, monkeypatch):
    run, _, config, discovery, runner = startup
    monkeypatch.setattr(config, 'CLOUD_API_DIRECT', 'https://cloud.example/v1')
    run()
    assert config.get_llm_config()['api_url'] == 'https://cloud.example/v1'
    discovery.assert_not_called()
    runner.assert_called_once()


def test_empty_demo_configuration_keeps_template_startup_available(startup, monkeypatch):
    run, _, config, discovery, runner = startup
    monkeypatch.setattr(config, 'DISABLE_AUTH', True)
    run()
    discovery.assert_not_called()
    runner.assert_called_once()
