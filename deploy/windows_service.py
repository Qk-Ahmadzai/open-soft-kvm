"""Windows Service wrapper for SoftKVM.

Install / uninstall:

    python deploy/windows_service.py install
    python deploy/windows_service.py start
    python deploy/windows_service.py remove

IMPORTANT
---------
A Windows Service runs in session 0, which has **no interactive desktop**.
Screen capture and mouse/keyboard injection therefore do not work from a
Service. For a KVM you want the *interactive* task instead:

    powershell -ExecutionPolicy Bypass -File deploy/install-windows.ps1 -Mode Task

This Service is still useful for headless telemetry on machines with no
signed-in session (or for the dashboard without the KVM features).
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SERVICE_NAME = "SoftKVM"
SERVICE_DISPLAY_NAME = "SoftKVM - Browser KVM and System Dashboard"
SERVICE_DESCRIPTION = (
    "Web dashboard for monitoring and remotely controlling this machine. "
    "Runs as a Windows Service (session 0) - screen control is unavailable; "
    "use the scheduled-task install for full KVM functionality."
)

try:
    import servicemanager
    import win32api
    import win32event
    import win32service
    import win32serviceutil
except ImportError as exc:  # pragma: no cover - Windows only
    print("pywin32 is required:  pip install pywin32", file=sys.stderr)
    raise SystemExit(1) from exc


class SoftKVMService(win32serviceutil.ServiceFramework):  # type: ignore[misc]
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    def __init__(self, args):
        super().__init__(args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._server = None
        self._thread = None

    # -- SCM callbacks -------------------------------------------------
    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop_event)
        if self._server is not None:
            try:
                self._server.close()
                self._server.task_dispatcher.shutdown()
            except Exception:
                pass

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        try:
            self._run()
        except Exception as exc:  # pragma: no cover - defensive
            servicemanager.LogErrorMsg(f"{SERVICE_NAME} failed: {exc}")

    # -- the actual work -----------------------------------------------
    def _run(self):
        from waitress.server import create_server

        from softkvm import create_app
        from softkvm.config import ConfigStore

        store = ConfigStore()
        app = create_app(store)
        cfg = store.current

        self._server = create_server(
            app, host=cfg.host, port=cfg.port, threads=16, channel_timeout=120
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

        # Block until the SCM asks us to stop.
        win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)

        try:
            app.extensions["softkvm_stream"].shutdown()
            app.extensions["softkvm_sampler"].stop()
            app.extensions["softkvm_scheduler"].stop()
        except Exception:
            pass


def main() -> int:
    if len(sys.argv) == 1:
        print(__doc__)
        return 1

    command = sys.argv[1]
    if command == "install":
        # Store the project path and interpreter so the service can find them.
        python = os.path.abspath(sys.executable)
        script = os.path.abspath(__file__)
        servicemanager.PrepareToHostSingle(SERVICE_NAME)
        win32serviceutil.InstallService(
            python,
            SERVICE_NAME,
            SERVICE_DISPLAY_NAME,
            startType=win32service.SERVICE_AUTO_START,
            errorControl=win32service.SERVICE_ERROR_NORMAL,
            startMethod=win32service.SERVICE_WIN32_OWN_PROCESS,
            displayName=SERVICE_DISPLAY_NAME,
            description=SERVICE_DESCRIPTION,
            exe_args=f'"{script}" run',
        )
        print(f"{SERVICE_NAME} installed. Start it with:  net start {SERVICE_NAME}")
        return 0

    if command == "remove":
        win32serviceutil.RemoveService(SERVICE_NAME)
        print(f"{SERVICE_NAME} removed.")
        return 0

    if command == "start":
        servicemanager.StartService(SERVICE_NAME)
        return 0

    if command == "stop":
        servicemanager.StopService(SERVICE_NAME)
        return 0

    if command == "run":
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(SERVICE_NAME)
        servicemanager.StartServiceCtrlDispatcher(SERVICE_NAME, SoftKVMService)
        return 0

    if command == "debug":
        # Run in the foreground so you can see the log output.
        service = SoftKVMService(None)
        service._run()
        return 0

    print(f"Unknown command: {command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
