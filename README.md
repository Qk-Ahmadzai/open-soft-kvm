# SoftKVM

A browser-based KVM (keyboard, video, mouse) and system dashboard for your own
machine, styled after [CasaOS](https://www.casaos.io/): a wallpaper-backed glass
dashboard with live telemetry, remote input control and system switches.

Run it on the machine you want to control, open the URL from any phone or
browser on the same network, and you have a remote desktop plus a full system
dashboard.

---

## Features

**Live KVM**
- Sub-second MJPEG stream of any attached display, with a live quality/FPS/size control
- Multi-monitor support - switch the target display at runtime
- Absolute pointer mapping that stays correct even when the stream is letterboxed
- Left / right / double click, drag, and scroll wheel forwarding
- Full keyboard forwarding, plus an on-screen keyboard with function keys and modifiers
- Mobile soft-keyboard bridge (type on your phone, it lands on the host)
- One-tap PNG/JPEG snapshot download
- Bidirectional clipboard sync with the host

**Dashboard**
- CPU, memory, storage and network gauges with live sparklines
- 3-minute rolling graphs for CPU, RAM and throughput (sampled server-side)
- Per-volume disk usage, per-interface network detail, temperatures and fans
- Top-process table with an end-task action (protected PIDs are refused)
- Host details: OS, CPU model, GPU, boot time, uptime, LAN addresses

**Controls**
- Wi-Fi on/off, volume up/down/mute, lock workstation, display standby, suspend
- Restart / shutdown with a typed confirmation and a cancel-pending-shutdown escape hatch
- Wake-on-LAN magic packets
- Quick-launch for whitelisted applications
- Scheduled actions (reboot, shutdown, lock, suspend, WoL) that survive restarts

**Interface**
- CasaOS-inspired glass panels over nine bundled wallpapers, light and dark themes
- Installable as a PWA - add it to your phone's home screen
- Responsive from 320px phones up to wide desktops, with a bottom tab bar on mobile
- Command palette (`Ctrl`/`Cmd` + `K`), toast notifications, keyboard-accessible controls

**Production**
- Password authentication (PBKDF2-SHA256), signed stateless sessions, login throttling
- Rate limiting, CSP and hardening headers, input validation on every endpoint
- Full audit trail of every privileged action, persisted to `audit.jsonl`
- `waitress` WSGI server, structured rotating logs, graceful shutdown
- Runs as a systemd user service on Linux and a scheduled task or service on Windows

---

## Requirements

- Python 3.10 or newer
- Windows, Linux (X11 or Wayland) or macOS
- A desktop session for screen capture and input injection

## Installation

```bash
git clone <your-repo> softkvm
cd softkvm
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
.venv/bin/python server.py                     # Windows: .venv\Scripts\python server.py
```

On first run a random password is generated and printed to the console:

```
  SoftKVM 2.0.0
  ----------------------------------------------------
  Reachable on this network:
    Wi-Fi                http://192.168.1.42:3699
  Not reachable remotely (link-local / loopback):
    Ethernet             169.254.232.7
    Local Area Connection* 1  169.254.242.186
  ----------------------------------------------------
  Access password: kQ3mZx7pTn2w
  Change it under Settings, or with: python server.py --reset-password
  ----------------------------------------------------
```

The password is shown **only** on the run that creates it. If you ever lose it:

```bash
python server.py --reset-password          # generate a new one and print it
python server.py --set-password "my pass"  # set a specific one (8+ characters)
```

Both work against a **running** server - no restart needed.

> The `169.254.x.x` addresses shown under "Not reachable remotely" are Windows'
> link-local addresses, auto-assigned to virtual adapters (Hyper-V, WSL,
> VirtualBox, Bluetooth). They cannot be reached from another machine, so
> SoftKVM never advertises them as usable.

## Configuration

All settings live in `data/config.json` and can be overridden with environment
variables:

| Variable | Default | Purpose |
|---|---|---|
| `SOFTKVM_HOST` | `0.0.0.0` | Bind address |
| `SOFTKVM_PORT` | `3699` | Listen port |
| `SOFTKVM_PASSWORD` | *(generated)* | First-run password |
| `SOFTKVM_DATA_DIR` | `./data` | Config, logs and audit trail |
| `SOFTKVM_REQUIRE_AUTH` | `true` | `false` disables the login screen |
| `SOFTKVM_THEME` | `dark` | `dark` or `light` |
| `SOFTKVM_WALLPAPER` | `night-sky` | Wallpaper id |
| `SOFTKVM_MONITOR` | `1` | Display index to stream |
| `SOFTKVM_FPS` | `15` | Stream frame rate |
| `SOFTKVM_QUALITY` | `60` | JPEG quality (10-95) |
| `SOFTKVM_POLL_MS` | `2000` | Telemetry refresh interval |
| `SOFTKVM_DEBUG` | `false` | Use the Flask dev server |

## Running as a service

See **[deploy/README.md](deploy/README.md)** for the full guide.

**Linux**

```bash
./deploy/install-linux.sh            # user service - recommended for a KVM
./deploy/install-linux.sh --system   # system service - telemetry only
```

**Windows**

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1            # scheduled task
powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Mode Service
```

**Diagnosing a Linux install**

```bash
./deploy/check.sh
```

It checks python, the virtualenv, the dependencies, the systemd unit, the
service state, the listening port, the graphical session and the config, then
prints PASS/FAIL for each and the exact commands to follow up on whatever
failed.

> **Important:** screen capture and mouse/keyboard injection need access to the
> interactive desktop. A Windows Service and a Linux *system* service both run
> without one (session 0), so the dashboard and telemetry work but the screen
> stream does not. Use the scheduled task (Windows) or the *user* service
> (Linux) for the full feature set. On a headless Linux box you can still run
> the dashboard against a virtual display:
>
> ```bash
> sudo apt-get install -y xvfb
> Xvfb :99 -screen 0 1920x1080x24 &
> export DISPLAY=:99
> ```

## API

All endpoints require a session cookie unless noted.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Liveness probe (no auth) |
| `GET` | `/api/metrics` | Full telemetry snapshot |
| `GET` | `/api/history` | Rolling graph series |
| `GET` | `/api/processes` | Top processes |
| `GET` | `/api/system` | Host identity |
| `GET` | `/api/monitors` | Attached displays |
| `GET` | `/api/info` | Service metadata and reachable URLs |
| `GET` | `/api/audit` | Recent privileged actions |
| `GET` | `/api/settings` | Current settings |
| `POST` | `/api/settings` | Update settings |
| `POST` | `/api/settings/password` | Change the access password |
| `POST` | `/api/input` | Pointer / keyboard / text events |
| `POST` | `/api/action` | Host control actions |
| `POST` | `/api/launch` | Launch a whitelisted application |
| `POST` | `/api/kill` | Terminate a process |
| `GET`/`POST` | `/api/clipboard` | Read / write the host clipboard |
| `GET`/`POST` | `/api/schedule` | List / create scheduled tasks |
| `DELETE` | `/api/schedule/<id>` | Remove a scheduled task |
| `POST` | `/api/monitor` | Change the streamed display |
| `GET` | `/stream` | MJPEG live stream |
| `GET` | `/snapshot` | Single JPEG frame |

## Layout

```
softkvm/
├── auth.py           password hashing, sessions, login throttling
├── capture.py        shared screen capture and MJPEG fan-out
├── config.py         settings model and atomic JSON persistence
├── inputctl.py       pointer / keyboard / clipboard control
├── logging_utils.py  logging setup
├── metrics.py        psutil collectors and history buffers
├── scheduler.py      scheduled host actions
├── security.py       headers, CSP, rate limiting, validation
├── systemctl.py      power, network, audio, apps, WoL
├── audit.py          action journal
├── routes/           page, API and stream blueprints
├── static/           CSS, JS, wallpapers, icons, PWA manifest
└── templates/        Jinja templates
deploy/               systemd units and Windows installers
tests/                pytest suite
tools/                icon generation and maintenance scripts
```

## Development

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests -q      # 80 tests
.venv/bin/python server.py               # dev server on :3699
```

Regenerate the PWA icons after changing the brand:

```bash
.venv/bin/python tools/make_icons.py
```

## Security notes

- Change the default password before exposing the server beyond a trusted LAN.
- Authentication can be disabled with `SOFTKVM_REQUIRE_AUTH=false`; only do this
  on a network you fully control.
- The server binds `0.0.0.0` by default so phones on the same Wi-Fi can reach
  it. Set `SOFTKVM_HOST=127.0.0.1` to keep it local-only.
- Every privileged action is recorded with the client address in
  `data/audit.jsonl`.

## Credits

Wallpapers are from [Unsplash](https://unsplash.com) under the Unsplash
License. The visual design follows the [CasaOS](https://www.casaos.io/)
dashboard and its open-source UI.
