"""Telemetry collector tests."""

from __future__ import annotations

from softkvm.metrics import (
    MetricsCollector,
    MetricsHistory,
    cpu_snapshot,
    disk_snapshot,
    human_bytes,
    human_rate,
    human_uptime,
    memory_snapshot,
    network_snapshot,
    processes_snapshot,
    sensors_snapshot,
    system_snapshot,
)
from softkvm.metrics import NetworkTracker


def test_human_bytes_formats():
    assert human_bytes(0) == "0.0 B"
    assert human_bytes(1024) == "1.0 KB"
    assert human_bytes(1024**3) == "1.0 GB"
    assert human_bytes(None) == "-"


def test_human_rate_formats():
    assert human_rate(512) == "512 B/s"
    assert human_rate(2048) == "2.0 KB/s"
    assert human_rate(5 * 1024**2) == "5.0 MB/s"
    assert human_rate(None) == "-"


def test_human_uptime_formats():
    assert human_uptime(30) == "0m"
    assert human_uptime(3600) == "1h 0m"
    assert "d" in human_uptime(90000)


def test_cpu_snapshot_shape():
    cpu = cpu_snapshot()
    assert 0 <= cpu["percent"] <= 100
    assert cpu["logical_cores"] >= 1
    assert isinstance(cpu["per_core"], list)


def test_memory_snapshot_shape():
    memory = memory_snapshot()
    assert memory["total"] > 0
    assert 0 <= memory["percent"] <= 100
    assert "swap_used_human" in memory


def test_disk_snapshot_lists_partitions():
    disk = disk_snapshot()
    assert disk["total"] > 0
    assert isinstance(disk["partitions"], list)
    for part in disk["partitions"]:
        assert "mountpoint" in part
        assert "percent" in part


def test_network_snapshot_shape():
    tracker = NetworkTracker()
    network = network_snapshot(tracker)
    assert network["up_bytes_per_sec"] >= 0
    assert network["down_bytes_per_sec"] >= 0
    assert isinstance(network["interfaces"], list)


def test_sensors_snapshot_never_raises():
    sensors = sensors_snapshot()
    assert isinstance(sensors["temperatures"], list)
    assert isinstance(sensors["fans"], list)


def test_processes_snapshot_sorted_and_limited():
    payload = processes_snapshot(limit=5, sort_by="cpu")
    assert len(payload["processes"]) <= 5
    values = [proc["cpu"] for proc in payload["processes"]]
    assert values == sorted(values, reverse=True)


def test_system_snapshot_shape():
    system = system_snapshot()
    assert system["hostname"]
    assert system["uptime_seconds"] >= 0
    assert "uptime_human" in system


def test_history_ring_buffer():
    history = MetricsHistory(maxlen=5)
    for index in range(10):
        history.cpu.push(index)
    series = history.cpu.series()
    assert len(series["v"]) == 5
    assert series["v"] == [5, 6, 7, 8, 9]


def test_history_limit():
    history = MetricsHistory(maxlen=100)
    for index in range(50):
        history.cpu.push(index)
    assert len(history.cpu.series(limit=10)["v"]) == 10


def test_collector_caches_payload():
    collector = MetricsCollector()
    first = collector.collect()
    second = collector.collect()
    assert first is second  # cached within the TTL


def test_collector_force_refresh():
    collector = MetricsCollector()
    collector.collect()
    refreshed = collector.collect(force=True)
    assert "cpu" in refreshed
