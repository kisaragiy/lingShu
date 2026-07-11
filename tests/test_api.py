"""Tests for API security and auth — pure function tests, no server needed."""

import time

import pytest

from agent_harness.api_security import validate_token, load_or_generate_token, reset_token
from agent_harness.auth_jwt import (
    create_access_token,
    create_refresh_token,
    verify_token,
    refresh_access_token,
    set_secret,
)


@pytest.fixture(autouse=True)
def fixed_secret():
    """Use a deterministic secret so tokens are reproducible within a test."""
    set_secret("test-secret-0123456789abcdef" * 2)  # 64 hex chars
    yield
    set_secret(None)  # Reset after test


class TestValidateToken:
    def test_valid_token(self):
        """Valid token matches stored token via constant-time compare."""
        assert validate_token("abc123", "abc123") is True

    def test_invalid_token(self):
        """Mismatched token returns False."""
        assert validate_token("abc", "xyz") is False

    def test_empty_request_token(self):
        """Empty or None request token returns False."""
        assert validate_token("", "secret") is False
        assert validate_token(None, "secret") is False

    def test_empty_stored_token(self):
        """Empty stored token returns False."""
        assert validate_token("abc", "") is False


class TestJWTToken:
    def test_create_and_verify_access_token(self):
        """Create an access token, then verify it successfully."""
        token = create_access_token("user1", "alice", "admin", expiry_hours=1)
        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user1"
        assert payload["username"] == "alice"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_create_and_verify_refresh_token(self):
        """Create a refresh token, verify with expected_type='refresh'."""
        token = create_refresh_token("user1", "alice", "admin", expiry_days=30)
        payload = verify_token(token, expected_type="refresh")
        assert payload is not None
        assert payload["type"] == "refresh"
        # Access token verification should fail for refresh tokens
        assert verify_token(token, expected_type="access") is None

    def test_token_expiry(self):
        """Expired token returns None."""
        token = create_access_token("user1", "alice", "admin", expiry_hours=0)
        payload = verify_token(token)
        assert payload is None

    def test_tampered_token(self):
        """A tampered token signature causes verification to fail."""
        token = create_access_token("user1", "alice", "admin")
        parts = token.split(".")
        # Tamper with payload
        parts[1] = parts[1][:-1] + "Z"
        tampered = ".".join(parts)
        assert verify_token(tampered) is None

    def test_refresh_flow(self):
        """Refresh token can be exchanged for a new access token."""
        refresh = create_refresh_token("user1", "alice", "admin")
        new_access = refresh_access_token(refresh)
        assert new_access is not None
        payload = verify_token(new_access)
        assert payload is not None
        assert payload["sub"] == "user1"
        assert payload["type"] == "access"

    def test_invalid_refresh_token_returns_none(self):
        """An invalid refresh token returns None from refresh_access_token."""
        assert refresh_access_token("invalid.token.here") is None
