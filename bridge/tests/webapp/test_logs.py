import logging

from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from milo_bridge.webapp.logbuf import RingBufferLogHandler
from .fakes import make_deps


def test_ring_buffer_caps_and_tails():
    h = RingBufferLogHandler(capacity=3)
    logger = logging.getLogger("rbtest")
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    for i in range(5):
        logger.info("line %d", i)
    logger.removeHandler(h)
    assert len(h.lines(10)) == 3
    assert h.lines(1)[0].endswith("line 4")
    assert h.lines(2)[0].endswith("line 3")


async def test_logs_endpoint():
    h = RingBufferLogHandler(capacity=10)
    logging.getLogger("milo-web-test").addHandler(h)
    logging.getLogger("milo-web-test").setLevel(logging.INFO)
    logging.getLogger("milo-web-test").info("hello from test")
    deps = make_deps(log_buffer=h)
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        data = await (await client.get("/api/logs?n=5")).json()
        assert any("hello from test" in line for line in data["lines"])
    finally:
        await client.close()
