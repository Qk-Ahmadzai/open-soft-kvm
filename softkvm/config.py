"""Configuration model and atomic JSON persistence for SoftKVM.

Every runtime-tunable value lives here so the UI, the API and the background
workers all agree on a single source of truth. The file is written atomically
(temp file + ``os.replace``) so a crash mid-write can never leave a truncated
config behind.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(__file__).resolve().parent

#: Wallpapers shipped with the app, discovered at runtime.
WALLPAPER_DIR = PACKAGE_ROOT / "static" / "img" / "wallpapers"


def default_data_dir() -> Path:
    """Directory that holds ``config.json``, secrets and the audit log."""
    env = os.environ.get("SOFTKVM_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (PROJECT_ROOT / "data").resolve()


@dataclass
class Config:
    """User-facing settings for the whole application."""

    # --- server ---
    host: str = "0.0.0.0"
    port: int = 3699
    debug: bool = False
    use_dev_server: bool = True

    # --- security ---
    require_auth: bool = True
    session_ttl_seconds: int = 60 * 60 * 8
    login_max_attempts: int = 5
    login_lockout_seconds: int = 60

    # --- capture / streaming ---
    monitor_index: int = 1
    stream_width: int = 1280
    stream_height: int = 720
    stream_fps: int = 15
    stream_quality: int = 60
    max_stream_clients: int = 4

    # --- dashboard ---
    poll_interval_ms: int = 2000
    theme: str = "dark"
    wallpaper: str = "night-sky"
    wallpaper_blur: bool = True

    # --- runtime secrets (never rendered to the page) ---
    secret_key: str = field(default_factory=lambda: secrets.token_hex(32))
    password_hash: str = ""
    password_salt: str = field(default_factory=lambda: secrets.token_hex(16))

    # --- scheduled tasks ---
    scheduled_tasks: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    def sanitised(self) -> dict[str, Any]:
        """Return the config without secret material."""
        data = asdict(self)
        for key in ("secret_key", "password_hash", "password_salt"):
            data.pop(key, None)
        return data

    def update(self, patch: dict[str, Any]) -> list[str]:
        """Apply validated field updates. Returns the list of changed keys."""
        changed: list[str] = []
        known = {f.name: f for f in fields(self)}
        for key, value in patch.items():
            spec = known.get(key)
            if spec is None or key in {"secret_key", "password_hash", "password_salt"}:
                continue
            current = getattr(self, key)
            coerced = _coerce(current, value)
            if coerced is None:
                continue
            if coerced != current:
                setattr(self, key, coerced)
                changed.append(key)
        return changed

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        cfg = cls()
        for key, value in clean.items():
            spec = {f.name: f for f in fields(cls)}[key]
            coerced = _coerce(getattr(cfg, key), value)
            if coerced is not None:
                setattr(cfg, key, coerced)
        return cfg


def _coerce(current: Any, value: Any) -> Any:
    """Coerce ``value`` to the type of ``current``; return None when invalid."""
    try:
        if isinstance(current, bool):
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if isinstance(current, int) and not isinstance(current, bool):
            return int(value)
        if isinstance(current, float):
            return float(value)
        if isinstance(current, str):
            return str(value)
        if isinstance(current, list):
            return list(value) if isinstance(value, (list, tuple)) else None
        if isinstance(current, dict):
            return dict(value) if isinstance(value, dict) else None
    except (TypeError, ValueError):
        return None
    return None


class ConfigStore:
    """Thread-safe, atomically persisted :class:`Config`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_data_dir() / "config.json"
        self._lock = threading.RLock()
        self._config = Config()
        self.load()

    # -- io ---------------------------------------------------------
    def load(self) -> Config:
        with self._lock:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    self._config = Config.from_dict(raw)
                except (OSError, ValueError) as exc:  # corrupt file -> keep defaults
                    print(f"[config] could not read {self.path}: {exc}")
                    self._config = Config()
            self._apply_env()
            return self._config

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(self._config.to_json(), encoding="utf-8")
            os.replace(tmp, self.path)

    def _apply_env(self) -> None:
        """Environment variables override file values (12-factor style)."""
        env_map: dict[str, str] = {
            "SOFTKVM_HOST": "host",
            "SOFTKVM_PORT": "port",
            "SOFTKVM_THEME": "theme",
            "SOFTKVM_WALLPAPER": "wallpaper",
            "SOFTKVM_MONITOR": "monitor_index",
            "SOFTKVM_FPS": "stream_fps",
            "SOFTKVM_QUALITY": "stream_quality",
            "SOFTKVM_POLL_MS": "poll_interval_ms",
            "SOFTKVM_REQUIRE_AUTH": "require_auth",
            "SOFTKVM_DEBUG": "debug",
        }
        for env_key, attr in env_map.items():
            raw = os.environ.get(env_key)
            if raw is None:
                continue
            coerced = _coerce(getattr(self._config, attr), raw)
            if coerced is not None:
                setattr(self._config, attr, coerced)

    # -- accessors --------------------------------------------------
    @property
    def current(self) -> Config:
        with self._lock:
            return self._config

    def update(self, patch: dict[str, Any]) -> list[str]:
        with self._lock:
            changed = self._config.update(patch)
            if changed:
                self.save()
            return changed

    def set_password(self, password_hash: str, salt: str) -> None:
        with self._lock:
            self._config.password_hash = password_hash
            self._config.password_salt = salt
            self.save()


def available_wallpapers(directory: Path | None = None) -> list[dict[str, str]]:
    """List bundled wallpapers, newest-looking names first."""
    directory = directory or WALLPAPER_DIR
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.jpg")):
        out.append(
            {
                "id": path.stem,
                "name": path.stem.replace("-", " ").title(),
                "url": f"/static/img/wallpapers/{path.name}",
            }
        )
    return out
