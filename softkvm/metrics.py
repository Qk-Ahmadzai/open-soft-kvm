"""System telemetry collectors built on psutil.

All collectors are defensive: a host that does not expose a sensor, a
partition that cannot be queried or a process that dies mid-scan must never
take the dashboard down. Failures degrade to ``None`` / empty lists.
"""

from __future__ import annotations

import platform
import socket
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Iterable

import psutil

GB = 1024**3
MB = 1024**2


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------
def human_bytes(value: float | None) -> str:
    if value is None:
        return "-"
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def human_rate(bps: float | None) -> str:
    if bps is None:
        return "-"
    bps = float(bps)
    if bps >= 1024**2:
        return f"{bps / 1024**2:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def human_uptime(seconds: float) -> str:
    delta = timedelta(seconds=int(seconds))
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes = delta.seconds % 3600 // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ---------------------------------------------------------------------------
# history ring buffer
# ---------------------------------------------------------------------------
class History:
    """Thread-safe ring buffer of (timestamp, value) samples."""

    def __init__(self, maxlen: int = 120) -> None:
        self._data: deque[tuple[float, float]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def push(self, value: float) -> None:
        with self._lock:
            self._data.append((time.time(), float(value)))

    def series(self, limit: int | None = None) -> dict[str, list[float]]:
        with self._lock:
            rows = list(self._data)
        if limit is not None:
            rows = rows[-limit:]
        return {
            "t": [round(ts, 2) for ts, _ in rows],
            "v": [round(val, 3) for _, val in rows],
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class MetricsHistory:
    """All tracked series in one place."""

    def __init__(self, maxlen: int = 120) -> None:
        self.cpu = History(maxlen)
        self.ram = History(maxlen)
        self.net_down = History(maxlen)
        self.net_up = History(maxlen)
        self.disk = History(maxlen)

    def snapshot(self, limit: int | None = None) -> dict[str, Any]:
        return {
            "cpu": self.cpu.series(limit),
            "ram": self.ram.series(limit),
            "net_down": self.net_down.series(limit),
            "net_up": self.net_up.series(limit),
        }


# ---------------------------------------------------------------------------
# network speed tracking
# ---------------------------------------------------------------------------
class NetworkTracker:
    """Tracks aggregate throughput between consecutive calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev = psutil.net_io_counters()
        self._prev_time = time.time()

    def sample(self) -> dict[str, Any]:
        with self._lock:
            current = psutil.net_io_counters()
            now = time.time()
            elapsed = max(now - self._prev_time, 0.05)
            up = (current.bytes_sent - self._prev.bytes_sent) / elapsed
            down = (current.bytes_recv - self._prev.bytes_recv) / elapsed
            self._prev = current
            self._prev_time = now
        return {
            "up_bytes_per_sec": round(max(up, 0.0), 2),
            "down_bytes_per_sec": round(max(down, 0.0), 2),
            "up_human": human_rate(max(up, 0.0)),
            "down_human": human_rate(max(down, 0.0)),
            "total_sent": current.bytes_sent,
            "total_recv": current.bytes_recv,
            "elapsed": round(elapsed, 3),
        }


# ---------------------------------------------------------------------------
# collectors
# ---------------------------------------------------------------------------
def cpu_snapshot() -> dict[str, Any]:
    logical = psutil.cpu_count(logical=True) or 0
    physical = psutil.cpu_count(logical=False) or logical
    freq = None
    try:
        cpu_freq = psutil.cpu_freq()
        if cpu_freq is not None:
            freq = round(cpu_freq.current / 1000, 2)
    except Exception:
        freq = None

    per_core: list[float] = []
    try:
        per_core = [round(v, 1) for v in psutil.cpu_percent(interval=None, percpu=True)]
    except Exception:
        per_core = []

    model = platform.processor() or ""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if "model name" in line:
                    model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass

    return {
        "percent": round(psutil.cpu_percent(interval=None), 1),
        "per_core": per_core,
        "logical_cores": logical,
        "physical_cores": physical,
        "freq_ghz": freq,
        "model": model or platform.machine(),
    }


def memory_snapshot() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    payload: dict[str, Any] = {
        "percent": round(vm.percent, 1),
        "total": vm.total,
        "used": vm.used,
        "available": vm.available,
        "total_human": human_bytes(vm.total),
        "used_human": human_bytes(vm.used),
        "available_human": human_bytes(vm.available),
    }
    try:
        swap = psutil.swap_memory()
        payload.update(
            {
                "swap_percent": round(swap.percent, 1),
                "swap_total": swap.total,
                "swap_used": swap.used,
                "swap_used_human": human_bytes(swap.used),
                "swap_total_human": human_bytes(swap.total),
            }
        )
    except Exception:
        payload.update(
            {
                "swap_percent": 0.0,
                "swap_total": 0,
                "swap_used": 0,
                "swap_used_human": "-",
                "swap_total_human": "-",
            }
        )
    return payload


def disk_snapshot() -> dict[str, Any]:
    """Primary disk plus every mounted partition we can read."""
    partitions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (OSError, PermissionError):
            continue
        if usage.total == 0:
            continue
        key = part.mountpoint
        if key in seen:
            continue
        seen.add(key)
        partitions.append(
            {
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": round(usage.percent, 1),
                "total_human": human_bytes(usage.total),
                "used_human": human_bytes(usage.used),
                "free_human": human_bytes(usage.free),
            }
        )

    root = psutil.disk_usage("/" if platform.system() != "Windows" else "C:\\")
    return {
        "percent": round(root.percent, 1),
        "total": root.total,
        "used": root.used,
        "free": root.free,
        "total_human": human_bytes(root.total),
        "used_human": human_bytes(root.used),
        "free_human": human_bytes(root.free),
        "partitions": partitions,
    }


def network_snapshot(tracker: NetworkTracker) -> dict[str, Any]:
    aggregate = tracker.sample()
    interfaces: list[dict[str, Any]] = []
    counters = psutil.net_io_counters(pernic=True)
    addresses = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    for name, counter in counters.items():
        addresses_for_iface = addresses.get(name, [])
        ipv4 = next(
            (a.address for a in addresses_for_iface if a.family == socket.AF_INET),
            None,
        )
        ipv6 = next(
            (a.address for a in addresses_for_iface if a.family == socket.AF_INET6),
            None,
        )
        iface_stats = stats.get(name)
        interfaces.append(
            {
                "name": name,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "mac": next(
                    (a.address for a in addresses_for_iface if a.family == psutil.AF_LINK),
                    None,
                ),
                "up": bool(iface_stats.isup) if iface_stats else True,
                "speed_mbps": iface_stats.speed if iface_stats else 0,
                "sent": counter.bytes_sent,
                "recv": counter.bytes_recv,
                "sent_human": human_bytes(counter.bytes_sent),
                "recv_human": human_bytes(counter.bytes_recv),
            }
        )

    return {
        "up_bytes_per_sec": aggregate["up_bytes_per_sec"],
        "down_bytes_per_sec": aggregate["down_bytes_per_sec"],
        "up_human": aggregate["up_human"],
        "down_human": aggregate["down_human"],
        "total_sent": aggregate["total_sent"],
        "total_recv": aggregate["total_recv"],
        "interfaces": interfaces,
        "host_ips": _host_addresses(),
    }


def _host_addresses() -> list[str]:
    """Best-effort list of this host's usable IPv4 addresses.

    Link-local (169.254.x.x) addresses are excluded: Windows assigns one to
    every virtual adapter (Hyper-V, VirtualBox, WSL, VPN filters) and they
    are not reachable from other machines, so listing them only creates
    confusion.
    """
    found: set[str] = set()
    for _, addresses in psutil.net_if_addrs().items():
        for addr in addresses:
            if addr.family == socket.AF_INET and addr.address:
                if _is_link_local(addr.address):
                    continue
                found.add(addr.address)
    try:
        primary = socket.gethostbyname(socket.gethostname())
        if not _is_link_local(primary):
            found.add(primary)
    except OSError:
        pass
    found.discard("127.0.0.1")
    return sorted(found)


def _is_link_local(address: str) -> bool:
    """True for 169.254.0.0/16 (APIPA / link-local) addresses."""
    return address.startswith("169.254.")


def interface_addresses() -> list[dict[str, str]]:
    """Every interface with its IPv4 address, flagged by usefulness.

    Used by the startup banner and the settings page so the operator can see
    *which* adapter an address belongs to instead of guessing.
    """
    rows: list[dict[str, str]] = []
    stats = psutil.net_if_stats()
    for name, addresses in psutil.net_if_addrs().items():
        ipv4 = next((a.address for a in addresses if a.family == socket.AF_INET), None)
        if not ipv4:
            continue
        iface_stats = stats.get(name)
        up = bool(iface_stats.isup) if iface_stats else True
        link_local = _is_link_local(ipv4)
        if link_local:
            kind = "virtual"
        elif ipv4.startswith("127."):
            kind = "loopback"
        else:
            kind = "lan"
        rows.append(
            {
                "name": name,
                "address": ipv4,
                "up": "up" if up else "down",
                "kind": kind,
                "usable": kind == "lan" and up,
            }
        )
    rows.sort(key=lambda row: (not row["usable"], row["name"]))
    return rows


def sensors_snapshot() -> dict[str, Any]:
    """Temperatures, fans and battery - many hosts expose none of these."""
    temperatures: list[dict[str, Any]] = []
    fans: list[dict[str, Any]] = []
    battery: dict[str, Any] | None = None

    try:
        for name, entries in (psutil.sensors_temperatures() or {}).items():
            for entry in entries:
                if entry.current is None:
                    continue
                temperatures.append(
                    {
                        "name": entry.label or name,
                        "group": name,
                        "current": round(entry.current, 1),
                        "high": round(entry.high, 1) if entry.high else None,
                        "critical": round(entry.critical, 1) if entry.critical else None,
                    }
                )
    except (AttributeError, OSError):
        pass

    try:
        for name, entries in (psutil.sensors_fans() or {}).items():
            for entry in entries:
                if entry.current is None:
                    continue
                fans.append({"name": name, "rpm": int(entry.current)})
    except (AttributeError, OSError):
        pass

    try:
        raw = psutil.sensors_battery()
        if raw is not None:
            battery = {
                "percent": round(raw.percent, 1),
                "plugged": raw.power_plugged,
                "secs_left": raw.secsleft if raw.secsleft is not None and raw.secsleft >= 0 else None,
            }
    except (AttributeError, OSError):
        pass

    return {"temperatures": temperatures, "fans": fans, "battery": battery}


def processes_snapshot(limit: int = 12, sort_by: str = "cpu") -> dict[str, Any]:
    """Top processes by CPU or memory usage."""
    rows: list[dict[str, Any]] = []
    key = "memory" if sort_by == "memory" else "cpu"

    for proc in psutil.process_iter(
        ["pid", "name", "username", "cpu_percent", "memory_percent", "status", "create_time"]
    ):
        try:
            info = proc.info
            if info["pid"] == 0:
                continue
            rows.append(
                {
                    "pid": info["pid"],
                    "name": info.get("name") or "Unknown",
                    "username": info.get("username") or "-",
                    "cpu": round(info.get("cpu_percent") or 0.0, 1),
                    "memory": round(info.get("memory_percent") or 0.0, 1),
                    "status": info.get("status") or "-",
                    "started": _format_start(info.get("create_time")),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    rows.sort(key=lambda row: row[key], reverse=True)
    total = len(rows)
    return {"processes": rows[:limit], "total": total}


def _format_start(create_time: float | None) -> str | None:
    if not create_time:
        return None
    try:
        return datetime.fromtimestamp(create_time).strftime("%H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return None


def system_snapshot() -> dict[str, Any]:
    """Static-ish host identity: OS, hardware, boot time."""
    boot = psutil.boot_time()
    uptime = max(time.time() - boot, 0)
    try:
        cpu_model = cpu_snapshot()["model"]
    except Exception:
        cpu_model = platform.processor() or platform.machine()

    gpu = _detect_gpu()
    return {
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.release()}",
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_model": cpu_model,
        "gpu": gpu,
        "boot_time": datetime.fromtimestamp(boot).isoformat(timespec="seconds"),
        "uptime_seconds": int(uptime),
        "uptime_human": human_uptime(uptime),
        "virtualization": _detect_virtualization(),
    }


def _detect_virtualization() -> str | None:
    try:
        return psutil.virtualization().get("system") or None
    except Exception:
        return None


def _detect_gpu() -> str | None:
    """Best-effort GPU name - Windows exposes it via WMI."""
    if platform.system() != "Windows":
        return None
    try:
        import subprocess

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return names[0] if names else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# aggregator
# ---------------------------------------------------------------------------
class MetricsCollector:
    """Collects the full dashboard payload with a short-lived cache."""

    def __init__(self, history: MetricsHistory | None = None) -> None:
        self.history = history or MetricsHistory()
        self.tracker = NetworkTracker()
        self._cache: dict[str, Any] = {}
        self._cache_time = 0.0
        self._cache_ttl = 0.9
        self._lock = threading.Lock()
        self._system: dict[str, Any] | None = None

    def collect(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            fresh = (time.time() - self._cache_time) < self._cache_ttl
            if fresh and not force and self._cache:
                return self._cache

            payload = {
                "cpu": cpu_snapshot(),
                "memory": memory_snapshot(),
                "disk": disk_snapshot(),
                "network": network_snapshot(self.tracker),
                "sensors": sensors_snapshot(),
                "system": self._system_info(),
            }
            self._cache = payload
            # Stamp *after* the build: collecting can take longer than the TTL
            # (the first call runs a WMI query), and a pre-build stamp would
            # make the cache look stale the instant it was written.
            self._cache_time = time.time()
            return payload

    def _system_info(self) -> dict[str, Any]:
        if self._system is None:
            try:
                self._system = system_snapshot()
            except Exception:
                self._system = {"hostname": socket.gethostname(), "uptime_human": "-"}
        return self._system

    def sample_history(self) -> None:
        """Push one sample into every history series (called by the sampler)."""
        payload = self.collect()
        self.history.cpu.push(payload["cpu"]["percent"])
        self.history.ram.push(payload["memory"]["percent"])
        self.history.net_down.push(payload["network"]["down_bytes_per_sec"])
        self.history.net_up.push(payload["network"]["up_bytes_per_sec"])
        self.history.disk.push(payload["disk"]["percent"])


class MetricsSampler(threading.Thread):
    """Background thread that keeps the history buffer warm."""

    def __init__(self, collector: MetricsCollector, interval: float = 2.0) -> None:
        super().__init__(name="softkvm-sampler", daemon=True)
        self.collector = collector
        self.interval = interval
        self._stop = threading.Event()

    def run(self) -> None:  # pragma: no cover - timing dependent
        while not self._stop.wait(self.interval):
            try:
                self.collector.sample_history()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
