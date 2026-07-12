"""Dumb panel widgets and pure formatters. No widget shells out or reads files."""

from __future__ import annotations

from rich.markup import escape
from textual.containers import VerticalScroll
from textual.widgets import Static

from .collectors.network import NetworkFast, NetworkSlow
from .collectors.services import JournalTail, ProcessInfo, ServicesSnapshot, UnitStatus
from .collectors.storage import StorageSnapshot
from .collectors.system import SystemFast, SystemStatic, ThrottleFlags

NA = "[dim]n/a[/]"


def bar(percent: float, width: int = 20) -> str:
    pct = max(0.0, min(100.0, percent))
    filled = round(pct / 100 * width)
    color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
    return f"[{color}]{'█' * filled}[/]{'░' * (width - filled)} {pct:5.1f}%"


def fmt_bytes(n: int | float | None) -> str:
    if n is None:
        return "n/a"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def fmt_rate(bps: float | None) -> str:
    return "n/a" if bps is None else f"{fmt_bytes(bps)}/s"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def temp_markup(temp_c: float | None) -> str:
    if temp_c is None:
        return NA
    color = "green" if temp_c < 60 else "yellow" if temp_c < 70 else "red"
    return f"[{color}]{temp_c:.1f} °C[/]"


def throttle_markup(flags: ThrottleFlags | None) -> str:
    if flags is None:
        return NA
    problems = []
    if flags.under_voltage:
        problems.append("[red]UNDER-VOLTAGE[/]")
    if flags.throttled:
        problems.append("[red]THROTTLED[/]")
    if flags.freq_capped:
        problems.append("[yellow]FREQ-CAPPED[/]")
    if flags.soft_temp_limit:
        problems.append("[yellow]SOFT-TEMP-LIMIT[/]")
    past = [
        label
        for flag, label in (
            (flags.past_under_voltage, "under-voltage"),
            (flags.past_throttled, "throttled"),
            (flags.past_freq_capped, "freq-capped"),
            (flags.past_soft_temp_limit, "soft-temp-limit"),
        )
        if flag
    ]
    out = " ".join(problems) if problems else "[green]OK[/]"
    if past:
        out += f"  [dim](past: {', '.join(past)})[/]"
    return out


def _state_markup(unit: UnitStatus | None, name: str) -> str:
    if unit is None:
        return f"{name:<14}{NA}"
    color = {"active": "green", "failed": "red"}.get(unit.active_state, "yellow")
    return f"{name:<14}[{color}]{unit.active_state}[/] ({unit.sub_state})"


class DashPanel(Static):
    """Static panel with a border title; subclasses only render snapshots."""

    TITLE = ""

    def on_mount(self) -> None:
        self.border_title = self.TITLE


class SystemPanel(DashPanel):
    TITLE = "SYSTEM"

    def __init__(self) -> None:
        super().__init__("collecting…")
        self._static: SystemStatic | None = None
        self._fast: SystemFast | None = None
        self._throttle: ThrottleFlags | None = None

    def update_fast(self, static: SystemStatic, fast: SystemFast) -> None:
        self._static, self._fast = static, fast
        self._render_panel()

    def update_throttle(self, flags: ThrottleFlags | None) -> None:
        self._throttle = flags
        self._render_panel()

    def _render_panel(self) -> None:
        st, f = self._static, self._fast
        if st is None or f is None:
            return
        lines = [f"CPU    {bar(f.cpu_percent)}"]
        lines += [f" core{i} {bar(c, 14)}" for i, c in enumerate(f.per_core)]
        load = (
            f"{f.load_avg[0]:.2f} {f.load_avg[1]:.2f} {f.load_avg[2]:.2f}"
            if f.load_avg
            else "n/a"
        )
        lines += [
            f"Load   {load}  ({st.core_count} cores)",
            f"Freq   {f.freq_mhz:.0f} MHz" if f.freq_mhz else f"Freq   {NA}",
            f"Temp   {temp_markup(f.temp_c)}",
            f"Power  {throttle_markup(self._throttle)}",
            f"RAM    {bar(f.mem_percent)}  {fmt_bytes(f.mem_used)} / {fmt_bytes(f.mem_total)}",
            f"Swap   {bar(f.swap_percent)}  {fmt_bytes(f.swap_used)} / {fmt_bytes(f.swap_total)}",
            "",
            f"Model  {escape(st.model) if st.model else 'n/a'}",
            f"OS     {escape(st.os_name or 'n/a')}",
            f"Kernel {escape(st.kernel or 'n/a')}",
        ]
        self.update("\n".join(lines))


class NetworkPanel(DashPanel):
    TITLE = "NETWORK"

    def __init__(self) -> None:
        super().__init__("collecting…")
        self._fast: NetworkFast | None = None
        self._slow: NetworkSlow | None = None

    def update_fast(self, fast: NetworkFast) -> None:
        self._fast = fast
        self._render_panel()

    def update_slow(self, slow: NetworkSlow) -> None:
        self._slow = slow
        self._render_panel()

    def _render_panel(self) -> None:
        f = self._fast
        if f is None:
            return
        lines = []
        for iface in f.interfaces:
            dot = "[green]●[/]" if iface.is_up else "[red]○[/]"
            lines.append(
                f"{dot} {escape(iface.name):<8} {iface.ipv4 or NA}"
                f"  [dim]{iface.mac or ''}[/]"
            )
        s = self._slow
        if s is not None:
            if s.wifi:
                sig = f"{s.wifi.signal_dbm} dBm" if s.wifi.signal_dbm is not None else "n/a"
                rate = (
                    f"{s.wifi.bitrate_mbps:.0f} Mbit/s"
                    if s.wifi.bitrate_mbps is not None
                    else "n/a"
                )
                lines.append(f"Wi-Fi  {escape(s.wifi.ssid)}  {sig}  {rate}")
            else:
                lines.append(f"Wi-Fi  {NA}")
            lines.append(f"GW     {s.gateway or 'n/a'}")
            inet = (
                NA
                if s.internet_ok is None
                else ("[green]reachable[/]" if s.internet_ok else "[red]unreachable[/]")
            )
            lines.append(f"Net    {inet}")
        lines += [
            "",
            f"RX     {fmt_rate(f.rx_rate_bps):<12} total {fmt_bytes(f.rx_total)}",
            f"TX     {fmt_rate(f.tx_rate_bps):<12} total {fmt_bytes(f.tx_total)}",
        ]
        self.update("\n".join(lines))


class StoragePanel(DashPanel):
    TITLE = "STORAGE"

    def __init__(self) -> None:
        super().__init__("collecting…")

    def update_storage(self, snap: StorageSnapshot) -> None:
        lines = []
        for d in snap.disks:
            lines.append(f"{escape(d.mount)}")
            lines.append(
                f"  {bar(d.percent, 16)}  {fmt_bytes(d.used)} / {fmt_bytes(d.total)}"
            )
        m = snap.milo
        lines += [
            "",
            "MILO data",
            f"  ~/.milo      {fmt_bytes(m.milo_dir_bytes)}",
            f"  graph.db     {fmt_bytes(m.graph_db_bytes)}",
            f"  policy.onnx  {fmt_bytes(m.policy_bytes) if m.policy_bytes is not None else '[yellow]missing[/]'}",
        ]
        self.update("\n".join(lines))


class ServicesPanel(DashPanel):
    TITLE = "SERVICES & ROBOT"

    def __init__(self) -> None:
        super().__init__("collecting…")
        self._snap: ServicesSnapshot | None = None
        self._procs: tuple[ProcessInfo, ...] = ()

    def update_services(self, snap: ServicesSnapshot) -> None:
        self._snap = snap
        self._render_panel()

    def update_procs(self, procs: tuple[ProcessInfo, ...]) -> None:
        self._procs = procs
        self._render_panel()

    def _render_panel(self) -> None:
        lines = []
        s = self._snap
        if s is not None:
            lines.append(_state_markup(s.bridge, "milo-bridge"))
            if s.bridge:
                pid = s.bridge.pid or "-"
                restarts = s.bridge.n_restarts if s.bridge.n_restarts is not None else "-"
                lines.append(f"  pid {pid}  restarts {restarts}")
                if s.bridge.since:
                    lines.append(f"  since {escape(s.bridge.since)}")
            if s.bridge_proc:
                lines.append(
                    f"  cpu {s.bridge_proc.cpu_percent:.1f}%  rss {fmt_bytes(s.bridge_proc.rss_bytes)}"
                )
            for unit in s.extras:
                if unit is not None:
                    lines.append(_state_markup(unit, unit.name.removesuffix(".service")))
            hw = s.hardware

            def yn(ok: bool) -> str:
                return "[green]yes[/]" if ok else "[red]no[/]"

            lines += [
                "",
                f"i2c-1 {yn(hw.i2c)}   camera {yn(hw.camera)}   voicehat {yn(hw.voicehat)}",
            ]
        if self._procs:
            lines += ["", "Top processes (cpu)"]
            for p in self._procs:
                lines.append(
                    f"  {p.pid:>6} {escape(p.name)[:18]:<18} {p.cpu_percent:5.1f}%  {p.mem_percent:4.1f}%"
                )
        if lines:
            self.update("\n".join(lines))


class JournalPanel(VerticalScroll):
    def compose(self):
        yield Static("collecting…", id="journal-text")

    def on_mount(self) -> None:
        self.border_title = "MILO-BRIDGE LOG"

    def update_journal(self, tail: JournalTail) -> None:
        body = self.query_one("#journal-text", Static)
        if tail.error:
            body.update(f"[yellow]{escape(tail.error)}[/]")
        elif tail.lines:
            body.update("\n".join(escape(line) for line in tail.lines))
        else:
            body.update("[dim]no journal lines[/]")
        self.scroll_end(animate=False)
