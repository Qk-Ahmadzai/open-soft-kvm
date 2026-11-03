"""Route blueprints for SoftKVM."""

from __future__ import annotations

from functools import wraps

from flask import current_app


def login_required(view):
    """Guard a view with the session check configured by :func:`init_auth`.

    The guard is resolved at request time so the decorator can be applied at
    import time, before the application context exists.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        guard = current_app.extensions["softkvm_login_required"]
        return guard(view)(*args, **kwargs)

    return wrapper
