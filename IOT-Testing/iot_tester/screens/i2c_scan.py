"""I2C Bus Scan screen: quick health check for the 3 expected devices."""

from __future__ import annotations

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from iot_tester.results_log import ResultRecorder

EXPECTED_DEVICES: dict[int, str] = {
    0x3C: "SSD1306 OLED",
    0x40: "PCA9685 servo driver",
    0x68: "MPU6050 IMU",
}


def scan_i2c_bus(bus, addresses: range = range(0x03, 0x78)) -> dict[int, bool]:
    """bus must expose read_byte(address), raising OSError when nothing responds."""
    found: dict[int, bool] = {}
    for address in addresses:
        try:
            bus.read_byte(address)
            found[address] = True
        except OSError:
            found[address] = False
    return found


class I2cScanScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(
            Static("Scanning I2C bus 1...", id="scan-status"),
            DataTable(id="scan-table"),
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#scan-table", DataTable)
        table.add_columns("Address", "Device", "Found")
        self.run_scan()

    @work()
    async def run_scan(self) -> None:
        status = self.query_one("#scan-status", Static)
        table = self.query_one("#scan-table", DataTable)
        try:
            from smbus2 import SMBus

            bus = await asyncio.to_thread(SMBus, 1)
        except Exception as exc:
            status.update(f"Could not open I2C bus 1: {exc}")
            for name in EXPECTED_DEVICES.values():
                self.recorder.record("I2C Bus Scan", name, False, note=str(exc))
            self.recorder.flush()
            return

        found = await asyncio.to_thread(scan_i2c_bus, bus)
        for address, name in EXPECTED_DEVICES.items():
            present = found.get(address, False)
            table.add_row(f"0x{address:02X}", name, "yes" if present else "NO")
            self.recorder.record("I2C Bus Scan", name, present)
        extra = sorted(addr for addr, ok in found.items() if ok and addr not in EXPECTED_DEVICES)
        for address in extra:
            table.add_row(f"0x{address:02X}", "(unexpected device)", "yes")
        self.recorder.flush()
        status.update("Scan complete.")
