"""Host control: power, network, audio, application launch and Wake-on-LAN.

Every external command is executed from an explicit argument list with
``shell=False`` - no request-controlled string ever reaches a shell.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Any

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

#: PIDs that must never be killed through the dashboard.
PROTECTED_PIDS = {0, 1, 2, 3, 4, 5}

#: Applications that can be launched from the dashboard.
APPLICATIONS: dict[str, dict[str, Any]] = {
    "taskmgr": {
        "label": "Task Manager",
        "icon": "activity",
        "command": ["taskmgr.exe"],
        "platforms": ["Windows"],
    },
    "cmd": {
        "label": "Command Prompt",
        "icon": "terminal",
        "command": ["cmd.exe", "/c", "start", "cmd.exe"],
        "platforms": ["Windows"],
    },
    "powershell": {
        "label": "PowerShell",
        "icon": "terminal",
        "command": ["powershell.exe", "-NoExit"],
        "platforms": ["Windows"],
    },
    "calc": {
        "label": "Calculator",
        "icon": "calculator",
        "command": ["calc.exe"],
        "platforms": ["Windows"],
    },
    "notepad": {
        "label": "Notepad",
        "icon": "file",
        "command": ["notepad.exe"],
        "platforms": ["Windows"],
    },
    "explorer": {
        "label": "File Explorer",
        "icon": "folder",
        "command": ["explorer.exe"],
        "platforms": ["Windows"],
    },
    "settings": {
        "label": "Settings",
        "icon": "settings",
        "command": ["ms-settings:"],
        "platforms": ["Windows"],
    },
    "terminal": {
        "label": "Terminal",
        "icon": "terminal",
        "command": ["gnome-terminal"],
        "platforms": ["Linux"],
    },
    "files": {
        "label": "Files",
        "icon": "folder",
        "command": ["nautilus"],
        "platforms": ["Linux"],
    },
}


def _run(command: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess:
    """Run a command without a shell and swallow platform errors."""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


class SystemController:
    """High-level host actions exposed to the dashboard."""

    def __init__(self) -> None:
        self.platform = platform.system()

    # -- power ------------------------------------------------------
    def lock(self) -> dict[str, Any]:
        if IS_WINDOWS:
            _run(["rundll32.exe", "user32.dll,LockWorkStation"])
        elif IS_MACOS:
            _run(["pmset", "displaysleepnow"])
            _run(["/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession", "-suspend"])
        else:
            _run(["loginctl", "lock-session"])
            _run(["xdg-screensaver", "lock"])
        return {"action": "lock", "ok": True, "message": "Workstation locked"}

    def sleep_display(self) -> dict[str, Any]:
        if IS_WINDOWS:
            _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Add-Type '[DllImport(\"user32.dll\")]public static extern int SendMessage(int hWnd, int hMsg, int wParam, int lParam);' -Name a -Pas)::SendMessage(-1, 0x0112, 0xF170, 2)",
                ],
                timeout=12.0,
            )
        elif IS_MACOS:
            _run(["pmset", "displaysleepnow"])
        else:
            _run(["xset", "dpms", "force", "off"])
        return {"action": "sleep_display", "ok": True, "message": "Display sent to standby"}

    def reboot(self, delay: int = 3) -> dict[str, Any]:
        if IS_WINDOWS:
            _run(["shutdown", "/r", "/t", str(int(delay))])
        elif IS_MACOS:
            _run(["shutdown", "-r", "now"])
        else:
            _run(["systemctl", "reboot"])
        return {"action": "reboot", "ok": True, "message": f"Reboot scheduled in {delay}s"}

    def shutdown(self, delay: int = 3) -> dict[str, Any]:
        if IS_WINDOWS:
            _run(["shutdown", "/s", "/t", str(int(delay))])
        elif IS_MACOS:
            _run(["shutdown", "-h", "now"])
        else:
            _run(["systemctl", "poweroff"])
        return {"action": "shutdown", "ok": True, "message": f"Shutdown scheduled in {delay}s"}

    def cancel_shutdown(self) -> dict[str, Any]:
        if IS_WINDOWS:
            _run(["shutdown", "/a"])
        else:
            _run(["systemctl", "cancel"])
        return {"action": "cancel_shutdown", "ok": True, "message": "Pending shutdown cancelled"}

    def suspend(self) -> dict[str, Any]:
        if IS_WINDOWS:
            _run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        elif IS_MACOS:
            _run(["pmset", "sleepnow"])
        else:
            _run(["systemctl", "suspend"])
        return {"action": "suspend", "ok": True, "message": "Host suspended"}

    # -- network ----------------------------------------------------
    def wifi(self, state: str) -> dict[str, Any]:
        """Enable or disable the wireless adapter."""
        if state not in {"on", "off"}:
            raise ValueError("wifi state must be 'on' or 'off'")
        admin = "enabled" if state == "on" else "disabled"

        if IS_WINDOWS:
            interface = self._windows_wifi_interface()
            if interface:
                _run(["netsh", "interface", "set", "interface", f"name={interface}", f"admin={admin}"])
            else:
                _run(["netsh", "interface", "set", "interface", "name=Wi-Fi", f"admin={admin}"])
        elif IS_MACOS:
            device = self._macos_wifi_device()
            if device:
                _run(["networksetup", "-setairportpower", device, "on" if state == "on" else "off"])
        else:
            _run(["nmcli", "radio", "wifi", admin])
        return {"action": f"wifi_{state}", "ok": True, "message": f"Wi-Fi turned {state.upper()}"}

    def _windows_wifi_interface(self) -> str | None:
        try:
            result = _run(["netsh", "interface", "show", "interfaces"], timeout=6)
        except (OSError, subprocess.SubprocessError):
            return None
        for line in result.stdout.splitlines():
            if "Wireless" in line or "Wi-Fi" in line:
                parts = line.split()
                if len(parts) >= 4:
                    return parts[3]
        return None

    def _macos_wifi_device(self) -> str | None:
        try:
            result = _run(
                ["networksetup", "-listallhardwareports"], timeout=6
            )
        except (OSError, subprocess.SubprocessError):
            return None
        current = None
        for line in result.stdout.splitlines():
            if line.startswith("Hardware Port:"):
                current = line.split(":", 1)[1].strip()
            if line.startswith("Device:") and current and "Wi-Fi" in current:
                return line.split(":", 1)[1].strip()
        return None

    def wifi_status(self) -> dict[str, Any]:
        """Report the current state of the wireless adapter."""
        if IS_WINDOWS:
            try:
                result = _run(["netsh", "interface", "show", "interface"], timeout=6)
                for line in result.stdout.splitlines():
                    if line.strip().startswith("State"):
                        state = line.split(":", 1)[1].strip().lower()
                        return {"supported": True, "state": state, "enabled": state == "connected"}
            except (OSError, subprocess.SubprocessError):
                pass
            return {"supported": True, "state": "unknown", "enabled": None}
        if IS_MACOS:
            device = self._macos_wifi_device()
            if device:
                try:
                    result = _run(["networksetup", "-getairportpower", device], timeout=6)
                    on = "on" in result.stdout.lower()
                    return {"supported": True, "state": "on" if on else "off", "enabled": on}
                except (OSError, subprocess.SubprocessError):
                    pass
        if shutil.which("nmcli"):
            try:
                result = _run(["nmcli", "radio", "wifi"], timeout=6)
                enabled = "enabled" in result.stdout.lower()
                return {"supported": True, "state": "on" if enabled else "off", "enabled": enabled}
            except (OSError, subprocess.SubprocessError):
                pass
        return {"supported": False, "state": "unknown", "enabled": None}

    # -- audio ------------------------------------------------------
    def volume(self, direction: str) -> dict[str, Any]:
        """Adjust or toggle the master volume."""
        try:
            from pynput.keyboard import Controller, Key

            keyboard = Controller()
            media_key = {
                "up": Key.media_volume_up,
                "down": Key.media_volume_down,
                "mute": Key.media_volume_mute,
            }[direction]
            keyboard.press(media_key)
            keyboard.release(media_key)
        except Exception as exc:
            return {"action": f"volume_{direction}", "ok": False, "message": f"Volume control failed: {exc}"}
        labels = {"up": "Volume increased", "down": "Volume decreased", "mute": "Mute toggled"}
        return {"action": f"volume_{direction}", "ok": True, "message": labels[direction]}

    # -- applications -----------------------------------------------
    def available_applications(self) -> list[dict[str, Any]]:
        out = []
        for key, spec in APPLICATIONS.items():
            if self.platform not in spec["platforms"]:
                continue
            out.append({"id": key, "label": spec["label"], "icon": spec["icon"]})
        return out

    def launch(self, app_id: str) -> dict[str, Any]:
        spec = APPLICATIONS.get(app_id)
        if spec is None:
            raise ValueError(f"unknown application '{app_id}'")
        if self.platform not in spec["platforms"]:
            raise ValueError(f"'{spec['label']}' is not available on {self.platform}")
        try:
            subprocess.Popen(
                spec["command"],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, ValueError) as exc:
            return {"action": "launch", "ok": False, "message": f"Could not launch {spec['label']}: {exc}"}
        return {"action": "launch", "ok": True, "message": f"Launched {spec['label']}"}

    # -- processes --------------------------------------------------
    def kill_process(self, pid: int) -> dict[str, Any]:
        """Terminate a process, refusing protected and self targets."""
        import psutil

        pid = int(pid)
        if pid in PROTECTED_PIDS:
            raise PermissionError(f"PID {pid} is protected and cannot be terminated")
        try:
            target = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return {"action": "kill", "ok": False, "message": f"No process with PID {pid}"}

        if pid == os.getpid():
            raise PermissionError("Refusing to terminate the SoftKVM server itself")

        name = target.name()
        protected_names = {"system", "systemd", "explorer.exe", "wininit.exe", "csrss.exe", "services.exe"}
        if name.lower() in protected_names:
            raise PermissionError(f"'{name}' is a protected system process")

        try:
            target.terminate()
            try:
                target.wait(timeout=3)
            except psutil.TimeoutExpired:
                target.kill()
        except psutil.AccessDenied:
            raise PermissionError(f"Access denied while terminating PID {pid}")
        except psutil.NoSuchProcess:
            pass
        return {"action": "kill", "ok": True, "message": f"Terminated {name} (PID {pid})"}

    # -- wake on lan ------------------------------------------------
    def wake_on_lan(self, mac_address: str) -> dict[str, Any]:
        """Send a Wake-on-LAN magic packet to a MAC address."""
        cleaned = mac_address.replace(":", "").replace("-", "").replace(" ", "")
        if len(cleaned) != 12 or any(c not in "0123456789abcdefABCDEF" for c in cleaned):
            raise ValueError("MAC address must look like AA:BB:CC:DD:EE:FF")

        payload = bytes.fromhex(cleaned)
        magic = b"\xff" * 6 + payload * 16

        sent = 0
        for broadcast in ("255.255.255.255", "127.0.0.1"):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.sendto(magic, (broadcast, 9))
                sent += 1
            except OSError:
                continue
        if not sent:
            raise OSError("could not send the magic packet on any interface")
        return {
            "action": "wake_on_lan",
            "ok": True,
            "message": f"Magic packet sent to {mac_address.upper()}",
        }

    # -- host listing -----------------------------------------------
    def hostname(self) -> str:
        return platform.node() or socket.gethostname()
