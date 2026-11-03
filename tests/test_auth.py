"""Authentication, session and throttling tests."""

from __future__ import annotations

import time

from softkvm.auth import (
    LoginThrottle,
    SessionManager,
    generate_password,
    hash_password,
    verify_password,
)


def test_password_hash_round_trip():
    password_hash, salt = hash_password("hunter2")
    assert verify_password("hunter2", password_hash, salt)
    assert not verify_password("hunter3", password_hash, salt)
    assert not verify_password("hunter2", "deadbeef", salt)


def test_password_hashes_are_salted():
    first, salt_one = hash_password("same")
    second, salt_two = hash_password("same")
    assert salt_one != salt_two
    assert first != second
    assert verify_password("same", first, salt_one)
    assert verify_password("same", second, salt_two)


def test_verify_rejects_empty_storage():
    assert not verify_password("x", "", "abc")
    assert not verify_password("x", "abc", "")


def test_generated_password_is_long_and_usable():
    password = generate_password(16)
    assert len(password) == 16
    password_hash, salt = hash_password(password)
    assert verify_password(password, password_hash, salt)


def test_session_issue_and_read():
    sessions = SessionManager("secret-key", ttl_seconds=60)
    token = sessions.issue("operator")
    payload = sessions.read(token)
    assert payload is not None
    assert payload["u"] == "operator"


def test_session_rejects_tampered_token():
    sessions = SessionManager("secret-key", ttl_seconds=60)
    token = sessions.issue("operator")
    assert sessions.read(token + "x") is None
    assert sessions.read("not-a-token") is None
    assert sessions.read(None) is None


def test_session_expires():
    sessions = SessionManager("secret-key", ttl_seconds=1)
    token = sessions.issue("operator")
    # itsdangerous timestamps have one-second resolution, so wait past a
    # full boundary to make the expiry deterministic.
    time.sleep(2.2)
    assert sessions.read(token) is None


def test_throttle_locks_after_max_attempts():
    throttle = LoginThrottle(max_attempts=3, base_lockout=60)
    for _ in range(3):
        throttle.record_failure("10.0.0.1")
    assert throttle.remaining_lockout("10.0.0.1") > 0


def test_throttle_clears_on_success():
    throttle = LoginThrottle(max_attempts=3, base_lockout=60)
    throttle.record_failure("10.0.0.2")
    throttle.record_success("10.0.0.2")
    assert throttle.remaining_lockout("10.0.0.2") == 0


def test_throttle_is_per_address():
    throttle = LoginThrottle(max_attempts=2, base_lockout=60)
    throttle.record_failure("1.1.1.1")
    throttle.record_failure("1.1.1.1")
    assert throttle.remaining_lockout("1.1.1.1") > 0
    assert throttle.remaining_lockout("2.2.2.2") == 0


def test_login_flow_sets_cookie(secure_client):
    response = secure_client.post("/login", data={"password": "correct-horse"})
    assert response.status_code == 302
    assert "softkvm_session" in response.headers.get("Set-Cookie", "")


def test_login_rejects_bad_password(secure_client):
    response = secure_client.post("/login", data={"password": "wrong"})
    assert response.status_code == 401
    assert b"Incorrect password" in response.data


def test_protected_page_redirects_to_login(secure_client):
    response = secure_client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_api_returns_401_when_unauthenticated(secure_client):
    response = secure_client.get("/api/metrics")
    assert response.status_code == 401
    assert response.get_json()["error"] == "authentication required"


def test_healthz_is_public(secure_client):
    response = secure_client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_logout_clears_cookie(secure_client):
    secure_client.post("/login", data={"password": "correct-horse"})
    response = secure_client.get("/logout")
    assert response.status_code == 302
    assert secure_client.get("/api/metrics").status_code == 401


def test_reload_password_picks_up_a_change_from_another_process(tmp_path):
    """`server.py --reset-password` writes to disk; a running server must notice."""
    from softkvm.config import ConfigStore

    store = ConfigStore(tmp_path / "config.json")
    first_hash, first_salt = hash_password("first-password-1")
    store.set_password(first_hash, first_salt)

    # A second store instance stands in for the separate CLI process.
    other = ConfigStore(tmp_path / "config.json")
    second_hash, second_salt = hash_password("second-password-2")
    other.set_password(second_hash, second_salt)

    # The first store still holds the old hash in memory...
    assert store.current.password_hash == first_hash
    # ...but reload_password() re-reads it from disk.
    live_hash, live_salt = store.reload_password()
    assert live_hash == second_hash
    assert verify_password("second-password-2", live_hash, live_salt)
    assert not verify_password("first-password-1", live_hash, live_salt)


def test_login_accepts_a_password_changed_in_another_process(secure_client, secure_store):
    """A reset performed while the server runs must take effect immediately."""
    from softkvm.auth import hash_password

    new_hash, new_salt = hash_password("brand-new-password")
    # Write directly to the file, bypassing the running server's memory.
    import json
    from pathlib import Path

    path = Path(secure_store.path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["password_hash"] = new_hash
    raw["password_salt"] = new_salt
    path.write_text(json.dumps(raw), encoding="utf-8")

    response = secure_client.post("/login", data={"password": "brand-new-password"})
    assert response.status_code == 302
    assert "softkvm_session" in response.headers.get("Set-Cookie", "")
