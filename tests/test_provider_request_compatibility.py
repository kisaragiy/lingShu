"""Real local HTTP regressions for provider authentication and thinking controls."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_harness.core import config
from agent_harness.core.pipeline import llm


@pytest.fixture
def provider(monkeypatch):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append((self.headers.get('Authorization'), payload))
            if self.headers.get('Authorization') != 'Bearer rehearsal-test-key':
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({'choices': [{'message': {'content': 'authenticated answer'},
                                           'finish_reason': 'stop'}],
                               'usage': {'total_tokens': 3}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = {'api_url': f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
           'api_key': 'rehearsal-test-key', 'model': 'rehearsal-model', 'backend': 'ollama'}
    monkeypatch.setattr(config, 'get_llm_config', lambda: dict(cfg))
    monkeypatch.setattr(llm, 'get_llm_config', lambda: dict(cfg))
    monkeypatch.setattr(llm, '_llm_cache_key', lambda *a, **k: None)
    monkeypatch.setattr(llm, '_emit_llm_envelope', lambda *a, **k: None)
    monkeypatch.setenv('HARNESS_LLM_THINKING', 'off')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    try:
        yield cfg, seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('caller', ['pipeline', 'supervisor', 'worker', 'graph'])
def test_main_callers_authenticate_and_disable_ollama_thinking(provider, caller):
    _, seen = provider
    messages = [{'role': 'user', 'content': 'hello'}]
    if caller == 'pipeline':
        answer = llm.call_llama(messages)[0]
    elif caller == 'supervisor':
        from agent_harness.core.agents import supervisor
        answer = supervisor._call_llm_full(messages)[0]
    elif caller == 'worker':
        from agent_harness.core.agents import workers
        answer = workers._call_llm(messages)
    else:
        from agent_harness.core.graph import graph
        answer = graph._call_llm(messages)
    assert answer == 'authenticated answer'
    assert len(seen) == 1
    assert seen[0][1]['reasoning_effort'] == 'none'
    assert 'thinking' not in seen[0][1]


def test_auth_key_is_not_sent_to_a_different_endpoint(provider):
    assert llm.runtime_auth_headers('https://unrelated.example/chat/completions') == {}


def test_removing_key_takes_effect_without_restart(provider):
    cfg, _ = provider
    assert 'Authorization' in llm.runtime_auth_headers(cfg['api_url'])
    cfg['api_key'] = ''
    assert llm.runtime_auth_headers(cfg['api_url']) == {}


def test_cloud_thinking_format_and_enabled_mode(provider, monkeypatch):
    cfg, _ = provider
    cfg['backend'] = 'deepseek'
    assert llm.thinking_payload() == {'thinking': {'type': 'disabled'}}
    monkeypatch.setenv('HARNESS_LLM_THINKING', 'on')
    cfg['backend'] = 'ollama'
    assert llm.thinking_payload() == {}
