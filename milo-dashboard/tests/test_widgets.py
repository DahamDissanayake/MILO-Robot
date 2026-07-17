from milo_dashboard.collectors.system import decode_throttled
from milo_dashboard.widgets import bar, fmt_bytes, fmt_duration, fmt_rate, throttle_markup


def test_bar_clamps_and_colors():
    assert "[green]" in bar(10.0)
    assert "[yellow]" in bar(70.0)
    assert "[red]" in bar(95.0)
    assert "100.0%" in bar(250.0)  # clamped
    assert "  0.0%" in bar(-5.0)


def test_fmt_bytes():
    assert fmt_bytes(None) == "n/a"
    assert fmt_bytes(512) == "512 B"
    assert fmt_bytes(2048) == "2.0 KiB"
    assert fmt_bytes(3 * 1024**3) == "3.0 GiB"


def test_fmt_rate():
    assert fmt_rate(None) == "n/a"
    assert fmt_rate(2048.0) == "2.0 KiB/s"


def test_fmt_duration():
    assert fmt_duration(None) == "n/a"
    assert fmt_duration(59) == "0m 59s"
    assert fmt_duration(3660) == "1h 1m"
    assert fmt_duration(90061) == "1d 1h 1m"


def test_throttle_markup():
    assert "[green]OK[/]" in throttle_markup(decode_throttled("0x0"))
    bad = throttle_markup(decode_throttled("0x50005"))
    assert "UNDER-VOLTAGE" in bad and "THROTTLED" in bad
    assert "past:" in bad
    assert throttle_markup(None) == "[dim]n/a[/]"
