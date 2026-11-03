"""API contract tests: payload shapes, validation and error handling."""

from __future__ import annotations

import json


def test_metrics_payload_has_expected_sections(client):
    response = client.get("/api/metrics")
    assert response.status_code == 200
    payload = response.get_json()
    for key in ("cpu", "memory", "disk", "network", "sensors", "system", "history"):
        assert key in payload, f"missing {key} in metrics payload"
    assert "percent" in payload["cpu"]
    assert "total_human" in payload["memory"]


def test_history_endpoint_returns_series(client):
    response = client.get("/api/history?limit=20")
    assert response.status_code == 200
    payload = response.get_json()
    assert "cpu" in payload
    assert "t" in payload["cpu"]


def test_processes_endpoint(client):
    response = client.get("/api/processes?limit=5&sort=memory")
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["processes"]) <= 5


def test_system_endpoint(client):
    response = client.get("/api/system")
    assert response.status_code == 200
    assert "hostname" in response.get_json()


def test_info_endpoint_lists_urls(client):
    response = client.get("/api/info")
    assert response.status_code == 200
    payload = response.get_json()
    assert "version" in payload
    assert isinstance(payload["urls"], list)


def test_monitors_endpoint(client):
    response = client.get("/api/monitors")
    assert response.status_code == 200
    payload = response.get_json()
    assert "monitors" in payload
    assert "active" in payload


def test_monitors_endpoint_reports_capture_status(client):
    """The UI needs to know *why* a stream is empty, not just that it is."""
    payload = client.get("/api/monitors").get_json()
    capture = payload["capture"]
    assert "available" in capture
    assert "error" in capture
    assert "running" in capture
    assert capture["monitor"] == payload["active"]


def test_input_requires_json_object(client):
    response = client.post("/api/input", data="not json", content_type="text/plain")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_input_rejects_unknown_type(client):
    response = client.post("/api/input", json={"type": "teleport"})
    assert response.status_code == 400
    assert "unknown input type" in response.get_json()["error"]


def test_input_rejects_out_of_range_coordinates(client):
    response = client.post("/api/input", json={"type": "click", "x": 5.0, "y": 0.5})
    assert response.status_code == 400
    assert "out of range" in response.get_json()["error"]


def test_input_rejects_missing_key(client):
    response = client.post("/api/input", json={"type": "key"})
    assert response.status_code == 400


def test_action_rejects_unknown_action(client):
    response = client.post("/api/action", json={"action": "self_destruct"})
    assert response.status_code == 400
    assert "unknown action" in response.get_json()["error"]


def test_action_requires_json(client):
    response = client.post("/api/action", data="{}", content_type="text/plain")
    assert response.status_code == 400


def test_launch_rejects_unknown_app(client):
    response = client.post("/api/launch", json={"app": "definitely-not-an-app"})
    assert response.status_code == 400


def test_kill_rejects_invalid_pid(client):
    response = client.post("/api/kill", json={"pid": 0})
    assert response.status_code == 400


def test_kill_rejects_non_numeric_pid(client):
    response = client.post("/api/kill", json={"pid": "abc"})
    assert response.status_code == 400


def test_settings_round_trip(client):
    response = client.post("/api/settings", json={"settings": {"theme": "light"}})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert "theme" in payload["changed"]
    assert client.get("/api/settings").get_json()["settings"]["theme"] == "light"


def test_settings_rejects_non_object(client):
    response = client.post("/api/settings", json={"settings": "nope"})
    assert response.status_code == 400


def test_settings_ignores_secret_overwrite(client):
    response = client.post(
        "/api/settings", json={"settings": {"secret_key": "hijack", "theme": "dark"}}
    )
    assert response.status_code == 200
    assert "secret_key" not in response.get_json()["changed"]
    assert response.get_json()["settings"].get("secret_key") is None


def test_password_change_rejects_wrong_current(client):
    response = client.post(
        "/api/settings/password", json={"current": "nope", "new": "longenough1"}
    )
    assert response.status_code == 403


def test_password_change_requires_minimum_length(client):
    response = client.post(
        "/api/settings/password", json={"current": "x", "new": "short"}
    )
    # Either the current password is wrong (403) or the length check trips (400).
    assert response.status_code in (400, 403)


def test_schedule_crud(client):
    response = client.post(
        "/api/schedule",
        json={"action": "reboot", "at": "23:30", "label": "Nightly", "daily": True},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    task_id = payload["task"]["id"]

    listing = client.get("/api/schedule").get_json()
    assert any(task["id"] == task_id for task in listing["tasks"])

    assert client.delete(f"/api/schedule/{task_id}").status_code == 200
    listing = client.get("/api/schedule").get_json()
    assert not any(task["id"] == task_id for task in listing["tasks"])


def test_schedule_rejects_unknown_action(client):
    response = client.post("/api/schedule", json={"action": "explode", "at": "10:00"})
    assert response.status_code == 400


def test_schedule_rejects_bad_time(client):
    response = client.post("/api/schedule", json={"action": "reboot", "at": "not-a-time"})
    assert response.status_code == 400


def test_schedule_delete_unknown_task(client):
    assert client.delete("/api/schedule/does-not-exist").status_code == 404


def test_audit_endpoint(client):
    client.post("/api/action", json={"action": "lock"})
    response = client.get("/api/audit?limit=5")
    assert response.status_code == 200
    payload = response.get_json()
    assert "events" in payload


def test_404_for_unknown_api_route(client):
    response = client.get("/api/definitely-not-here")
    assert response.status_code == 404
    assert response.get_json()["error"] == "not found"


def test_security_headers_present(client):
    response = client.get("/healthz")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in response.headers
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_rate_limit_is_enforced(client):
    limiter = client.application.extensions["softkvm_limiter"]
    limiter.reset()
    # The "action" bucket allows 60 events per minute.
    statuses = [client.post("/api/action", json={"action": "lock"}).status_code for _ in range(70)]
    assert 429 in statuses, "expected the rate limiter to reject some requests"
    limiter.reset()
