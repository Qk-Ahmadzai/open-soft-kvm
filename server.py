#!/usr/bin/env python3
"""SoftKVM - browser based KVM and system dashboard.

Run with:

    python server.py

Manage the access password:

    python server.py --reset-password          # generate a new one and print it
    python server.py --set-password "my pass"  # set a specific one

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
from softkvm.auth import generate_password, hash_password  # noqa: E402
from softkvm.config import ConfigStore  # noqa: E402
from softkvm.logging_utils import configure_logging  # noqa: E402

logger = logging.getLogger("softkvm.server")

USAGE = """SoftKVM - browser based KVM and system dashboard

Usage:
  python server.py                      start the server
  python server.py --reset-password     generate a new password and print it
  python server.py --set-password PASS  set a specific password
"""


def set_new_password(store, password: str | None = None) -> str:
    """Store a new password hash. Generates one when ``password`` is None."""
    chosen = password or generate_password()
    password_hash, salt = hash_password(chosen)
    store.set_password(password_hash, salt)
    return chosen


def interface_rows() -> list[dict[str, str]]:
    """Every IPv4 interface, classified as reachable or not."""
    try:
        from softkvm.metrics import interface_addresses

        return interface_addresses()
    except Exception:
        return []


def local_addresses(port: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split the host's IPv4 addresses into (reachable, not-reachable).

    ``169.254.x.x`` link-local addresses and ``127.x`` loopback are never
    reachable from another machine - Windows assigns link-local addresses to
    every virtual adapter (Hyper-V, WSL, VirtualBox, VPN filters), so listing
    them as usable only creates confusion.
    """
    reachable: list[tuple[str, str]] = []
    unreachable: list[tuple[str, str]] = []

    for row in interface_rows():
        address = row["address"]
        if address.startswith("127."):
            unreachable.append((row["name"], address))
        elif address.startswith("169.254."):
            unreachable.append((row["name"], address))
        elif row["up"] == "up":
            reachable.append((row["name"], f"http://{address}:{port}"))
        else:
            unreachable.append((row["name"], address))

    if not reachable:
        reachable.append(("localhost", f"http://127.0.0.1:{port}"))
    return reachable, unreachable


def main() -> int:
    configure_logging()
    args = sys.argv[1:]

    # ---- password management (runs without starting the server) --------
    if args and args[0] in {"--reset-password", "--set-password"}:
        store = ConfigStore()
        chosen = None
        if args[0] == "--set-password":
            if len(args) < 2 or not args[1]:
                print("--set-password needs a value", file=sys.stderr)
                print(USAGE, file=sys.stderr)
                return 2
            chosen = args[1]
            if len(chosen) < 8:
                print("The password must be at least 8 characters.", file=sys.stderr)
                return 2
        password = set_new_password(store, chosen)
        print("")
        print("  SoftKVM access password")
        print("  " + "-" * 40)
        print(f"    {password}")
        print("  " + "-" * 40)
        print(f"  Saved to {store.path}")
        print("  Sign in at http://127.0.0.1:<port>/ and change it under Settings.")
        print("")
        return 0

    if args and args[0] in {"-h", "--help"}:
        print(USAGE)
        return 0

    store = ConfigStore()
    app = create_app(store)
    cfg = store.current
    reachable, unreachable = local_addresses(cfg.port)

    banner = [
        "",
        "  SoftKVM " + __version__,
        "  " + "-" * 52,
    ]
    banner.append("  Reachable on this network:")
    for name, url in reachable:
        banner.append(f"    {name:<20} {url}")
    if unreachable:
        banner.append("")
        banner.append("  Not reachable remotely (link-local / loopback):")
        for name, address in unreachable:
            banner.append(f"    {name:<20} {address}")
    banner.append("  " + "-" * 52)

    generated = app.extensions.get("softkvm_generated_password")
    if generated:
        banner.append(f"  Access password: {generated}")
        banner.append("  Change it under Settings, or with: python server.py --reset-password")
    elif cfg.require_auth:
        banner.append("  Access password: already set (not shown again)")
        banner.append("  Forgotten it?  Run: python server.py --reset-password")
    else:
        banner.append("  Authentication is DISABLED - anyone on the network can control this machine.")

    banner.append("  " + "-" * 52)
    banner.append("  Press Ctrl+C to stop.")
    banner.append("")
    print("\n".join(banner))

    if cfg.debug:
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
