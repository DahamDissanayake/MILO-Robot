"""Network stats: interfaces, Wi-Fi link, gateway, RX/TX rates, reachability."""

from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass

import psutil

from . import run_cmd

WIFI_IFACE = "wlan0"


@dataclass(frozen=True)
class Interface:
    name: str
    is_up: bool
    ipv4: str | None
    mac: str | None


@dataclass(frozen=True)
class WifiLink:
    ssid: str
    signal_dbm: int | None
    bitrate_mbps: float | None


def parse_iw_link(text: str) -> WifiLink | None:
    if not text or text.strip().startswith("Not connected"):
        return None
    m = re.search(r"SSID:\s*(.+)", text)
    if m is None:
        return None
    ssid = m.group(1).strip()
    m = re.search(r"signal:\s*(-?\d+)\s*dBm", text)
    signal = int(m.group(1)) if m else None
    m = re.search(r"tx bitrate:\s*([\d.]+)\s*MBit/s", text)
    bitrate = float(m.group(1)) if m else None
    return WifiLink(ssid=ssid, signal_dbm=signal, bitrate_mbps=bitrate)


def parse_default_route(text: str) -> str | None:
    m = re.search(r"default via (\S+)", text or "")
    return m.group(1) if m else None


class RateTracker:
    """Turns two successive byte-counter readings into bytes/sec rates."""

    def __init__(self) -> None:
        self._last: tuple[float, int, int] | None = None

    def update(
        self, rx_total: int, tx_total: int, now: float | None = None
    ) -> tuple[float, float]:
        now = time.monotonic() if now is None else now
        rates = (0.0, 0.0)
        if self._last is not None:
            t0, rx0, tx0 = self._last
            dt = now - t0
            if dt > 0 and rx_total >= rx0 and tx_total >= tx0:
                rates = ((rx_total - rx0) / dt, (tx_total - tx0) / dt)
        self._last = (now, rx_total, tx_total)
        return rates


@dataclass(frozen=True)
class NetworkFast:
    interfaces: tuple[Interface, ...]
    rx_rate_bps: float
    tx_rate_bps: float
    rx_total: int
    tx_total: int


def collect_fast(tracker: RateTracker) -> NetworkFast:
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    interfaces = []
    for name in sorted(addrs):
        if name == "lo" or name.lower().startswith("loopback"):
            continue
        ipv4 = mac = None
        for a in addrs[name]:
            if a.family == socket.AF_INET:
                ipv4 = a.address
            elif a.family == psutil.AF_LINK:
                mac = a.address
        st = stats.get(name)
        interfaces.append(
            Interface(name=name, is_up=bool(st and st.isup), ipv4=ipv4, mac=mac)
        )
    io = psutil.net_io_counters()
    rx_rate, tx_rate = tracker.update(io.bytes_recv, io.bytes_sent)
    return NetworkFast(
        interfaces=tuple(interfaces),
        rx_rate_bps=rx_rate,
        tx_rate_bps=tx_rate,
        rx_total=io.bytes_recv,
        tx_total=io.bytes_sent,
    )


@dataclass(frozen=True)
class NetworkSlow:
    wifi: WifiLink | None
    gateway: str | None
    internet_ok: bool | None


def check_internet(host: str = "1.1.1.1", port: int = 53, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def collect_slow() -> NetworkSlow:
    return NetworkSlow(
        wifi=parse_iw_link(run_cmd(["iw", "dev", WIFI_IFACE, "link"]) or ""),
        gateway=parse_default_route(run_cmd(["ip", "route", "show", "default"]) or ""),
        internet_ok=check_internet(),
    )
