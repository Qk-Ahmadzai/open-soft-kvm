"""Authentication: password hashing, signed sessions and login throttling.

Design notes
------------
* Passwords are stored as PBKDF2-HMAC-SHA256 (stdlib only - no extra runtime
  dependency) with a per-install random salt and 200k iterations.
* Sessions are stateless: a signed, timestamped cookie. No server-side session
  table means no cleanup job and no memory growth.
* Failed logins are throttled per client address with exponential backoff.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from flask import Flask, g, jsonify, redirect, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

PBKDF2_ROUNDS = 200_000
SESSION_COOKIE = "softkvm_session"


# ---------------------------------------------------------------------------
# password hashing
# ---------------------------------------------------------------------------
def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return ``(hash_hex, salt_hex)`` for the given plaintext password."""
    salt_hex = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ROUNDS
    )
    return derived.hex(), salt_hex


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Constant-time verification of a plaintext password against storage."""
    if not password_hash or not salt:
        return False
    try:
        candidate, _ = hash_password(password, salt)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, password_hash)


def generate_password(length: int = 12) -> str:
    """Human-friendly random password (no ambiguous characters)."""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------------------------------------------------------------------------
# login throttling
# ---------------------------------------------------------------------------
@dataclass
class _Attempt:
    failures: int = 0
    locked_until: float = 0.0


class LoginThrottle:
    """Per-address failure tracking with exponential lockout."""

    def __init__(self, max_attempts: int = 5, base_lockout: int = 60) -> None:
        self.max_attempts = max_attempts
        self.base_lockout = base_lockout
        self._attempts: dict[str, _Attempt] = {}
        self._lock = threading.Lock()

    def remaining_lockout(self, address: str) -> int:
        """Seconds left on the lockout for ``address`` (0 when unlocked)."""
        with self._lock:
            entry = self._attempts.get(address)
            if not entry:
                return 0
            remaining = entry.locked_until - time.time()
            return int(remaining) + 1 if remaining > 0 else 0

    def record_failure(self, address: str) -> int:
        """Register a failed attempt; returns the resulting lockout seconds."""
        with self._lock:
            entry = self._attempts.setdefault(address, _Attempt())
            entry.failures += 1
            if entry.failures >= self.max_attempts:
                # 1x, 2x, 4x ... capped at 15 minutes
                exponent = min(entry.failures - self.max_attempts, 4)
                entry.locked_until = time.time() + self.base_lockout * (2**exponent)
            return max(0, int(entry.locked_until - time.time()))

    def record_success(self, address: str) -> None:
        with self._lock:
            self._attempts.pop(address, None)

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()


# ---------------------------------------------------------------------------
# session handling
# ---------------------------------------------------------------------------
class SessionManager:
    """Stateless signed-cookie sessions."""

    def __init__(self, secret_key: str, ttl_seconds: int = 8 * 3600) -> None:
        self.serializer = URLSafeTimedSerializer(secret_key, salt="softkvm.session")
        self.ttl_seconds = ttl_seconds

    def issue(self, username: str = "operator") -> str:
        return self.serializer.dumps({"u": username, "n": secrets.token_urlsafe(8)})

    def read(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        try:
            return self.serializer.loads(token, max_age=self.ttl_seconds)
        except (BadSignature, SignatureExpired):
            return None


def client_address() -> str:
    """Best-effort client address, honouring one proxy hop."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ---------------------------------------------------------------------------
# Flask integration
# ---------------------------------------------------------------------------
def init_auth(app: Flask, config_store) -> None:
    """Wire session manager, throttle and the ``login_required`` decorator."""
    cfg = config_store.current
    sessions = SessionManager(cfg.secret_key, cfg.session_ttl_seconds)
    throttle = LoginThrottle(cfg.login_max_attempts, cfg.login_lockout_seconds)

    app.extensions["softkvm_sessions"] = sessions
    app.extensions["softkvm_throttle"] = throttle

    def current_user() -> dict[str, Any] | None:
        """Return the signed-in user payload, or ``None``."""
        if not config_store.current.require_auth:
            return {"u": "operator", "local": True}
        token = request.cookies.get(SESSION_COOKIE)
        payload = sessions.read(token)
        return payload if payload else None

    def login_required(view):
        """Guard a view: HTML routes redirect, API routes return 401 JSON."""

        def wrapper(*args, **kwargs):
            if not config_store.current.require_auth:
                return view(*args, **kwargs)
            if current_user() is None:
                if request.path.startswith("/api/") or request.path in {"/stream", "/snapshot"}:
                    return jsonify({"error": "authentication required"}), 401
                return redirect(url_for("pages.login", next=request.full_path))
            return view(*args, **kwargs)

        wrapper.__name__ = view.__name__
        wrapper.__doc__ = view.__doc__
        return wrapper

    app.extensions["softkvm_current_user"] = current_user
    app.extensions["softkvm_login_required"] = login_required
