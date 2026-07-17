from iot_tester.screens.wiring import (
    GPIO_TABLE,
    I2C_BUS,
    I2S_BUS,
    PIN_HEADER_DIAGRAM,
    POWER_RULE,
    SERVO_MAP,
    WiringScreen,
)


def test_i2c_bus_lists_all_three_addresses() -> None:
    assert "0x40" in I2C_BUS
    assert "0x3C" in I2C_BUS
    assert "0x68" in I2C_BUS


def test_servo_map_lists_all_eight_channels() -> None:
    for name, channel in [
        ("R1", "0"), ("R2", "1"), ("L1", "2"), ("L2", "3"),
        ("R4", "4"), ("R3", "5"), ("L3", "6"), ("L4", "7"),
    ]:
        assert name in SERVO_MAP
        assert f"ch{channel}" in SERVO_MAP


def test_power_rule_warns_about_pi_5v() -> None:
    assert "5V" in POWER_RULE
    assert "never" in POWER_RULE.lower()


def test_i2s_bus_documents_mic_channel_select() -> None:
    assert "GND" in I2S_BUS
    assert "3V3" in I2S_BUS


def test_pin_header_diagram_shows_i2c_pins() -> None:
    assert "GPIO2" in PIN_HEADER_DIAGRAM
    assert "GPIO3" in PIN_HEADER_DIAGRAM


def test_gpio_table_lists_i2s_pins() -> None:
    assert "GPIO 18" in GPIO_TABLE
    assert "GPIO 21" in GPIO_TABLE


def test_wiring_screen_composes_without_error() -> None:
    screen = WiringScreen()
    widgets = list(screen.compose())
    assert len(widgets) > 0
