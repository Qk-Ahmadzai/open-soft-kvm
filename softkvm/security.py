"""HTTP hardening: security headers, CSP and a token-bucket rate limiter."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable

from flask import Flask, Response, current_app, jsonify, request

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
    "X-XSS-Protection": "0",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "media-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'; "
    "worker-src 'self' blob:; "
    "manifest-src 'self'"
)


def apply_security_headers(response: Response) -> Response:
    """Attach the standard hardening headers to every response."""
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if request.path.startswith("/static/"):
        # Immutable-ish caching for wallpapers; the dev server may still change them.
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
    else:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


class RateLimiter:
    """Sliding-window limiter keyed by (route bucket, client address)."""

    def __init__(self, limits: dict[str, tuple[int, float]]) -> None:
        # bucket -> (max events, window seconds)
        self.limits = limits
        self._events: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, bucket: str, address: str) -> bool:
        max_events, window = self.limits.get(bucket, (120, 60.0))
        now = time.time()
        key = (bucket, address)
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and now - events[0] > window:
                events.popleft()
            if len(events) >= max_events:
                return False
            events.append(now)
            if len(self._events) > 4096:  # crude bound on memory
                self._prune(now, window)
            return True

    def _prune(self, now: float, window: float) -> None:
        stale = [k for k, v in self._events.items() if not v or now - v[-1] > window]
        for key in stale:
            self._events.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


def limit(bucket: str) -> Callable:
    """Decorator factory: reject with 429 when the bucket is exhausted."""

    def decorator(view):
        def wrapper(*args, **kwargs):
            limiter: RateLimiter | None = current_app.extensions.get("softkvm_limiter")
            if limiter is not None:
                address = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                address = address or (request.remote_addr or "unknown")
                if not limiter.allow(bucket, address):
                    resp = jsonify({"error": "rate limit exceeded", "bucket": bucket})
                    resp.status_code = 429
                    resp.headers["Retry-After"] = "5"
                    return resp
            return view(*args, **kwargs)

        wrapper.__name__ = view.__name__
        wrapper.__doc__ = view.__doc__
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# payload validation helpers
# ---------------------------------------------------------------------------
class ValidationError(ValueError):
    """Raised when a client payload fails validation."""


def require_json(request) -> dict[str, Any]:
    """Parse and sanity-check a JSON request body."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")
    return data


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def as_int(value: Any, low: int, high: int, default: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        if default is None:
            raise ValidationError(f"expected an integer, got {value!r}")
        return default
    if number < low or number > high:
        raise ValidationError(f"value {number} out of range [{low}, {high}]")
    return number


def as_float(value: Any, low: float, high: float, default: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        if default is None:
            raise ValidationError(f"expected a number, got {value!r}")
        return default
    if number < low or number > high:
        raise ValidationError(f"value {number} out of range [{low}, {high}]")
    return number


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def as_str(value: Any, max_length: int = 256) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) > max_length:
        raise ValidationError(f"text longer than {max_length} characters")
    return text


def init_security(app: Flask) -> RateLimiter:
    """Install headers and return the shared rate limiter."""
    limiter = RateLimiter(
        {
            "login": (10, 300.0),
            "input": (600, 60.0),
            "action": (60, 60.0),
            "api": (600, 60.0),
            "stream": (20, 60.0),
        }
    )
    app.extensions["softkvm_limiter"] = limiter

    @app.after_request
    def _headers(response: Response) -> Response:
        return apply_security_headers(response)

    return limiter
