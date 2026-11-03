"""Host control, scheduler and capture-backend tests."""

from __future__ import annotations

import socket
from datetime import datetime, timedelta

import pytest

from softkvm.capture import resolve_monitor
from softkvm.scheduler import Scheduler, add_task, list_tasks, remove_task
from softkvm.systemctl import SystemController


# ---------------------------------------------------------------------------
# system control
# ---------------------------------------------------------------------------
def test_kill_protects_critical_pids():
    controller = SystemController()
    for pid in (0, 1, 4):
        with pytest.raises(PermissionError):
            controller.kill_process(pid)


def test_kill_reports_missing_process():
    controller = SystemController()
    result = controller.kill_process(999_999_9)
    assert result["ok"] is False
    assert "No process" in result["message"]


def test_wake_on_lan_rejects_bad_mac():
    controller = SystemController()
    with pytest.raises(ValueError):
        controller.wake_on_lan("not-a-mac")
    with pytest.raises(ValueError):
        controller.wake_on_lan("AA:BB")


def test_wake_on_lan_accepts_formatted_mac():
    controller = SystemController()
    result = controller.wake_on_lan("AA:BB:CC:DD:EE:FF")
    assert result["ok"] is True
    assert "AA:BB:CC:DD:EE:FF" in result["message"]


def test_wifi_rejects_invalid_state():
    controller = SystemController()
    with pytest.raises(ValueError):
        controller.wifi("sideways")


def test_launch_rejects_unknown_app():
    controller = SystemController()
    with pytest.raises(ValueError):
        controller.launch("not-an-app")


def test_available_applications_is_a_list():
    controller = SystemController()
    apps = controller.available_applications()
    assert isinstance(apps, list)
    for app in apps:
        assert {"id", "label", "icon"} <= set(app)


def test_hostname_is_a_string():
    assert isinstance(SystemController().hostname(), str)


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------
def test_add_task_validates_action_and_time(config_store):
    with pytest.raises(ValueError):
        add_task(config_store, {"action": "explode", "at": "10:00"})
    with pytest.raises(ValueError):
        add_task(config_store, {"action": "reboot", "at": "99:99"})
    with pytest.raises(ValueError):
        add_task(config_store, {"action": "wake_on_lan", "at": "10:00"})


def test_add_and_remove_task(config_store):
    task = add_task(config_store, {"action": "lock", "at": "07:15", "label": "Morning lock"})
    assert task["at"] == "07:15"
    assert len(list_tasks(config_store)) == 1
    assert remove_task(config_store, task["id"]) is True
    assert list_tasks(config_store) == []
    assert remove_task(config_store, task["id"]) is False


def test_scheduler_fires_due_task_once(config_store):
    task = add_task(config_store, {"action": "lock", "at": "07:15", "daily": True})
    now = datetime(2026, 1, 1, 7, 15, 30)
    scheduler = Scheduler(config_store, SystemController(), _NullAudit())

    fired = scheduler.tick(now=now)
    assert len(fired) == 1
    assert fired[0]["task"] == task["label"]
    assert config_store.current.scheduled_tasks[0]["last_run"] == "2026-01-01"

    # A second tick on the same day must not fire it again.
    assert scheduler.tick(now=now) == []


def test_scheduler_ignores_tasks_that_are_not_due(config_store):
    add_task(config_store, {"action": "lock", "at": "07:15"})
    scheduler = Scheduler(config_store, SystemController(), _NullAudit())
    assert scheduler.tick(now=datetime(2026, 1, 1, 6, 0, 0)) == []


def test_scheduler_skips_disabled_tasks(config_store):
    task = add_task(config_store, {"action": "lock", "at": "07:15", "enabled": False})
    scheduler = Scheduler(config_store, SystemController(), _NullAudit())
    assert scheduler.tick(now=datetime(2026, 1, 1, 7, 15, 10)) == []
    assert config_store.current.scheduled_tasks[0]["last_run"] is None


class _NullAudit:
    def record(self, *args, **kwargs):
        return {}


# ---------------------------------------------------------------------------
# capture backend
# ---------------------------------------------------------------------------
def test_resolve_monitor_returns_valid_geometry():
    monitor = resolve_monitor(1)
    assert monitor.width > 0
    assert monitor.height > 0
    assert monitor.label.startswith("Display")


def test_resolve_monitor_falls_back_when_index_missing():
    monitor = resolve_monitor(99)
    assert monitor.width > 0
