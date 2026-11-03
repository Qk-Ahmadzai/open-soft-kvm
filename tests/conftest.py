"""Shared pytest fixtures for the SoftKVM test suite."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from softkvm import create_app  # noqa: E402
from softkvm.auth import hash_password  # noqa: E402
from softkvm.config import ConfigStore  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    return directory


@pytest.fixture()
def config_store(data_dir: Path) -> ConfigStore:
    store = ConfigStore(data_dir / "config.json")
    store.update(
        {
            "require_auth": False,
            "debug": False,
            "poll_interval_ms": 1000,
        }
    )
    return store


@pytest.fixture()
def app(config_store: ConfigStore):
    application = create_app(config_store)
    application.config.update(TESTING=True)
    yield application
    application.extensions["softkvm_stream"].shutdown()
    application.extensions["softkvm_sampler"].stop()
    application.extensions["softkvm_scheduler"].stop()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def secure_store(data_dir: Path) -> ConfigStore:
    """A store that requires a password."""
    store = ConfigStore(data_dir / "config.json")
    password_hash, salt = hash_password("correct-horse")
    store.set_password(password_hash, salt)
    store.update({"require_auth": True})
    return store


@pytest.fixture()
def secure_client(secure_store: ConfigStore):
    application = create_app(secure_store)
    application.config.update(TESTING=True)
    with application.test_client() as test_client:
        yield test_client
    application.extensions["softkvm_stream"].shutdown()
    application.extensions["softkvm_sampler"].stop()
    application.extensions["softkvm_scheduler"].stop()
