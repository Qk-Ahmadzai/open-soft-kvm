"""Screen capture and MJPEG streaming.

The original implementation opened a fresh ``mss`` handle per HTTP request and
never closed it, so every reconnect leaked a capture context and a thread.
Here a single producer thread owns the capture for a given monitor and fans
encoded JPEG frames out to any number of subscribers:

* one capture + one encode per monitor, no matter how many browsers watch;
* bounded per-subscriber queues - a slow client drops frames instead of
  stalling the producer or exhausting memory;
* subscribers are removed as soon as their request context goes away.
"""

from __future__ import annotations

import io
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

import mss
from PIL import Image

BOUNDARY = "frame"


def open_capture():
    """Open a capture context, preferring the current mss API.

    ``mss.mss()`` is deprecated in mss 10.x in favour of ``mss.MSS()``; the
    fallback keeps older installs working.
    """
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


@dataclass
class MonitorInfo:
    """A single display known to the capture backend."""

    index: int
    left: int
    top: int
    width: int
    height: int
    is_primary: bool

    @property
    def label(self) -> str:
        return f"Display {self.index}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "is_primary": self.is_primary,
            "label": self.label,
            "aspect": round(self.width / self.height, 3) if self.height else 1.777,
        }


class CaptureError(RuntimeError):
    """Raised when a frame cannot be captured or encoded."""


def list_monitors() -> list[MonitorInfo]:
    """Enumerate every attached display."""
    with open_capture() as sct:
        monitors = sct.monitors
        out: list[MonitorInfo] = []
        for index, mon in enumerate(monitors):
            if index == 0:  # index 0 is the virtual "all monitors" union
                continue
            out.append(
                MonitorInfo(
                    index=index,
                    left=mon["left"],
                    top=mon["top"],
                    width=mon["width"],
                    height=mon["height"],
                    is_primary=index == 1,
                )
            )
    return out


def resolve_monitor(index: int) -> MonitorInfo:
    """Return the monitor at ``index``, falling back to the primary display."""
    monitors = list_monitors()
    if not monitors:
        raise CaptureError("no display detected")
    for monitor in monitors:
        if monitor.index == index:
            return monitor
    return monitors[0]


class ScreenStreamer:
    """Producer thread that captures one monitor and fans out JPEG frames."""

    def __init__(
        self,
        monitor_index: int,
        width: int = 1280,
        height: int = 720,
        fps: int = 15,
        quality: int = 60,
    ) -> None:
        self.monitor_index = monitor_index
        self.width = max(320, int(width))
        self.height = max(240, int(height))
        self.fps = max(1, min(int(fps), 60))
        self.quality = max(10, min(int(quality), 95))

        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_error: str | None = None
        self.frames_produced = 0
        # Stop capturing shortly after the last viewer disconnects, so an
        # abandoned KVM tab does not keep burning CPU.
        self.idle_timeout = 15.0

    # -- lifecycle --------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"softkvm-capture-{self.monitor_index}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
        with self._lock:
            for sub in list(self._subscribers):
                self._drop_oldest(sub)
                try:
                    sub.put_nowait(None)
                except queue.Full:
                    pass
            self._subscribers.clear()

    def reconfigure(self, width: int, height: int, fps: int, quality: int) -> None:
        """Apply new stream settings and restart the producer."""
        self.width = max(320, int(width))
        self.height = max(240, int(height))
        self.fps = max(1, min(int(fps), 60))
        self.quality = max(10, min(int(quality), 95))
        self.stop()
        self.start()

    # -- subscriber management --------------------------------------
    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=2)
        with self._lock:
            self._subscribers.add(sub)
        self.start()
        return sub

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @staticmethod
    def _drop_oldest(sub: queue.Queue) -> None:
        try:
            sub.get_nowait()
        except queue.Empty:
            pass

    # -- producer ---------------------------------------------------
    def _run(self) -> None:
        try:
            sct = open_capture()
        except Exception as exc:  # pragma: no cover - platform dependent
            self._last_error = f"capture unavailable: {exc}"
            return

        try:
            monitor = resolve_monitor(self.monitor_index)
            region = {
                "left": monitor.left,
                "top": monitor.top,
                "width": monitor.width,
                "height": monitor.height,
            }
            interval = 1.0 / self.fps
            next_frame = time.time()

            idle_since: float | None = None
            while not self._stop.is_set():
                if self.subscriber_count == 0:
                    if idle_since is None:
                        idle_since = time.time()
                    elif time.time() - idle_since > self.idle_timeout:
                        break
                else:
                    idle_since = None

                try:
                    shot = sct.grab(region)
                    frame = self._encode(shot)
                    self.frames_produced += 1
                    self._publish(frame)
                except Exception as exc:  # keep the stream alive on transient errors
                    self._last_error = str(exc)
                    time.sleep(0.2)

                next_frame += interval
                delay = next_frame - time.time()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -1.0:
                    # We fell far behind (heavy load); resync instead of spinning.
                    next_frame = time.time()
        finally:
            try:
                sct.close()
            except Exception:
                pass

    def _encode(self, shot) -> bytes:
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        image.thumbnail((self.width, self.height), Image.Resampling.BILINEAR)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=self.quality, optimize=False)
        return buffer.getvalue()

    def _publish(self, frame: bytes) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for sub in subscribers:
            try:
                sub.put_nowait(frame)
            except queue.Full:
                # Slow consumer: drop the oldest frame and keep the newest.
                self._drop_oldest(sub)
                try:
                    sub.put_nowait(frame)
                except queue.Full:
                    pass


class StreamManager:
    """Owns one :class:`ScreenStreamer` per monitor index."""

    def __init__(self, config_store) -> None:
        self._config_store = config_store
        self._streamers: dict[int, ScreenStreamer] = {}
        self._lock = threading.Lock()

    def streamer_for(self, monitor_index: int) -> ScreenStreamer:
        cfg = self._config_store.current
        with self._lock:
            streamer = self._streamers.get(monitor_index)
            if streamer is None:
                streamer = ScreenStreamer(
                    monitor_index=monitor_index,
                    width=cfg.stream_width,
                    height=cfg.stream_height,
                    fps=cfg.stream_fps,
                    quality=cfg.stream_quality,
                )
                self._streamers[monitor_index] = streamer
            return streamer

    def apply_settings(self) -> None:
        """Propagate config changes to every running streamer."""
        cfg = self._config_store.current
        with self._lock:
            streamers = list(self._streamers.values())
        for streamer in streamers:
            streamer.reconfigure(
                cfg.stream_width, cfg.stream_height, cfg.stream_fps, cfg.stream_quality
            )

    def shutdown(self) -> None:
        with self._lock:
            streamers = list(self._streamers.values())
            self._streamers.clear()
        for streamer in streamers:
            streamer.stop()


def multipart_stream(sub: queue.Queue, unsubscribe) -> Iterator[bytes]:
    """Yield an MJPEG multipart body from a subscriber queue."""
    try:
        while True:
            frame = sub.get()
            if frame is None:
                break
            yield (
                b"--" + BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
            )
    except GeneratorExit:
        raise
    finally:
        unsubscribe()
