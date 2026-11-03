"""Configuration persistence and coercion tests."""

from __future__ import annotations

import json
from pathlib import Path

from softkvm.config import Config, ConfigStore, available_wallpapers


def test_defaults_are_sane():
    config = Config()
    assert config.port == 3699
    assert config.require_auth is True
    assert config.stream_fps == 15
    assert 10 <= config.stream_quality <= 95


def test_store_round_trips(tmp_path: Path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.update({"theme": "light", "stream_fps": 25})

    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["theme"] == "light"
    assert raw["stream_fps"] == 25

    reloaded = ConfigStore(path)
    assert reloaded.current.theme == "light"
    assert reloaded.current.stream_fps == 25


def test_update_rejects_unknown_and_secret_fields(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    changed = store.update(
        {
            "theme": "light",
            "not_a_field": "x",
            "secret_key": "hijacked",
            "password_hash": "hijacked",
        }
    )
    assert changed == ["theme"]
    assert store.current.secret_key != "hijacked"
    assert store.current.password_hash != "hijacked"


def test_update_coerces_types(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    store.update({"stream_fps": "30", "wallpaper_blur": "false", "port": "4000"})
    assert store.current.stream_fps == 30
    assert store.current.wallpaper_blur is False
    assert store.current.port == 4000


def test_sanitised_hides_secrets(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    clean = store.current.sanitised()
    assert "secret_key" not in clean
    assert "password_hash" not in clean
    assert "password_salt" not in clean
    assert "theme" in clean


def test_corrupt_config_falls_back_to_defaults(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = ConfigStore(path)
    assert store.current.theme == "dark"


def test_wallpapers_are_discovered():
    wallpapers = available_wallpapers()
    assert wallpapers, "expected bundled wallpapers to be present"
    names = {item["id"] for item in wallpapers}
    assert "night-sky" in names
    for item in wallpapers:
        assert item["url"].startswith("/static/img/wallpapers/")
