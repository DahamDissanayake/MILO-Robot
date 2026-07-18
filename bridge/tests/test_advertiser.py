from milo_bridge.config import BridgeConfig
from milo_bridge.net.advertiser import SERVICE_TYPE, RobotAdvertiser


def _cfg(**overrides):
    defaults = dict(robot_id="milo-1", robot_name="milo", robot_ws_port=8765)
    defaults.update(overrides)
    return BridgeConfig(**defaults)


def test_service_info_carries_id_name_and_default_flags():
    adv = RobotAdvertiser(_cfg())
    info = adv._service_info()
    assert info.type == SERVICE_TYPE
    assert info.port == 8765
    props = {k.decode(): v.decode() for k, v in info.properties.items()}
    assert props["id"] == "milo-1"
    assert props["name"] == "milo"
    assert props["busy"] == "0"
    assert props["pairing"] == "0"


def test_service_info_reflects_current_busy_and_pairing_flags():
    adv = RobotAdvertiser(_cfg())
    adv.busy = True
    adv.pairing = True
    props = {k.decode(): v.decode() for k, v in adv._service_info().properties.items()}
    assert props["busy"] == "1"
    assert props["pairing"] == "1"


def test_update_sets_flags_without_a_registered_service():
    # update() before start() (no self._zc yet) must not raise -- it should
    # just record the flags for whenever the service actually registers.
    adv = RobotAdvertiser(_cfg())
    adv.update(busy=True, pairing=True)
    assert adv.busy is True
    assert adv.pairing is True
    adv.update(pairing=False)
    assert adv.busy is True  # untouched fields stay as they were
    assert adv.pairing is False


def test_service_info_records_the_advertised_ip():
    adv = RobotAdvertiser(_cfg())
    adv._service_info()
    assert adv.advertised_ip  # non-empty; exact value is host-dependent
