"""SoftKVM - a production-grade browser KVM and system dashboard.

Application factory. Everything is wired here so the app can be created with
a custom config store in tests without touching the real data directory.
"""

from __future__ import annotations

import logging
import os
import platform
import secrets
import socket
import sys
from pathlib import Path
from typing import Any

from flask import Flask, current_app, jsonify, request

from .audit import AuditLog
from .auth import generate_password, hash_password, init_auth
from .capture import StreamManager
from .config import ConfigStore, available_wallpapers
from .inputctl import Clipboard, InputController
from .logging_utils import configure_logging
from .metrics import MetricsCollector, MetricsHistory, MetricsSampler
from .scheduler import Scheduler
from .security import ValidationError, init_security
from .systemctl import SystemController

__version__ = "2.0.0"

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def create_app(config_store: ConfigStore | None = None) -> Flask:
    """Build and configure the Flask application."""
    configure_logging()
    logger = logging.getLogger("softkvm")

    app = Flask(
        __name__,
        root_path=str(PACKAGE_ROOT),
        template_folder=str(PACKAGE_ROOT / "templates"),
        static_folder=str(PACKAGE_ROOT / "static"),
        static_url_path="/static",
    )

    store = config_store or ConfigStore()
    cfg = store.current

    app.config.update(
        SECRET_KEY=cfg.secret_key,
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SEND_FILE_MAX_AGE_DEFAULT=0,
        TEMPLATES_AUTO_RELOAD=True,
        PROPAGATE_EXCEPTIONS=False,
    )
    app.url_map.strict_slashes = False

    # -- components -------------------------------------------------
    limiter = init_security(app)
    init_auth(app, store)

    metrics_history = MetricsHistory(maxlen=180)
    collector = MetricsCollector(metrics_history)
    streamer = StreamManager(store)
    controller = InputController()
    clipboard = Clipboard()
    system = SystemController()
    audit = AuditLog(store.path.parent / "audit.jsonl")
    scheduler = Scheduler(store, system, audit)

    app.extensions["softkvm_config"] = store
    app.extensions["softkvm_metrics"] = collector
    app.extensions["softkvm_history"] = metrics_history
    app.extensions["softkvm_stream"] = streamer
    app.extensions["softkvm_input"] = controller
    app.extensions["softkvm_clipboard"] = clipboard
    app.extensions["softkvm_system"] = system
    app.extensions["softkvm_audit"] = audit
    app.extensions["softkvm_scheduler"] = scheduler
    app.extensions["softkvm_version"] = __version__

    # -- first-run password ----------------------------------------
    generated_password: str | None = None
    if not cfg.password_hash:
        password = os.environ.get("SOFTKVM_PASSWORD") or generate_password()
        password_hash, salt = hash_password(password)
        store.set_password(password_hash, salt)
        generated_password = password
        logger.info("=" * 68)
        logger.info(" SoftKVM %s - first run", __version__)
        logger.info(" Access password: %s", password)
        logger.info(" Change it from Settings, or with: python server.py --reset-password")
        logger.info("=" * 68)
        if not os.environ.get("SOFTKVM_PASSWORD"):
            print("\n" + "=" * 68)
            print(f" SoftKVM {__version__} - first run")
            print(f" Access password : {password}")
            print(" Save it now - it is not shown again.")
            print("=" * 68 + "\n")
    else:
        logger.info(
            "Access password is already set. Run 'python server.py --reset-password' to set a new one."
        )

    app.extensions["softkvm_generated_password"] = generated_password

    # -- blueprints --------------------------------------------------
    from .routes.api import api_bp
    from .routes.pages import pages_bp
    from .routes.stream import stream_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(stream_bp)

    # -- template globals -------------------------------------------
    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        # `current_user` is request-bound, so this must be resolved inside a
        # request context - which is exactly when the context processor runs.
        user = current_app.extensions["softkvm_current_user"]()
        return {
            "app_version": __version__,
            "wallpapers": available_wallpapers(),
            "current_config": store.current.sanitised(),
            "platform_name": platform.system(),
            "signed_in": user is not None,
        }

    # -- error handlers ---------------------------------------------
    @app.errorhandler(404)
    def not_found(error):  # type: ignore[unused-argument]
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return ("<h1>404</h1><p>Page not found. "
                "<a href='/'>Return to dashboard</a></p>"), 404

    @app.errorhandler(429)
    def too_many(error):  # type: ignore[unused-argument]
        return jsonify({"error": "rate limit exceeded"}), 429

    @app.errorhandler(ValidationError)
    def validation_error(exc: ValidationError):
        """Every rejected payload becomes a clean 400 instead of a 500."""
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": str(exc)}), 400
        return str(exc), 400

    @app.errorhandler(500)
    def server_error(error):  # type: ignore[unused-argument]
        logger.exception("unhandled error on %s", request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": "internal server error"}), 500
        return ("<h1>500</h1><p>Something went wrong on the server. "
                "<a href='/'>Return to dashboard</a></p>"), 500

    # -- background workers ------------------------------------------
    sampler = MetricsSampler(collector, interval=max(1.0, cfg.poll_interval_ms / 1000))
    sampler.start()
    scheduler.start()
    app.extensions["softkvm_sampler"] = sampler

    @app.teardown_appcontext
    def _teardown(exception=None):  # type: ignore[unused-argument]
        return None

    @app.after_request
    def _no_store_for_dynamic(response):
        if request.path.startswith(("/api/", "/stream")):
            response.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate")
            response.headers.setdefault("Pragma", "no-cache")
        return response

    logger.info("SoftKVM %s ready on %s:%s", __version__, cfg.host, cfg.port)
    return app
