"""Audit trail for privileged actions.

Every control-plane action (power, process kill, KVM input, settings change)
is recorded with the acting client address so the operator can answer
"who pressed that?". Records live in a bounded in-memory ring for the UI and
are mirrored to an append-only JSONL file for durability.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_EVENTS = 400


class AuditLog:
    """Bounded, thread-safe action journal."""

    def __init__(self, path: Path | None = None, max_events: int = MAX_EVENTS) -> None:
        self.path = Path(path) if path else None
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def record(
        self,
        action: str,
        actor: str = "-",
        detail: Any = None,
        ok: bool = True,
        category: str = "control",
    ) -> dict[str, Any]:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": action,
            "actor": actor,
            "category": category,
            "ok": bool(ok),
            "detail": detail,
        }
        with self._lock:
            self._events.appendleft(event)
        self._persist(event)
        return event

    def recent(self, limit: int = 60, category: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        if category:
            events = [event for event in events if event["category"] == category]
        return events[: max(1, min(int(limit), MAX_EVENTS))]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def _persist(self, event: dict[str, Any]) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass  # auditing must never break the request it is recording
