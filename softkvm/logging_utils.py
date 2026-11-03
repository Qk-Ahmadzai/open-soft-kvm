"""Logging configuration for SoftKVM."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_CONFIGURED = False

LOG_FORMAT = "%(asctime)s  %(levelname)-8s %(name)-18s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str | None = None) -> None:
    """Configure root logging once, with a rotating file handler."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    resolved = (level or os.environ.get("SOFTKVM_LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger()
    root.setLevel(getattr(logging, resolved, logging.INFO))

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        from .config import default_data_dir

        data_dir = default_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            data_dir / "softkvm.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        pass  # file logging is best-effort

    # Werkzeug's request log is noisy at INFO level.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _CONFIGURED = True
