from milo_bridge.webapp.server import pick_port, start_web

from .fakes import make_deps


def test_pick_port_prefers_config():
    assert pick_port(80, port_free=lambda p: True) == 80


def test_pick_port_falls_back_to_8080():
    assert pick_port(80, port_free=lambda p: p != 80) == 8080


async def test_start_web_never_propagates_exceptions(monkeypatch):
    def _boom(deps):
        raise RuntimeError("app factory exploded")

    monkeypatch.setattr("milo_bridge.webapp.server.create_app", _boom)

    result = await start_web(make_deps())

    assert result is None
