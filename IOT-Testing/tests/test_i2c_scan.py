from datetime import datetime, timezone

from iot_tester.screens.i2c_scan import EXPECTED_DEVICES, I2cScanScreen, scan_i2c_bus
from iot_tester.results_log import ResultRecorder


class FakeBus:
    def __init__(self, present: set[int]) -> None:
        self._present = present

    def read_byte(self, address: int) -> int:
        if address not in self._present:
            raise OSError(f"no device at 0x{address:02X}")
        return 0


def test_scan_reports_present_and_absent_addresses() -> None:
    bus = FakeBus({0x3C, 0x40, 0x68})
    found = scan_i2c_bus(bus, addresses=range(0x3A, 0x42))
    assert found[0x3C] is True
    assert found[0x40] is True
    assert found[0x3B] is False


def test_expected_devices_match_documented_addresses() -> None:
    assert EXPECTED_DEVICES == {
        0x3C: "SSD1306 OLED",
        0x40: "PCA9685 servo driver",
        0x68: "MPU6050 IMU",
    }


def test_i2c_scan_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = I2cScanScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
