from milo_bridge.drivers.servos import SERVO_CHANNELS, ServoDriver

from iot_tester.app import IotTesterApp
from iot_tester.screens.servos import (
    ANGLES,
    ASSEMBLY_ANGLE,
    ServoScreen,
    angle_button_id,
    parse_angle_button_id,
)


class _FakeChannel:
    def __init__(self) -> None:
        self.duty_cycle = 0


class _FakePca:
    def __init__(self) -> None:
        self.channels = [_FakeChannel() for _ in range(16)]


def test_angle_button_id_round_trips_for_every_servo_and_angle() -> None:
    for name in ("R1", "R2", "L1", "L2", "R4", "R3", "L3", "L4"):
        for angle in ANGLES:
            button_id = angle_button_id(name, angle)
            assert parse_angle_button_id(button_id) == (name, angle)


def test_angle_button_id_format() -> None:
    assert angle_button_id("R1", 45) == "angle-R1-45"


def test_servo_screen_composes_without_error() -> None:
    screen = ServoScreen()
    widgets = list(screen.compose())
    assert len(widgets) > 0


async def test_connect_button_shows_friendly_error_without_hardware() -> None:
    """On this dev machine there's no PCA9685/adafruit-blinka, so clicking
    Connect must hit the try/except and show a friendly message instead of
    crashing -- the same graceful-degradation behavior every other screen's
    hardware-open call already has."""
    app = IotTesterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ServoScreen())
        await pilot.pause()
        await pilot.click("#connect-btn")
        await pilot.pause()
        panel = app.screen.query_one("#panel-area")
        texts = [str(s.render()) for s in panel.query("Static")]
        assert any("Could not open the PCA9685" in t for t in texts)


async def test_center_all_sets_every_servo_to_assembly_angle() -> None:
    """docs/BUILD-PLAN.md Phase 5: every servo must be centered to 90 degrees
    before any horn/leg is attached. One button should do this for all 8
    servos at once, instead of clicking each servo's 90 degree button."""
    app = IotTesterApp()
    async with app.run_test(size=(120, 80)) as pilot:
        await pilot.pause()
        screen = ServoScreen()
        app.push_screen(screen)
        await pilot.pause()
        panel = screen.query_one("#panel-area")
        await screen._build_panel(panel)
        screen._driver = ServoDriver(_FakePca(), stagger_ms=0)
        await pilot.pause()
        await pilot.click("#center-btn")
        await pilot.pause()
        for name in SERVO_CHANNELS:
            assert screen._driver.last_angle(name) == ASSEMBLY_ANGLE
            label = screen.query_one(f"#label-{name}")
            assert f"{ASSEMBLY_ANGLE}°" in str(label.render())
