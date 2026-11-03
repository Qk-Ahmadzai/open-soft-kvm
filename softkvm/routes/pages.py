"""HTML page routes."""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, redirect, render_template, request, url_for

from ..auth import (
    SESSION_COOKIE,
    client_address,
    generate_password,
    hash_password,
    verify_password,
)
from . import login_required

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/healthz")
def healthz():
    """Liveness probe - intentionally unauthenticated and cheap."""
    return {
        "status": "ok",
        "version": current_app.extensions["softkvm_version"],
        "time": datetime.now().isoformat(timespec="seconds"),
    }


@pages_bp.route("/login", methods=["GET", "POST"])
def login():
    """Password login. Issues a signed session cookie on success."""
    store = current_app.extensions["softkvm_config"]
    sessions = current_app.extensions["softkvm_sessions"]
    throttle = current_app.extensions["softkvm_throttle"]

    if not store.current.require_auth:
        return redirect(url_for("pages.dashboard"))

    if request.method == "GET":
        return render_template("login.html", error=None, next=request.args.get("next", ""))

    password = request.form.get("password", "")
    address = client_address()

    remaining = throttle.remaining_lockout(address)
    if remaining:
        return (
            render_template(
                "login.html",
                error=f"Too many failed attempts. Try again in {remaining}s.",
                next=request.form.get("next", ""),
            ),
            429,
        )

    # Re-read the hash from disk: the password may have been changed by
    # `python server.py --reset-password` in another process.
    password_hash, password_salt = store.reload_password()
    if verify_password(password, password_hash, password_salt):
        throttle.record_success(address)
        current_app.extensions["softkvm_audit"].record(
            "login", actor=address, ok=True, category="auth"
        )
        response = redirect(request.form.get("next") or url_for("pages.dashboard"))
        response.set_cookie(
            SESSION_COOKIE,
            sessions.issue(),
            max_age=store.current.session_ttl_seconds,
            httponly=True,
            samesite="Lax",
        )
        return response

    locked = throttle.record_failure(address)
    current_app.extensions["softkvm_audit"].record(
        "login", actor=address, ok=False, detail="invalid password", category="auth"
    )
    message = "Incorrect password."
    if locked:
        message = f"Incorrect password. Locked for {locked}s."
    return render_template("login.html", error=message, next=request.form.get("next", "")), 401


@pages_bp.route("/logout")
def logout():
    """Drop the session cookie."""
    current_app.extensions["softkvm_audit"].record(
        "logout", actor=client_address(), ok=True, category="auth"
    )
    response = redirect(url_for("pages.login"))
    response.delete_cookie(SESSION_COOKIE)
    return response


@pages_bp.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html", active="dashboard")


@pages_bp.route("/kvm")
@login_required
def kvm():
    return render_template("kvm.html", active="kvm")


@pages_bp.route("/controls")
@login_required
def controls():
    return render_template("controls.html", active="controls")


@pages_bp.route("/schedule")
@login_required
def schedule():
    return render_template("schedule.html", active="schedule")


@pages_bp.route("/settings")
@login_required
def settings():
    return render_template("settings.html", active="settings")


@pages_bp.route("/qr.svg")
@login_required
def qr_code():
    """QR code (SVG) pointing at this server, for quick phone sign-in."""
    from flask import Response

    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return Response(
            '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240">'
            '<text x="10" y="120" fill="#333" font-size="12">QR unavailable</text></svg>',
            mimetype="image/svg+xml",
        )

    url = request.url_root.rstrip("/")
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    import io

    buffer = io.BytesIO()
    image.save(buffer)
    return Response(buffer.getvalue(), mimetype="image/svg+xml")
