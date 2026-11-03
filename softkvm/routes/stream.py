"""Streaming routes: MJPEG live view and single-frame snapshots."""

from __future__ import annotations

import io
import logging

from flask import Blueprint, Response, current_app, request, stream_with_context

from ..capture import BOUNDARY, multipart_stream, open_capture, resolve_monitor
from ..security import as_int, limit
from . import login_required

logger = logging.getLogger("softkvm.stream")

stream_bp = Blueprint("stream", __name__)


def _active_monitor():
    store = current_app.extensions["softkvm_config"]
    try:
        return resolve_monitor(store.current.monitor_index)
    except Exception:
        return resolve_monitor(1)


@stream_bp.route("/stream")
@login_required
@limit("stream")
def stream():
    """Live MJPEG feed of the selected monitor."""
    store = current_app.extensions["softkvm_config"]
    manager = current_app.extensions["softkvm_stream"]

    requested = request.args.get("monitor")
    monitor_index = store.current.monitor_index
    if requested is not None:
        try:
            monitor_index = as_int(requested, 0, 16, monitor_index)
        except Exception:
            pass

    streamer = manager.streamer_for(monitor_index)
    subscriber = streamer.subscribe()

    def generate():
        try:
            yield from multipart_stream(
                subscriber, lambda: manager.streamer_for(monitor_index).unsubscribe(subscriber)
            )
        except GeneratorExit:
            raise
        except Exception as exc:  # pragma: no cover - transport dependent
            logger.debug("stream ended: %s", exc)

        # The boundary parameter is mandatory: without it the browser cannot
    # parse the multipart body and the <img> tag silently fails to render.
    response = Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=" + BOUNDARY,
    )
    response.direct_passthrough = True
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@stream_bp.route("/snapshot")
@login_required
@limit("api")
def snapshot():
    """Single still frame of the selected monitor, as JPEG."""
    store = current_app.extensions["softkvm_config"]
    manager = current_app.extensions["softkvm_stream"]
    cfg = store.current

    requested = request.args.get("monitor")
    monitor_index = cfg.monitor_index
    if requested is not None:
        try:
            monitor_index = as_int(requested, 0, 16, monitor_index)
        except Exception:
            pass

    try:
        monitor = resolve_monitor(monitor_index)
    except Exception as exc:
        return {"error": f"no display available: {exc}"}, 503

    try:
        with open_capture() as sct:
            shot = sct.grab(
                {
                    "left": monitor.left,
                    "top": monitor.top,
                    "width": monitor.width,
                    "height": monitor.height,
                }
            )
    except Exception as exc:
        logger.warning("snapshot failed: %s", exc)
        return {"error": f"capture failed: {exc}"}, 503

    from PIL import Image

    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    image.thumbnail((cfg.stream_width, cfg.stream_height), Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)

    return Response(
        buffer.getvalue(),
        mimetype="image/jpeg",
        headers={
            "Content-Disposition": f'inline; filename="softkvm-{monitor.index}.jpg"',
            "Cache-Control": "no-store",
        },
    )
