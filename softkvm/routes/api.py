"""JSON API routes.

Every mutating endpoint validates its payload, is rate limited, and writes an
audit record. Responses are consistently shaped so the front-end can rely on
``{"ok": true, ...}`` for success and ``{"error": "..."}`` for failure.
"""

from __future__ import annotations

import logging
import socket
from datetime import datetime
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from ..auth import client_address, hash_password, verify_password
from ..capture import resolve_monitor
from ..security import ValidationError, as_float, as_int, as_str, limit, require_json
from ..scheduler import TASK_ACTIONS, add_task, list_tasks, remove_task
from . import login_required

logger = logging.getLogger("softkvm.api")

api_bp = Blueprint("api", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _ext(name: str) -> Any:
    return current_app.extensions[name]


def _actor() -> str:
    return client_address()


def _error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------
@api_bp.route("/metrics")
@login_required
@limit("api")
def metrics():
    """Everything the dashboard needs in a single round trip."""
    collector = _ext("softkvm_metrics")
    history = _ext("softkvm_history")
    payload = collector.collect(force=request.args.get("force") == "1")
    payload = dict(payload)
    payload["processes"] = _ext("softkvm_metrics").collect().get("processes")
    payload["history"] = history.snapshot(limit=90)
    payload["server_time"] = datetime.now().isoformat(timespec="seconds")
    return jsonify(payload)


@api_bp.route("/history")
@login_required
@limit("api")
def history():
    limit_points = as_int(request.args.get("limit", 90), 10, 180, 90)
    return jsonify(_ext("softkvm_history").snapshot(limit=limit_points))


@api_bp.route("/system")
@login_required
@limit("api")
def system():
    return jsonify(_ext("softkvm_metrics").collect()["system"])


@api_bp.route("/processes")
@login_required
@limit("api")
def processes():
    from ..metrics import processes_snapshot

    count = as_int(request.args.get("limit", 20), 1, 100, 20)
    sort_by = request.args.get("sort", "cpu")
    if sort_by not in {"cpu", "memory"}:
        sort_by = "cpu"
    return jsonify(processes_snapshot(limit=count, sort_by=sort_by))


@api_bp.route("/monitors")
@login_required
@limit("api")
def monitors():
    from ..capture import list_monitors

    try:
        items = [monitor.as_dict() for monitor in list_monitors()]
    except Exception as exc:
        return _error(f"could not enumerate displays: {exc}", 503)
    current = _ext("softkvm_config").current.monitor_index
    return jsonify({"monitors": items, "active": current})


@api_bp.route("/info")
@login_required
@limit("api")
def info():
    """Service metadata and the addresses a phone should use."""
    store = _ext("softkvm_config")
    cfg = store.current
    urls = _reachable_urls()
    from ..metrics import interface_addresses

    return jsonify(
        {
            "version": _ext("softkvm_version"),
            "name": "SoftKVM",
            "platform": socket.gethostname(),
            "urls": urls,
            "all_addresses": interface_addresses(),
            "qr_path": "/qr.svg",
            "auth_required": cfg.require_auth,
            "stream": {"width": cfg.stream_width, "height": cfg.stream_height,
                       "fps": cfg.stream_fps, "quality": cfg.stream_quality},
            "input": _ext("softkvm_input").status(),
            "clipboard": {"backend": _ext("softkvm_clipboard").backend,
                          "available": _ext("softkvm_clipboard").available},
            "server_time": datetime.now().isoformat(timespec="seconds"),
        }
    )


def _reachable_urls() -> list[dict[str, str]]:
    """Local network URLs where this server can be reached, with interface names."""
    from ..metrics import interface_addresses

    port = _ext("softkvm_config").current.port
    urls: list[dict[str, str]] = []
    for row in interface_addresses():
        if not row["usable"]:
            continue
        urls.append(
            {
                "address": row["address"],
                "interface": row["name"],
                "url": f"http://{row['address']}:{port}",
            }
        )
    return urls


# ---------------------------------------------------------------------------
# input
# ---------------------------------------------------------------------------
@api_bp.route("/input", methods=["POST"])
@login_required
@limit("input")
def input_event():
    """Pointer, keyboard and text input for the remote session."""
    try:
        data = require_json(request)
    except ValidationError as exc:
        return _error(str(exc))

    kind = str(data.get("type", ""))
    controller = _ext("softkvm_input")
    audit = _ext("softkvm_audit")

    if not controller.available:
        return _error(
            controller.error or "input backend unavailable on this host", 503
        )

    try:
        monitor = resolve_monitor(_ext("softkvm_config").current.monitor_index)
    except Exception as exc:
        return _error(f"no display available: {exc}", 503)

    try:
        if kind == "click":
            x = as_float(data.get("x"), -0.05, 1.05)
            y = as_float(data.get("y"), -0.05, 1.05)
            button = as_str(data.get("button", "left"), 16) or "left"
            count = as_int(data.get("count", 1), 1, 3, 1)
            controller.click(monitor, x, y, button=button, count=count)
            audit.record("kvm:click", actor=_actor(), detail={"x": x, "y": y, "button": button},
                         category="input")

        elif kind == "move":
            x = as_float(data.get("x"), -0.05, 1.05)
            y = as_float(data.get("y"), -0.05, 1.05)
            controller.move(monitor, x, y)

        elif kind == "drag":
            x1 = as_float(data.get("x1"), -0.05, 1.05)
            y1 = as_float(data.get("y1"), -0.05, 1.05)
            x2 = as_float(data.get("x2"), -0.05, 1.05)
            y2 = as_float(data.get("y2"), -0.05, 1.05)
            controller.drag(monitor, x1, y1, x2, y2)
            audit.record("kvm:drag", actor=_actor(), category="input")

        elif kind == "scroll":
            dy = as_int(data.get("dy", 0), -2000, 2000, 0)
            dx = as_int(data.get("dx", 0), -2000, 2000, 0)
            if dy or dx:
                controller.scroll(dx, dy)

        elif kind == "key":
            key = as_str(data.get("key", ""), 32)
            if not key:
                return _error("key is required")
            modifiers = [as_str(m, 16) for m in (data.get("modifiers") or [])]
            if not controller.press_key(key, modifiers):
                return _error(f"unsupported key '{key}'")
            audit.record("kvm:key", actor=_actor(), detail=key, category="input")

        elif kind == "type":
            text = as_str(data.get("text", ""), 2000)
            sent = controller.type_text(text)
            audit.record("kvm:type", actor=_actor(), detail={"chars": sent}, category="input")

        else:
            return _error(f"unknown input type '{kind}'")

    except ValidationError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.warning("input failed: %s", exc)
        return _error(f"input failed: {exc}", 500)

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# control plane
# ---------------------------------------------------------------------------
@api_bp.route("/action", methods=["POST"])
@login_required
@limit("action")
def action():
    """Perform a host control action."""
    try:
        data = require_json(request)
    except ValidationError as exc:
        return _error(str(exc))

    name = as_str(data.get("action", ""), 40)
    system = _ext("softkvm_system")
    audit = _ext("softkvm_audit")

    try:
        if name == "lock":
            result = system.lock()
        elif name == "screen_sleep":
            result = system.sleep_display()
        elif name == "suspend":
            result = system.suspend()
        elif name == "reboot":
            result = system.reboot(as_int(data.get("delay", 3), 0, 300, 3))
        elif name == "shutdown":
            result = system.shutdown(as_int(data.get("delay", 3), 0, 300, 3))
        elif name == "cancel_shutdown":
            result = system.cancel_shutdown()
        elif name == "wifi_on":
            result = system.wifi("on")
        elif name == "wifi_off":
            result = system.wifi("off")
        elif name == "vol_up":
            result = system.volume("up")
        elif name == "vol_down":
            result = system.volume("down")
        elif name == "toggle_mute":
            result = system.volume("mute")
        elif name == "wake_on_lan":
            result = system.wake_on_lan(as_str(data.get("mac", ""), 32))
        else:
            return _error(f"unknown action '{name}'")
    except ValidationError as exc:
        return _error(str(exc))
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.warning("action %s failed: %s", name, exc)
        audit.record(f"action:{name}", actor=_actor(), detail=str(exc), ok=False, category="control")
        return _error(f"action failed: {exc}", 500)

    audit.record(f"action:{name}", actor=_actor(), detail=result.get("message"), category="control")
    return jsonify({"ok": True, **result})


@api_bp.route("/launch", methods=["POST"])
@login_required
@limit("action")
def launch():
    """Launch one of the whitelisted applications."""
    try:
        data = require_json(request)
    except ValidationError as exc:
        return _error(str(exc))

    app_id = as_str(data.get("app", ""), 40)
    system = _ext("softkvm_system")
    audit = _ext("softkvm_audit")

    try:
        result = system.launch(app_id)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.warning("launch failed: %s", exc)
        return _error(f"launch failed: {exc}", 500)

    audit.record("launch", actor=_actor(), detail=app_id, ok=result.get("ok", True),
                 category="control")
    return jsonify({"ok": True, **result})


@api_bp.route("/applications")
@login_required
@limit("api")
def applications():
    return jsonify({"applications": _ext("softkvm_system").available_applications()})


@api_bp.route("/kill", methods=["POST"])
@login_required
@limit("action")
def kill():
    """Terminate a process."""
    try:
        data = require_json(request)
    except ValidationError as exc:
        return _error(str(exc))

    pid = as_int(data.get("pid"), 1, 2**31 - 1)
    system = _ext("softkvm_system")
    audit = _ext("softkvm_audit")

    try:
        result = system.kill_process(pid)
    except PermissionError as exc:
        audit.record("kill", actor=_actor(), detail={"pid": pid}, ok=False, category="control")
        return _error(str(exc), 403)
    except Exception as exc:
        logger.warning("kill failed: %s", exc)
        return _error(f"could not terminate PID {pid}: {exc}", 500)

    audit.record("kill", actor=_actor(), detail={"pid": pid}, ok=True, category="control")
    return jsonify({"ok": True, **result})


# ---------------------------------------------------------------------------
# clipboard
# ---------------------------------------------------------------------------
@api_bp.route("/clipboard", methods=["GET", "POST"])
@login_required
@limit("api")
def clipboard():
    """Read or replace the host clipboard."""
    board = _ext("softkvm_clipboard")
    audit = _ext("softkvm_audit")

    if request.method == "GET":
        if not board.available:
            return _error("no clipboard backend on this host", 503)
        text = board.read()
        return jsonify({"ok": True, "text": text, "backend": board.backend})

    try:
        data = require_json(request)
    except ValidationError as exc:
        return _error(str(exc))

    text = as_str(data.get("text", ""), 100_000)
    if not board.available:
        return _error("no clipboard backend on this host", 503)
    if not board.write(text):
        return _error(board.error or "could not write to the clipboard", 500)
    audit.record("clipboard:write", actor=_actor(), detail={"chars": len(text)}, category="control")
    return jsonify({"ok": True, "message": "Clipboard updated"})


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
@api_bp.route("/settings", methods=["GET", "POST"])
@login_required
@limit("api")
def settings():
    store = _ext("softkvm_config")

    if request.method == "GET":
        return jsonify({"ok": True, "settings": store.current.sanitised()})

    try:
        data = require_json(request)
    except ValidationError as exc:
        return _error(str(exc))

    patch = data.get("settings")
    if not isinstance(patch, dict):
        return _error("expected a 'settings' object")

    changed = store.update(patch)
    if not changed:
        return jsonify({"ok": True, "changed": [], "settings": store.current.sanitised()})

    if {"stream_width", "stream_height", "stream_fps", "stream_quality"} & set(changed):
        _ext("softkvm_stream").apply_settings()

    _ext("softkvm_audit").record(
        "settings:update", actor=_actor(), detail=changed, category="settings"
    )
    return jsonify({"ok": True, "changed": changed, "settings": store.current.sanitised()})


@api_bp.route("/settings/password", methods=["POST"])
@login_required
@limit("login")
def change_password():
    """Change the access password."""
    store = _ext("softkvm_config")
    audit = _ext("softkvm_audit")

    try:
        data = require_json(request)
    except ValidationError as exc:
        return _error(str(exc))

    current = as_str(data.get("current", ""), 200)
    new = as_str(data.get("new", ""), 200)

    if not verify_password(current, store.current.password_hash, store.current.password_salt):
        audit.record("password:change", actor=_actor(), ok=False, category="auth")
        return _error("current password is incorrect", 403)
    if len(new) < 8:
        return _error("the new password must be at least 8 characters")
    if new == current:
        return _error("the new password must differ from the current one")

    password_hash, salt = hash_password(new)
    store.set_password(password_hash, salt)
    audit.record("password:change", actor=_actor(), ok=True, category="auth")
    return jsonify({"ok": True, "message": "Password updated"})


@api_bp.route("/monitor", methods=["POST"])
@login_required
@limit("api")
def set_monitor():
    """Change which display the KVM stream shows."""
    try:
        data = require_json(request)
    except ValidationError as exc:
        return _error(str(exc))

    index = as_int(data.get("index"), 0, 16)
    store = _ext("softkvm_config")
    store.update({"monitor_index": index})
    _ext("softkvm_audit").record("monitor:switch", actor=_actor(), detail=index,
                                 category="settings")
    return jsonify({"ok": True, "monitor_index": index})


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------
@api_bp.route("/schedule", methods=["GET", "POST"])
@login_required
@limit("api")
def schedule():
    store = _ext("softkvm_config")

    if request.method == "GET":
        return jsonify({"ok": True, "tasks": list_tasks(store), "actions": TASK_ACTIONS})

    try:
        data = require_json(request)
    except ValidationError as exc:
        return _error(str(exc))

    try:
        task = add_task(store, data)
    except ValueError as exc:
        return _error(str(exc))

    _ext("softkvm_audit").record("schedule:add", actor=_actor(), detail=task.get("label"),
                                 category="schedule")
    return jsonify({"ok": True, "task": task, "tasks": list_tasks(store)})


@api_bp.route("/schedule/<task_id>", methods=["DELETE"])
@login_required
@limit("api")
def schedule_delete(task_id: str):
    store = _ext("softkvm_config")
    if not remove_task(store, task_id):
        return _error("task not found", 404)
    _ext("softkvm_audit").record("schedule:remove", actor=_actor(), detail=task_id,
                                 category="schedule")
    return jsonify({"ok": True, "tasks": list_tasks(store)})


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
@api_bp.route("/audit")
@login_required
@limit("api")
def audit_log():
    count = as_int(request.args.get("limit", 60), 1, 200, 60)
    category = request.args.get("category")
    return jsonify({"ok": True, "events": _ext("softkvm_audit").recent(count, category)})
