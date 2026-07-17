"""IMU screen: gyro calibration + live roll/pitch/gyro tracking via Mpu6050."""

from __future__ import annotations

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from milo_bridge.drivers.imu import Mpu6050

from iot_tester.results_log import ResultRecorder
from iot_tester.widgets import ask_pass_fail

LIVE_UPDATE_INTERVAL_S = 0.1


def format_readout(roll: float, pitch: float, gyro: tuple[float, float, float]) -> str:
    return (
        f"roll={roll:6.1f} deg  pitch={pitch:6.1f} deg  "
        f"gyro={gyro[0]:6.1f},{gyro[1]:6.1f},{gyro[2]:6.1f} deg/s"
    )


class ImuScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="imu-readout")
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_mount(self) -> None:
        self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        readout = self.query_one("#imu-readout", Static)
        try:
            imu = await asyncio.to_thread(Mpu6050.from_hardware)
        except Exception as exc:
            await container.mount(Static(f"Could not open the IMU: {exc}"))
            return

        await container.mount(Static("Calibrating gyro -- keep the robot still..."))
        try:
            await asyncio.to_thread(imu.calibrate_gyro)
            self.recorder.record("IMU", "Gyro calibration", True)
        except Exception as exc:
            self.recorder.record("IMU", "Gyro calibration", False, note=str(exc))
        self.recorder.flush()

        await container.mount(
            Static("Live tracking -- tilt the robot forward/back/side-to-side")
        )
        stop_event = asyncio.Event()

        async def update_loop() -> None:
            while not stop_event.is_set():
                state = await asyncio.to_thread(imu.update)
                readout.update(format_readout(state.roll, state.pitch, state.gyro))
                await asyncio.sleep(LIVE_UPDATE_INTERVAL_S)

        updater = asyncio.create_task(update_loop())
        passed, note = await ask_pass_fail(
            container, "Did roll/pitch respond correctly as you tilted the robot?"
        )
        stop_event.set()
        await updater
        self.recorder.record("IMU", "Live tracking", passed, note)
        self.recorder.flush()

        await container.mount(Static("IMU tests complete. Press Escape to return to menu."))
