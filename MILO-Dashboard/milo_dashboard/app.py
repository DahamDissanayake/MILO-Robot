"""MILO Dashboard Textual app: layout, refresh timers, keybindings."""

from __future__ import annotations

import sys
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static

from .collectors import network, services, storage, system
from .widgets import (
    JournalPanel,
    NetworkPanel,
    ServicesPanel,
    StoragePanel,
    SystemPanel,
    fmt_duration,
)

FAST_INTERVAL_S = 2.0
SLOW_INTERVAL_S = 10.0


class TopBar(Static):
    def update_bar(self, hostname: str, uptime_s: float) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        self.update(
            f"[b]MILO DASHBOARD[/b]  ·  {hostname}  ·  up {fmt_duration(uptime_s)}  ·  {now}"
        )


class MiloDashApp(App):
    TITLE = "MILO Dashboard"
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]
    CSS = """
    TopBar {
        dock: top;
        height: 1;
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
    }
    #row-top { height: auto; }
    #row-bottom { height: 1fr; }
    SystemPanel, NetworkPanel, StoragePanel {
        border: round $primary;
        padding: 0 1;
        width: 1fr;
        height: auto;
    }
    ServicesPanel, JournalPanel {
        border: round $primary;
        padding: 0 1;
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.static_info = system.collect_static()
        self.rate_tracker = network.RateTracker()

    def compose(self) -> ComposeResult:
        yield TopBar()
        with Vertical():
            with Horizontal(id="row-top"):
                yield SystemPanel()
                yield NetworkPanel()
                yield StoragePanel()
            with Horizontal(id="row-bottom"):
                yield ServicesPanel()
                yield JournalPanel()
        yield Footer()

    def on_mount(self) -> None:
        self._tick_fast()
        self._tick_slow()
        self.set_interval(FAST_INTERVAL_S, self._tick_fast)
        self.set_interval(SLOW_INTERVAL_S, self._tick_slow)

    def _tick_fast(self) -> None:
        self.run_worker(self._collect_fast, thread=True, exclusive=True, group="fast")

    def _tick_slow(self) -> None:
        self.run_worker(self._collect_slow, thread=True, exclusive=True, group="slow")

    def _collect_fast(self) -> None:
        sys_fast = system.collect_fast()
        net_fast = network.collect_fast(self.rate_tracker)
        procs = services.top_processes()
        self.call_from_thread(self._apply_fast, sys_fast, net_fast, procs)

    def _apply_fast(self, sys_fast, net_fast, procs) -> None:
        self.query_one(TopBar).update_bar(self.static_info.hostname, sys_fast.uptime_s)
        self.query_one(SystemPanel).update_fast(self.static_info, sys_fast)
        self.query_one(NetworkPanel).update_fast(net_fast)
        self.query_one(ServicesPanel).update_procs(procs)

    def _collect_slow(self) -> None:
        throttle = system.collect_throttle()
        net_slow = network.collect_slow()
        stor = storage.collect()
        svc = services.collect_slow()
        self.call_from_thread(self._apply_slow, throttle, net_slow, stor, svc)

    def _apply_slow(self, throttle, net_slow, stor, svc) -> None:
        self.query_one(SystemPanel).update_throttle(throttle)
        self.query_one(NetworkPanel).update_slow(net_slow)
        self.query_one(StoragePanel).update_storage(stor)
        self.query_one(ServicesPanel).update_services(svc)
        self.query_one(JournalPanel).update_journal(svc.journal)

    def action_refresh(self) -> None:
        self._tick_fast()
        self._tick_slow()


def main() -> None:
    if "--check" in sys.argv:
        from . import check

        check.run()
        return
    MiloDashApp().run()
