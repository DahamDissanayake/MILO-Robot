"""--check mode: collect everything once and print a plain-text report.

Useful over a bare SSH session and for verifying collectors on any machine.
"""

from __future__ import annotations

from .collectors import network, services, storage, system
from .widgets import fmt_bytes, fmt_duration, fmt_rate


def _opt(value, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value}{suffix}"


def render_report() -> str:
    st = system.collect_static()
    fast = system.collect_fast()
    throttle = system.collect_throttle()
    net_fast = network.collect_fast(network.RateTracker())
    net_slow = network.collect_slow()
    stor = storage.collect()
    svc = services.collect_slow()
    procs = services.top_processes()

    lines = [
        f"== SYSTEM ({st.hostname}) ==",
        f"model: {_opt(st.model)} | os: {_opt(st.os_name)} | kernel: {_opt(st.kernel)}",
        f"CPU {fast.cpu_percent:.1f}% | load {_opt(fast.load_avg)} | freq {_opt(fast.freq_mhz, ' MHz')}",
        f"temp {_opt(fast.temp_c, ' C')} | throttle {'n/a' if throttle is None else throttle}",
        f"mem {fmt_bytes(fast.mem_used)}/{fmt_bytes(fast.mem_total)} ({fast.mem_percent:.0f}%)"
        f" | swap {fast.swap_percent:.0f}% | up {fmt_duration(fast.uptime_s)}",
        "",
        "== NETWORK ==",
    ]
    for i in net_fast.interfaces:
        lines.append(f"{'UP ' if i.is_up else 'DOWN'} {i.name}: {_opt(i.ipv4)} [{_opt(i.mac)}]")
    lines += [
        f"wifi: {net_slow.wifi} | gw: {_opt(net_slow.gateway)} | internet: {_opt(net_slow.internet_ok)}",
        f"rx {fmt_rate(net_fast.rx_rate_bps)} (total {fmt_bytes(net_fast.rx_total)})"
        f" | tx {fmt_rate(net_fast.tx_rate_bps)} (total {fmt_bytes(net_fast.tx_total)})",
        "",
        "== STORAGE ==",
    ]
    for d in stor.disks:
        lines.append(f"{d.mount}: {fmt_bytes(d.used)}/{fmt_bytes(d.total)} ({d.percent:.0f}%)")
    m = stor.milo
    lines += [
        f"~/.milo {fmt_bytes(m.milo_dir_bytes)} | graph.db {fmt_bytes(m.graph_db_bytes)}"
        f" | policy.onnx {fmt_bytes(m.policy_bytes)}",
        "",
        "== SERVICES ==",
        f"milo-bridge: {svc.bridge}",
        f"bridge proc: {svc.bridge_proc}",
    ]
    for unit in svc.extras:
        lines.append(f"unit: {unit}")
    hw = svc.hardware
    lines += [
        f"hardware: i2c={hw.i2c} camera={hw.camera} voicehat={hw.voicehat}",
        f"journal: {svc.journal.error or f'{len(svc.journal.lines)} lines'}",
        "top procs: " + ", ".join(f"{p.name}({p.cpu_percent:.0f}%)" for p in procs),
    ]
    return "\n".join(lines)


def run() -> None:
    print(render_report())
