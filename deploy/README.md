# Running SoftKVM as a service

This guide covers running SoftKVM in the background on Linux and Windows, with
automatic start on boot and automatic restart on failure.

---

## The one thing to understand first

A KVM needs two different things from the host:

| Capability | Needs the interactive desktop? |
|---|---|
| Dashboard & telemetry | No |
| Screen stream (video) | **Yes** |
| Mouse / keyboard injection | **Yes** |

Windows Services and Linux *system* services both run in **session 0** - a
segregated session with no visible desktop. Screen capture returns a black
frame and synthetic input goes nowhere.

So the choice is:

| Platform | Mode | Screen control |
|---|---|---|
| Linux | systemd **user** service | Yes - recommended |
| Linux | systemd system service | No - telemetry only |
| Windows | Scheduled task (`-Mode Task`) | Yes - recommended |
| Windows | Windows Service (`-Mode Service`) | No - telemetry only |

---

## Linux

### User service (recommended)

Run it inside your graphical session so it can reach the framebuffer and send
input events.

```bash
./deploy/install-linux.sh
```

This will:

1. Create `.venv` and install the dependencies
2. Copy `deploy/softkvm-user.service` to `~/.config/systemd/user/softkvm.service`
3. Enable and start it with `systemctl --user`

Check it:

```bash
systemctl --user status softkvm
journalctl --user -u softkvm -f
```

To keep it running after you log out, enable lingering once:

```bash
sudo loginctl enable-linger $USER
```

Without lingering, `systemctl --user` services are stopped at logout.

#### Wayland

On Wayland, `mss` captures via the XWayland compatibility layer, which can
return a black frame for some compositors. If the stream is black, log in with
an X11 session, or grant the compositor a screen-capture permission
(`wlr-randr`/`grim` based setups vary by compositor).

### System service (telemetry only)

```bash
sudo ./deploy/install-linux.sh --system
```

This installs to `/opt/softkvm` with a dedicated `softkvm` user and data in
`/var/lib/softkvm`.

```bash
systemctl status softkvm
journalctl -u softkvm -f
```

### Manual systemd unit

If you prefer to write the unit yourself, `deploy/softkvm-user.service` is a
working starting point. The important bits are:

```ini
[Service]
ExecStart=%h/softkvm/.venv/bin/python %h/softkvm/server.py
Environment="DISPLAY=:0"
Environment="XAUTHORITY=%h/.Xauthority"
```

Adjust `DISPLAY` to match your session (`echo $DISPLAY`).

---

## Windows

### Scheduled task (recommended)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1
```

The task runs when you log on, in your interactive session, with highest
privileges, and restarts automatically if it crashes.

```powershell
Get-ScheduledTask -TaskName SoftKVM
Stop-ScheduledTask -TaskName SoftKVM
Start-ScheduledTask -TaskName SoftKVM
Unregister-ScheduledTask -TaskName SoftKVM -Confirm:$false
```

Configuration and logs live in `%LOCALAPPDATA%\SoftKVM`.

The first-run password is written to the log:

```powershell
Get-Content "$env:LOCALAPPDATA\SoftKVM\softkvm.log" -Tail 20
```

### Windows Service (telemetry only)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Mode Service
```

This needs `pywin32` and installs a real Windows Service:

```powershell
Get-Service SoftKVM
Start-Service SoftKVM
Stop-Service SoftKVM
```

You can also drive it directly:

```powershell
python deploy\windows_service.py install
python deploy\windows_service.py start
python deploy\windows_service.py remove
python deploy\windows_service.py debug   # run in the foreground to see logs
```

Remember: **session 0 has no desktop**, so the screen stream and input
injection will not work in this mode.

---

## Securing the service

Both platforms bind `0.0.0.0` by default so phones on the same Wi-Fi can reach
the server. To restrict it:

```ini
# systemd
Environment="SOFTKVM_HOST=127.0.0.1"
```

```powershell
# scheduled task - edit the action arguments, or set the env var for the task
$env:SOFTKVM_HOST = "127.0.0.1"
```

For a LAN-only deployment behind a reverse proxy, terminate TLS there and
leave SoftKVM on plain HTTP on the loopback interface.

Always change the password before exposing the server outside a trusted
network:

```bash
journalctl -u softkvm | grep -i password   # read the generated one
```

Then change it under **Settings → Security**, or set `SOFTKVM_PASSWORD` in the
service environment so it is applied on the next start.

---

## Troubleshooting

**The screen stream is black**

The process has no access to the desktop. Check that you are using the *user*
service (Linux) or the scheduled task (Windows), and that `DISPLAY` is correct.

**`mss` fails to open a display**

`mss` needs an X11 or Wayland display. On a headless server, install Xvfb and
point `DISPLAY` at it - telemetry will work, but there is nothing to capture.

**The service starts and immediately stops**

Check the log:

```bash
journalctl --user -u softkvm -n 50          # Linux
Get-Content "$env:LOCALAPPDATA\SoftKVM\softkvm.log" -Tail 50   # Windows
```

A missing dependency or a wrong `WorkingDirectory` is the usual cause.

**Port already in use**

```bash
sudo ss -ltnp | grep 3699                   # Linux
netstat -ano | findstr :3699                # Windows
```

Change the port with `SOFTKVM_PORT` and update any firewall rules.
