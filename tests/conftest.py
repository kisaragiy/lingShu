"""Shared fixtures for LingShu Agent test suite."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

# Ensure the src package is importable
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def mock_response():
    """Build a simple mock HTTP response object with status_code, text, and json()."""

    class MockResponse:
        def __init__(self, status_code=200, text="", json_data=None):
            self.status_code = status_code
            self.text = text
            self._json = json_data or {}

        def json(self):
            return self._json

    return MockResponse


@pytest.fixture
def mock_session(mock_response, monkeypatch):
    """Fixture that patches requests.Session with a controlled mock."""
    import requests

    class MockSession:
        def __init__(self):
            self.responses = {}
            self.last_url = None
            self.last_params = None
            self.last_headers = None
            self.last_json = None

        def get(self, url, **kwargs):
            self.last_url = url
            self.last_params = kwargs.get("params")
            self.last_headers = kwargs.get("headers", {})
            resp = self.responses.get(url) or mock_response()
            return resp

        def post(self, url, **kwargs):
            self.last_url = url
            self.last_json = kwargs.get("json")
            self.last_headers = kwargs.get("headers", {})
            resp = self.responses.get(url) or mock_response()
            return resp

    fake_session = MockSession()
    monkeypatch.setattr("agent_harness.core.pipeline.llm._session", fake_session)
    # Also patch the session used by web.py which imports it from llm
    from agent_harness.core.tools import web as web_module
    monkeypatch.setattr(web_module, "_session", fake_session)

    return fake_session


@pytest.fixture
def test_config():
    """Provide standard test configuration values."""
    return {
        "MODEL_LLAMA": "test-model",
        "LLAMA_API": "http://test.local:8081/v1/chat/completions",
        "HARNESS_DIR": Path(__file__).resolve().parent.parent / "src" / "agent_harness",
    }
