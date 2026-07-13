import milo_bridge.webapp.server as server_mod
from milo_bridge.webapp.server import pick_port


def test_pick_port_prefers_config():
    assert pick_port(80, port_free=lambda p: True) == 80


def test_pick_port_falls_back_to_8080():
    assert pick_port(80, port_free=lambda p: p != 80) == 8080
