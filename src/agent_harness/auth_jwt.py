"""JWT token implementation — pure Python stdlib, HMAC-SHA256.

No external dependencies. Uses hmac + hashlib + base64.
Token format: header.payload.signature (standard JWT)
Payload includes: sub (user_id), role, username, exp, iat, jti
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

# ─── Encoding helpers ───

_JWT_SECRET: str | None = None  # Set once at startup


def _b64enc(data: bytes) -> str:
    """URL-safe base64 encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64dec(s: str) -> bytes:
    """URL-safe base64 decode with padding restoration."""
    # Restore padding
    padding = 4 - len(s) % 4
    if padding != 4:
        s += '=' * padding
    return base64.urlsafe_b64decode(s)


def _get_secret() -> str:
    """Get or generate the JWT signing secret."""
    global _JWT_SECRET
    if _JWT_SECRET is None:
        # Store alongside API token
        from .api_security import HARNESS_DIR
        secret_file = HARNESS_DIR / "jwt_secret.txt"
        if secret_file.exists():
            _JWT_SECRET = secret_file.read_text("utf-8").strip()
        if not _JWT_SECRET:
            _JWT_SECRET = secrets.token_hex(32)
            HARNESS_DIR.mkdir(parents=True, exist_ok=True)
            secret_file.write_text(_JWT_SECRET, "utf-8")
    return _JWT_SECRET


def set_secret(secret: str):
    """Override the JWT secret (for testing)."""
    global _JWT_SECRET
    _JWT_SECRET = secret


# ─── Token creation ───

def create_access_token(user_id: str, username: str, role: str,
                        expiry_hours: int = 8) -> str:
    """Create a JWT access token.

    Args:
        user_id: User's unique ID
        username: Username
        role: User role (admin/user)
        expiry_hours: Token lifetime (default 8h, so a work day works)

    Returns:
        Encoded JWT string
    """
    secret = _get_secret()
    now = int(time.time())
    jti = secrets.token_hex(16)

    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "jti": jti,
        "iat": now,
        "exp": now + expiry_hours * 3600,
        "type": "access",
    }

    return _encode(payload, secret)


def create_refresh_token(user_id: str, username: str, role: str,
                         expiry_days: int = 30) -> str:
    """Create a JWT refresh token (long-lived, used only for refreshing access)."""
    secret = _get_secret()
    now = int(time.time())
    jti = secrets.token_hex(16)

    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "jti": jti,
        "iat": now,
        "exp": now + expiry_days * 86400,
        "type": "refresh",
    }

    return _encode(payload, secret)


def _encode(payload: dict, secret: str) -> str:
    """Encode a JWT token."""
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64enc(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64enc(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    message = f"{header_b64}.{payload_b64}"
    sig = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = _b64enc(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


# ─── Token verification ───

def verify_token(token: str, expected_type: str = "access") -> dict | None:
    """Verify a JWT token and return its payload.

    Args:
        token: The JWT string
        expected_type: 'access' or 'refresh'

    Returns:
        Decoded payload dict, or None if invalid/expired
    """
    secret = _get_secret()

    # Split token
    parts = token.split(".")
    if len(parts) != 3:
        return None

    header_b64, payload_b64, sig_b64 = parts

    # Verify signature
    message = f"{header_b64}.{payload_b64}"
    expected_sig = hmac.new(secret.encode("utf-8"), message.encode("utf-8"),
                            hashlib.sha256).digest()
    actual_sig = _b64dec(sig_b64)

    # Constant-time comparison
    if not hmac.compare_digest(expected_sig, actual_sig):
        return None

    # Decode payload
    try:
        payload = json.loads(_b64dec(payload_b64))
    except (json.JSONDecodeError, ValueError):
        return None

    # Check expiry
    now = time.time()
    if payload.get("exp", 0) < now:
        return None

    # Check type
    if payload.get("type") != expected_type:
        return None

    return payload


# ─── Token refresh flow ───

def refresh_access_token(refresh_token: str) -> str | None:
    """Exchange a valid refresh token for a new access token.

    Returns new access token, or None if refresh token is invalid.
    """
    payload = verify_token(refresh_token, expected_type="refresh")
    if payload is None:
        return None

    return create_access_token(
        user_id=payload["sub"],
        username=payload["username"],
        role=payload["role"],
    )
