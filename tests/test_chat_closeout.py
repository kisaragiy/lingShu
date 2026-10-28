"""Chat write authorization and async responsiveness regression tests."""
import asyncio
import threading
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_harness.apps.research import api
from agent_harness.core import config
from agent_harness.core.pipeline import session_store as ss


@pytest.fixture
def chat(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, 'SESSION_DIR', str(tmp_path))
    monkeypatch.setattr(config, 'LLAMA_API', 'http://fake.invalid')
    monkeypatch.setattr(api, '_agent_semaphore', threading.Semaphore(2))
    calls = []
    monkeypatch.setattr(api, '_execute_single', lambda *a: calls.append(a) or 'synthetic reply')
    ss.save_session('victim', [{'role': 'user', 'content': 'private history'}], owner_id='owner')
    return calls


def client(uid, role):
    app = FastAPI()
    @app.middleware('http')
    async def identity(request: Request, call_next):
        if uid:
            request.state.user = {'id': uid, 'role': role}
        return await call_next(request)
    app.include_router(api.router)
    return TestClient(app)


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('uid,role,status', [('owner','user',200), ('other','user',404), ('admin','admin',200), ('','user',401)])
def test_chat_write_authorization(chat, stream, uid, role, status):
    r = client(uid, role).post('/v1/chat/completions', headers={'X-Session-Id':'victim'}, json={'model':'agent-harness-single','stream':stream,'messages':[{'role':'user','content':'new turn'}]})
    assert r.status_code == status, r.text
    assert ss.get_session_summary('victim')['owner_id'] == 'owner'
    if status != 200:
        assert not chat
        assert len(ss.load_session('victim')) == 1
        assert 'private history' not in r.text


def test_new_session_claim_and_invalid_id(chat):
    c = client('new-owner', 'user')
    payload = {'model':'agent-harness-single','messages':[{'role':'user','content':'hello'}]}
    assert c.post('/v1/chat/completions', headers={'X-Session-Id':'new-session'},json=payload).status_code == 200
    assert ss.get_session_summary('new-session')['owner_id'] == 'new-owner'
    assert c.post('/v1/chat/completions', headers={'X-Session-Id':'../victim'},json=payload).status_code == 400


def test_claim_and_storage_do_not_transfer_ownership(chat):
    assert ss.claim_session('reserved', 'first') == 'first'
    with pytest.raises(PermissionError):
        ss.claim_session('reserved', 'second')
    with pytest.raises(PermissionError):
        ss.save_session('victim', [{'role':'user','content':'overwrite'}], owner_id='other')
    assert ss.load_session('victim')[0]['content'] == 'private history'


def test_waiting_for_slot_keeps_loop_responsive(monkeypatch):
    monkeypatch.setattr(api, '_agent_semaphore', threading.Semaphore(0))
    monkeypatch.setattr(api, '_SEMAPHORE_TIMEOUT_S', .2)
    async def run():
        start = time.monotonic()
        async def pulse():
            await asyncio.sleep(.01)
            return time.monotonic() - start
        available, elapsed = await asyncio.gather(api._acquire_agent_slot(), pulse())
        assert not available
        assert elapsed < .1
        waiter = asyncio.create_task(api._acquire_agent_slot())
        await asyncio.sleep(.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        api._agent_semaphore.release()
        await asyncio.sleep(.06)
        assert api._agent_semaphore.acquire(blocking=False)
    asyncio.run(run())


def test_silent_stream_keeps_loop_responsive(monkeypatch):
    def worker(prompt, history, model, q, *args, **kwargs):
        time.sleep(.2)
        q.put({'type':'done'})
    monkeypatch.setattr(api, '_run_with_queue', worker)
    monkeypatch.setattr(api, '_save_session', lambda *a, **k: None)
    monkeypatch.setattr(api, '_load_session', lambda *a: [])
    async def run():
        gen = api._stream_progress('hello','','agent-harness-single','synthetic')
        await anext(gen)
        start = time.monotonic()
        async def pulse():
            await asyncio.sleep(.01)
            return time.monotonic() - start
        _, elapsed = await asyncio.gather(anext(gen), pulse())
        assert elapsed < .1
        await gen.aclose()
    asyncio.run(run())
