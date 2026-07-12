from milo_dashboard.collectors.network import (
    RateTracker,
    collect_fast,
    parse_default_route,
    parse_iw_link,
)

IW_CONNECTED = """Connected to aa:bb:cc:dd:ee:ff (on wlan0)
\tSSID: HomeNet-5G
\tfreq: 2437
\tRX: 123456 bytes (789 packets)
\tTX: 65432 bytes (321 packets)
\tsignal: -52 dBm
\trx bitrate: 65.0 MBit/s
\ttx bitrate: 72.2 MBit/s MCS 7 short GI
"""

IW_NOT_CONNECTED = "Not connected.\n"

IP_ROUTE = "default via 192.168.1.1 dev wlan0 proto dhcp src 192.168.1.42 metric 600\n"


def test_parse_iw_link_connected():
    link = parse_iw_link(IW_CONNECTED)
    assert link is not None
    assert link.ssid == "HomeNet-5G"
    assert link.signal_dbm == -52
    assert link.bitrate_mbps == 72.2


def test_parse_iw_link_not_connected():
    assert parse_iw_link(IW_NOT_CONNECTED) is None
    assert parse_iw_link("") is None


def test_parse_default_route():
    assert parse_default_route(IP_ROUTE) == "192.168.1.1"
    assert parse_default_route("") is None


def test_rate_tracker_first_call_zero():
    t = RateTracker()
    assert t.update(1000, 500, now=10.0) == (0.0, 0.0)


def test_rate_tracker_computes_rates():
    t = RateTracker()
    t.update(1000, 500, now=10.0)
    rx, tx = t.update(3000, 1500, now=12.0)
    assert rx == 1000.0  # (3000-1000)/2s
    assert tx == 500.0


def test_rate_tracker_counter_reset_yields_zero():
    t = RateTracker()
    t.update(5000, 5000, now=10.0)
    assert t.update(100, 100, now=12.0) == (0.0, 0.0)


def test_collect_fast_never_raises():
    snap = collect_fast(RateTracker())
    assert snap.rx_total >= 0
    for iface in snap.interfaces:
        assert iface.name
