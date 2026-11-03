#!/usr/bin/env python3
"""SoftKVM - browser based KVM and system dashboard.

Run with:

    python server.py

Environment variables (all optional):

    SOFTKVM_HOST         bind address            (default 0.0.0.0)
    SOFTKVM_PORT         listen port             (default 3699)
    SOFTKVM_PASSWORD     first-run password
    SOFTKVM_DATA_DIR     where config.json lives (default ./data)
    SOFTKVM_THEME        dark | light
    SOFTKVM_WALLPAPER    wallpaper id
    SOFTKVM_MONITOR      monitor index to stream
    SOFTKVM_REQUIRE_AUTH true | false
    SOFTKVM_DEBUG        true | false  (enables the Flask dev server)
"""

from __future__ import annotations

import logging
import os
import socket
import sys
from pathlib import Path

# Make sure the package is importable when started from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from softkvm import __version__, create_app  # noqa: E402
from softkvm.config import ConfigStore  # noqa: E402
from softkvm.logging_utils import configure_logging  # noqa: E402

logger = logging.getLogger("softkvm.server")


def local_addresses(port: int) -> tuple[list[str], list[tuple[str, str]]]:
    """Return (usable LAN URLs, all interfaces) for the startup banner.

    ``usable`` excludes loopback and link-local (169.254.x.x) addresses -
    Windows hands one of those to every virtual adapter (Hyper-V, WSL,
    VirtualBox, VPN filters) and they cannot be reached from other machines.
    """
    usable: list[str] = []
    every: list[tuple[str, str]] = []

    try:
        import psutil

        rows = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception:
        return [f"http://127.0.0.1:{port}"], []

    for name, addresses in rows.items():
        ipv4 = next((a.address for a in addresses if a.family == socket.AF_INET), None)
        if not ipv4:
            continue
        iface_stats = stats.get(name)
        up = bool(iface_stats.isup) if iface_stats else True
        every.append((name, ipv4))

        if ipv4.startswith("127."):
            continue
        if ipv4.startswith("169.254."):
            continue
        if not up:
            continue
        usable.append(f"http://{ipv4}:{port}")

    if not usable:
        usable.append(f"http://127.0.0.1:{port}")
    return usable, every


def main() -> int:
    configure_logging()
    store = ConfigStore()
    app = create_app(store)
    cfg = store.current
    urls, others = local_addresses(cfg.port)

    banner = [
        "",
        "  SoftKVM " + __version__,
        "  " + "-" * 52,
    ]
    for url in urls:
        banner.append(f"  Open             {url}")
    if others:
        banner.append("  " + "-" * 52)
        banner.append("  Other addresses on this machine (not reachable remotely):")
        for name, address in others:
            banner.append(f"    {name:<18} {address}")
    banner.append("  " + "-" * 52)
    banner.append("  Press Ctrl+C to stop.")
    banner.append("")
    print("\n".join(banner))

    if cfg.use_dev_server and cfg.debug:
        logger.info("Starting the Flask development server")
        app.run(host=cfg.host, port=cfg.port, debug=True, threaded=True)
        return 0

    try:
        from waitress import serve
    except ImportError:
        logger.warning("waitress is not installed - falling back to the Flask dev server")
        logger.warning("Install it for production use:  pip install waitress")
        app.run(host=cfg.host, port=cfg.port, debug=False, threaded=True)
        return 0

    logger.info("Starting waitress on %s:%s", cfg.host, cfg.port)
    try:
        serve(app, host=cfg.host, port=cfg.port, threads=16, channel_timeout=120)
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        app.extensions["softkvm_stream"].shutdown()
        app.extensions["softkvm_sampler"].stop()
        app.extensions["softkvm_scheduler"].stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
