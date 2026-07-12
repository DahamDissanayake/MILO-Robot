from milo_dashboard.collectors.services import (
    collect_slow,
    parse_asound_cards,
    parse_systemctl_show,
    top_processes,
)

SHOW_ACTIVE = """ActiveState=active
SubState=running
MainPID=1234
NRestarts=2
ActiveEnterTimestamp=Sun 2026-07-12 20:11:03 BST
"""

SHOW_FAILED = """ActiveState=failed
SubState=failed
MainPID=0
NRestarts=5
ActiveEnterTimestamp=
"""

ASOUND_CARDS = """ 0 [sndrpigooglevoi]: RPi-simple - snd_rpi_googlevoicehat_soundcar
                      snd_rpi_googlevoicehat_soundcard
"""


def test_parse_systemctl_show_active():
    u = parse_systemctl_show("milo-bridge.service", SHOW_ACTIVE)
    assert u is not None
    assert u.active_state == "active" and u.sub_state == "running"
    assert u.pid == 1234
    assert u.n_restarts == 2
    assert u.since == "Sun 2026-07-12 20:11:03 BST"


def test_parse_systemctl_show_failed_has_no_pid():
    u = parse_systemctl_show("milo-bridge.service", SHOW_FAILED)
    assert u.active_state == "failed"
    assert u.pid is None
    assert u.since is None


def test_parse_systemctl_show_empty_returns_none():
    assert parse_systemctl_show("x.service", "") is None


def test_parse_asound_cards():
    assert parse_asound_cards(ASOUND_CARDS)
    assert not parse_asound_cards(" 0 [Headphones]: bcm2835 Headphones")
    assert not parse_asound_cards("")


def test_top_processes_sorted_and_limited():
    procs = top_processes(limit=3)
    assert len(procs) <= 3
    cpus = [p.cpu_percent for p in procs]
    assert cpus == sorted(cpus, reverse=True)


def test_collect_slow_never_raises():
    snap = collect_slow()
    assert isinstance(snap.journal.lines, tuple)
    assert isinstance(snap.hardware.i2c, bool)
