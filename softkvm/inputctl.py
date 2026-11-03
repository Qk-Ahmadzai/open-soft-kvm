"""Pointer, keyboard and clipboard control for the remote session.

All coordinates arriving from the browser are normalised (0.0 - 1.0) against
the *target monitor*, so a click lands in the right place even when the
streamed image is letterboxed or scaled to fit the viewport.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

IS_WINDOWS = platform.system() == "Windows"

try:
    from pynput.keyboard import Controller as KeyboardController, Key
    from pynput.mouse import Button, Controller as MouseController

    INPUT_AVAILABLE = True
except Exception:  # pragma: no cover - pynput needs a display server
    Button = Key = None  # type: ignore[assignment]
    MouseController = KeyboardController = None  # type: ignore[assignment]
    INPUT_AVAILABLE = False


SPECIAL_KEYS: dict[str, Any] = {
    "Enter": "enter",
    "Return": "enter",
    "Backspace": "backspace",
    "Tab": "tab",
    "Space": "space",
    "Esc": "esc",
    "Escape": "esc",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "ArrowUp": "up",
    "ArrowDown": "down",
    "ArrowLeft": "left",
    "ArrowRight": "right",
    "Home": "home",
    "End": "end",
    "PageUp": "page_up",
    "PageDown": "page_down",
    "Delete": "delete",
    "Insert": "insert",
    "CapsLock": "caps_lock",
    "NumLock": "num_lock",
    "ScrollLock": "scroll_lock",
    "PrintScreen": "print_screen",
    "Pause": "pause",
    "Menu": "menu",
    "Shift": "shift",
    "Control": "ctrl",
    "Ctrl": "ctrl",
    "Alt": "alt",
    "Super": "cmd",
    "Win": "cmd",
    "Command": "cmd",
}
for _i in range(1, 21):
    SPECIAL_KEYS[f"F{_i}"] = f"f{_i}"

MODIFIER_KEYS = {"ctrl", "alt", "shift", "cmd"}

BUTTONS = {"left": "left", "right": "right", "middle": "middle"}


class InputUnavailable(RuntimeError):
    """Raised when pynput cannot talk to the desktop session."""


class InputController:
    """Serialised access to the mouse and keyboard."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mouse = None
        self._keyboard = None
        self._error: str | None = None
        if INPUT_AVAILABLE:
            try:
                self._mouse = MouseController()
                self._keyboard = KeyboardController()
            except Exception as exc:
                self._error = str(exc)
        else:
            self._error = "pynput is not available in this environment"

    @property
    def available(self) -> bool:
        return self._mouse is not None and self._keyboard is not None

    @property
    def error(self) -> str | None:
        return self._error

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "error": self._error,
            "platform": platform.system(),
            "backend": "pynput" if self.available else "unavailable",
        }

    # -- pointer ----------------------------------------------------
    def move(self, monitor, x: float, y: float) -> None:
        """Move the pointer to normalised ``(x, y)`` on ``monitor``."""
        if not self.available:
            raise InputUnavailable(self._error or "input backend unavailable")
        with self._lock:
            target_x = monitor.left + int(float(x) * monitor.width)
            target_y = monitor.top + int(float(y) * monitor.height)
            self._mouse.position = (target_x, target_y)

    def click(self, monitor, x: float, y: float, button: str = "left", count: int = 1) -> None:
        if not self.available:
            raise InputUnavailable(self._error or "input backend unavailable")
        name = BUTTONS.get(button, "left")
        btn = getattr(Button, name)
        with self._lock:
            target_x = monitor.left + int(float(x) * monitor.width)
            target_y = monitor.top + int(float(y) * monitor.height)
            self._mouse.position = (target_x, target_y)
            time.sleep(0.01)  # let the pointer settle before the click lands
            for _ in range(max(1, min(int(count), 3))):
                self._mouse.click(btn)
                if count > 1:
                    time.sleep(0.06)

    def drag(self, monitor, x1: float, y1: float, x2: float, y2: float, button: str = "left") -> None:
        """Press, move, release - used for touch dragging on mobile."""
        if not self.available:
            raise InputUnavailable(self._error or "input backend unavailable")
        name = BUTTONS.get(button, "left")
        btn = getattr(Button, name)
        with self._lock:
            self._mouse.position = (
                monitor.left + int(float(x1) * monitor.width),
                monitor.top + int(float(y1) * monitor.height),
            )
            self._mouse.press(btn)
            try:
                steps = 8
                for step in range(1, steps + 1):
                    ratio = step / steps
                    self._mouse.position = (
                        monitor.left + int((float(x1) + (float(x2) - float(x1)) * ratio) * monitor.width),
                        monitor.top + int((float(y1) + (float(y2) - float(y1)) * ratio) * monitor.height),
                    )
                    time.sleep(0.012)
            finally:
                self._mouse.release(btn)

    def scroll(self, dx: int, dy: int) -> None:
        if not self.available:
            raise InputUnavailable(self._error or "input backend unavailable")
        with self._lock:
            self._mouse.scroll(int(dx), int(dy))

    # -- keyboard ---------------------------------------------------
    def _resolve_key(self, raw: str):
        name = SPECIAL_KEYS.get(raw)
        if name is None:
            return None
        if not hasattr(Key, name):
            return None
        return getattr(Key, name)

    def press_key(self, raw: str, modifiers: list[str] | None = None) -> bool:
        """Press (and release) a single key, optionally with modifiers."""
        if not self.available:
            raise InputUnavailable(self._error or "input backend unavailable")

        key = self._resolve_key(raw)
        modifier_keys = []
        for mod in modifiers or []:
            resolved = self._resolve_key(mod.capitalize()) or self._resolve_key(mod)
            if resolved is not None:
                modifier_keys.append(resolved)

        with self._lock:
            for mod in modifier_keys:
                self._keyboard.press(mod)
            try:
                if key is not None:
                    self._keyboard.press(key)
                    self._keyboard.release(key)
                elif len(raw) == 1:
                    self._keyboard.press(raw)
                    self._keyboard.release(raw)
                else:
                    return False
            finally:
                for mod in reversed(modifier_keys):
                    self._keyboard.release(mod)
        return True

    def type_text(self, text: str) -> int:
        """Type a literal string; returns the number of characters sent."""
        if not self.available:
            raise InputUnavailable(self._error or "input backend unavailable")
        sent = 0
        with self._lock:
            for char in text:
                self._keyboard.press(char)
                self._keyboard.release(char)
                sent += 1
                time.sleep(0.012)
        return sent


# ---------------------------------------------------------------------------
# clipboard
# ---------------------------------------------------------------------------
class Clipboard:
    """Cross-platform clipboard access with graceful degradation."""

    def __init__(self) -> None:
        self.backend = self._detect_backend()
        self.error: str | None = None

    def _detect_backend(self) -> str:
        if IS_WINDOWS:
            try:
                import ctypes  # noqa: F401

                return "windows"
            except Exception:
                return "unavailable"
        if sys.platform == "darwin":
            return "macos" if shutil.which("pbcopy") else "unavailable"
        if shutil.which("xclip"):
            return "xclip"
        if shutil.which("xsel"):
            return "xsel"
        return "unavailable"

    @property
    def available(self) -> bool:
        return self.backend != "unavailable"

    # -- read -------------------------------------------------------
    def read(self) -> str:
        if self.backend == "windows":
            return self._read_windows()
        if self.backend == "macos":
            return self._run_text(["pbpaste"])
        if self.backend == "xclip":
            return self._run_text(["xclip", "-selection", "clipboard", "-o"])
        if self.backend == "xsel":
            return self._run_text(["xsel", "--clipboard", "--output"])
        return ""

    def _read_windows(self) -> str:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13

        if not user32.OpenClipboard(None):
            self.error = "clipboard is locked by another application"
            return ""
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.c_wchar_p(pointer).value or ""
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    # -- write ------------------------------------------------------
    def write(self, text: str) -> bool:
        if self.backend == "windows":
            return self._write_windows(text)
        if self.backend == "macos":
            return self._run_write(["pbcopy"], text)
        if self.backend == "xclip":
            return self._run_write(["xclip", "-selection", "clipboard"], text)
        if self.backend == "xsel":
            return self._run_write(["xsel", "--clipboard", "--input"], text)
        self.error = "no clipboard backend available on this platform"
        return False

    def _write_windows(self, text: str) -> bool:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        if not user32.OpenClipboard(None):
            self.error = "clipboard is locked by another application"
            return False
        try:
            user32.EmptyClipboard()
            size = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not handle:
                self.error = "could not allocate clipboard memory"
                return False
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                self.error = "could not lock clipboard memory"
                return False
            try:
                ctypes.memmove(pointer, ctypes.c_wchar_p(text), size)
            finally:
                kernel32.GlobalUnlock(handle)
            user32.SetClipboardData(CF_UNICODETEXT, handle)
            return True
        finally:
            user32.CloseClipboard()

    # -- process helpers ---------------------------------------------
    def _run_text(self, command: list[str]) -> str:
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=5, check=False, shell=False
            )
            return result.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError) as exc:
            self.error = str(exc)
            return ""

    def _run_write(self, command: list[str], text: str) -> bool:
        try:
            subprocess.run(
                command,
                input=text.encode("utf-8"),
                timeout=5,
                check=False,
                shell=False,
            )
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            self.error = str(exc)
            return False
