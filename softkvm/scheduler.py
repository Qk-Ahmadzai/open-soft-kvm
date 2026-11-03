"""Scheduled host actions.

Tasks are stored in the config file, so they survive a restart. Each task has
an ``at`` time (``HH:MM``), a ``daily`` flag and a ``last_run`` stamp; the
scheduler thread fires anything that is due and has not already run today.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Callable

#: Actions a task may perform.
TASK_ACTIONS = {
    "reboot": "Reboot the host",
    "shutdown": "Shut the host down",
    "lock": "Lock the workstation",
    "sleep_display": "Send the display to standby",
    "suspend": "Suspend the host",
    "wake_on_lan": "Send a Wake-on-LAN packet",
}


class Scheduler:
    """Fires scheduled tasks at their configured local time."""

    def __init__(self, config_store, system_controller, audit, poll_seconds: float = 20.0) -> None:
        self._config_store = config_store
        self._system = system_controller
        self._audit = audit
        self.poll_seconds = poll_seconds
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- lifecycle --------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="softkvm-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:  # pragma: no cover - timing dependent
        while not self._stop.wait(self.poll_seconds):
            try:
                self.tick()
            except Exception:
                pass

    # -- logic ------------------------------------------------------
    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Run any due task; returns the tasks that fired."""
        now = now or datetime.now()
        today = now.date().isoformat()
        current = now.strftime("%H:%M")
        fired: list[dict[str, Any]] = []

        tasks = self._config_store.current.scheduled_tasks
        for task in list(tasks):
            if not task.get("enabled", True):
                continue
            if task.get("last_run") == today:
                continue
            at = str(task.get("at", ""))
            if not at or not self._is_due(at, current):
                continue
            if not task.get("daily", True) and task.get("date") != today:
                continue

            result = self._execute(task)
            task["last_run"] = today
            task["last_result"] = result.get("message", "")
            fired.append({"task": task.get("label") or task.get("action"), "result": result})
            self._audit.record(
                f"scheduled:{task.get('action')}",
                actor="scheduler",
                detail=task.get("label"),
                ok=bool(result.get("ok")),
                category="schedule",
            )

        if fired:
            self._config_store.update({"scheduled_tasks": tasks})
        return fired

    @staticmethod
    def _is_due(at: str, current: str) -> bool:
        """True when the scheduled minute has arrived (within a 2 min window)."""
        try:
            target_hour, target_minute = (int(part) for part in at.split(":"))
            now_hour, now_minute = (int(part) for part in current.split(":"))
        except (ValueError, AttributeError):
            return False
        target = target_hour * 60 + target_minute
        now = now_hour * 60 + now_minute
        return 0 <= (now - target) < 2

    def _execute(self, task: dict[str, Any]) -> dict[str, Any]:
        action = str(task.get("action", ""))
        try:
            if action == "reboot":
                return self._system.reboot()
            if action == "shutdown":
                return self._system.shutdown()
            if action == "lock":
                return self._system.lock()
            if action == "sleep_display":
                return self._system.sleep_display()
            if action == "suspend":
                return self._system.suspend()
            if action == "wake_on_lan":
                return self._system.wake_on_lan(str(task.get("target", "")))
        except Exception as exc:
            return {"ok": False, "message": f"{action} failed: {exc}"}
        return {"ok": False, "message": f"unknown action '{action}'"}


# ---------------------------------------------------------------------------
# task CRUD
# ---------------------------------------------------------------------------
def list_tasks(config_store) -> list[dict[str, Any]]:
    return list(config_store.current.scheduled_tasks)


def add_task(config_store, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and append a scheduled task."""
    action = str(payload.get("action", ""))
    if action not in TASK_ACTIONS:
        raise ValueError(f"unknown action '{action}'")

    at = str(payload.get("at", ""))
    try:
        hour, minute = (int(part) for part in at.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("time must be formatted as HH:MM")

    task = {
        "id": f"task-{int(datetime.now().timestamp() * 1000) % 10_000_000}",
        "action": action,
        "label": str(payload.get("label") or TASK_ACTIONS[action]),
        "at": f"{hour:02d}:{minute:02d}",
        "daily": bool(payload.get("daily", True)),
        "enabled": bool(payload.get("enabled", True)),
        "target": str(payload.get("target", "")),
        "last_run": None,
        "last_result": "",
    }
    if action == "wake_on_lan" and not task["target"]:
        raise ValueError("Wake-on-LAN tasks need a target MAC address")

    tasks = config_store.current.scheduled_tasks
    tasks.append(task)
    config_store.update({"scheduled_tasks": tasks})
    return task


def remove_task(config_store, task_id: str) -> bool:
    tasks = config_store.current.scheduled_tasks
    remaining = [task for task in tasks if task.get("id") != task_id]
    if len(remaining) == len(tasks):
        return False
    config_store.update({"scheduled_tasks": remaining})
    return True
