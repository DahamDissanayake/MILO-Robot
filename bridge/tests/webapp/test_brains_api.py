from .client_helpers import authed_client
from .fakes import FakePeer, FakeRobotServer, make_deps

_client = authed_client


async def test_brains_reports_no_connection_and_no_paired_by_default():
    deps = make_deps(robot_server=FakeRobotServer())
    client = await _client(deps)
    try:
        resp = await client.get("/api/brains")
        assert resp.status == 200
        data = await resp.json()
        assert data == {
            "connected": [], "active_id": None, "paired": [], "pairing": False,
            "ip": "192.168.1.15", "port": 8765,
        }
    finally:
        await client.close()


async def test_brains_reports_ip_and_port_regardless_of_pairing_state():
    # Always included, not gated on pairing -- an already-paired brain that
    # can't discover the robot via mDNS needs this to reconnect manually too.
    deps = make_deps(robot_server=FakeRobotServer())
    client = await _client(deps)
    try:
        data = await (await client.get("/api/brains")).json()
        assert data["ip"] == "192.168.1.15"
        assert data["port"] == 8765
    finally:
        await client.close()


async def test_brains_reports_connected_brain_and_paired_list():
    rs = FakeRobotServer(paired=[{"id": "brain-1", "name": "desk"}])
    rs.connect(FakePeer("brain-1", "desk"))
    deps = make_deps(robot_server=rs)
    client = await _client(deps)
    try:
        data = await (await client.get("/api/brains")).json()
        assert data["connected"] == [{"id": "brain-1", "name": "desk", "active": True}]
        assert data["active_id"] == "brain-1"
        assert data["paired"] == [{"id": "brain-1", "name": "desk"}]
        assert data["pairing"] is False
    finally:
        await client.close()


async def test_brains_reports_multiple_connected_brains_with_one_active():
    rs = FakeRobotServer(paired=[{"id": "brain-1", "name": "desk"}, {"id": "brain-2", "name": "laptop"}])
    rs.connect(FakePeer("brain-1", "desk"))
    rs.connect(FakePeer("brain-2", "laptop"))
    deps = make_deps(robot_server=rs)
    client = await _client(deps)
    try:
        data = await (await client.get("/api/brains")).json()
        assert data["connected"] == [
            {"id": "brain-1", "name": "desk", "active": True},
            {"id": "brain-2", "name": "laptop", "active": False},
        ]
        assert data["active_id"] == "brain-1"
    finally:
        await client.close()


async def test_brains_reports_pairing_flag():
    rs = FakeRobotServer()
    rs.advertiser.pairing = True
    deps = make_deps(robot_server=rs)
    client = await _client(deps)
    try:
        data = await (await client.get("/api/brains")).json()
        assert data["pairing"] is True
    finally:
        await client.close()


async def test_brains_never_leaks_the_pin():
    rs = FakeRobotServer()
    rs.pairing.current_pin = "9999"
    rs.advertiser.pairing = True
    deps = make_deps(robot_server=rs)
    client = await _client(deps)
    try:
        text = await (await client.get("/api/brains")).text()
        assert "9999" not in text
    finally:
        await client.close()


async def test_brains_degrades_gracefully_when_robot_server_is_absent():
    deps = make_deps(robot_server=None)
    client = await _client(deps)
    try:
        resp = await client.get("/api/brains")
        assert resp.status == 200
        assert await resp.json() == {
            "connected": [], "active_id": None, "paired": [], "pairing": False, "ip": "", "port": 0,
        }
    finally:
        await client.close()
