from milo_dashboard.collectors.system import (
    collect_fast,
    collect_static,
    decode_throttled,
    parse_os_release,
)

OS_RELEASE = '''PRETTY_NAME="Raspberry Pi OS Lite (64-bit)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
'''


def test_decode_throttled_clean():
    f = decode_throttled("throttled=0x0")
    assert f is not None
    assert not f.under_voltage and not f.past_under_voltage
    assert not f.throttled and not f.past_throttled


def test_decode_throttled_past_events():
    # 0x50005: under-voltage + throttled now, and both in the past
    f = decode_throttled("throttled=0x50005")
    assert f.under_voltage and f.throttled
    assert f.past_under_voltage and f.past_throttled
    assert not f.freq_capped and not f.past_freq_capped


def test_decode_throttled_bare_hex_and_garbage():
    assert decode_throttled("0x10000").past_under_voltage
    assert decode_throttled("not hex") is None


def test_parse_os_release():
    assert parse_os_release(OS_RELEASE) == "Raspberry Pi OS Lite (64-bit)"
    assert parse_os_release("NAME=x") is None


def test_collect_static_never_raises():
    st = collect_static()
    assert st.hostname
    assert st.core_count >= 1


def test_collect_fast_never_raises():
    f = collect_fast()
    assert 0.0 <= f.mem_percent <= 100.0
    assert f.uptime_s > 0
    assert len(f.per_core) >= 1
