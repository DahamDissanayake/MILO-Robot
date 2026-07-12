"""Servos screen: TC1 full-range sweep / TC2 return-to-zero, per servo."""

from __future__ import annotations

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from milo_bridge.drivers.servos import SERVO_CHANNELS, ServoDriver

from iot_tester.results_log import ResultRecorder
from iot_tester.widgets import ask_pass_fail

SWEEP_UP_ANGLES = (0, 45, 90, 135, 180)
SWEEP_DOWN_ANGLES = (180, 90, 0)
STEP_DELAY_S = 0.5


async def run_sweep(
    driver: ServoDriver, servo: str, angles: tuple[int, ...], step_delay_s: float = STEP_DELAY_S
) -> None:
    for angle in angles:
        driver.set_angle(servo, angle)
        await asyncio.sleep(step_delay_s)


class ServoScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Servos must be powered from the battery/5A rail, NEVER the Pi's 5V. "
            "Keep the robot on a stand, clear of obstructions.",
            classes="warning",
        )
        yield Button("Start Servo Tests", id="start-btn", variant="primary")
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start-btn":
            event.button.disabled = True
            self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        try:
            driver = ServoDriver.from_hardware()
        except Exception as exc:
            await container.mount(Static(f"Could not open the PCA9685: {exc}"))
            return

        for name in SERVO_CHANNELS:
            await run_sweep(driver, name, SWEEP_UP_ANGLES)
            passed, note = await ask_pass_fail(
                container, f"{name}: did it sweep smoothly through its full range?"
            )
            self.recorder.record(f"Servo {name}", "TC1 Full range sweep", passed, note)
            self.recorder.flush()

            await run_sweep(driver, name, SWEEP_DOWN_ANGLES)
            passed, note = await ask_pass_fail(
                container, f"{name}: did it return cleanly to 0 degrees?"
            )
            self.recorder.record(f"Servo {name}", "TC2 Return to zero", passed, note)
            self.recorder.flush()

        driver.relax()
        await container.mount(Static("Servo tests complete. Press Escape to return to menu."))
