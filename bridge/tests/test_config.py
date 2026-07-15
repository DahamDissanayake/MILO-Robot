from milo_bridge.config import BridgeConfig
from milo_bridge.webapp.auth import verify_password


def test_load_seeds_web_credentials_on_first_run(tmp_path):
    path = tmp_path / "config.json"
    cfg = BridgeConfig.load(path)
    assert cfg.web_username == "dama"
    assert cfg.web_password_hash != ""
    assert verify_password("MILO@gate", cfg.web_password_hash)

    # Second load reads the saved file back — must NOT re-seed/re-hash.
    cfg2 = BridgeConfig.load(path)
    assert cfg2.web_password_hash == cfg.web_password_hash


def test_servo_pulse_ranges_round_trip_through_json(tmp_path):
    path = tmp_path / "config.json"
    cfg = BridgeConfig.load(path)
    assert cfg.servo_pulse_ranges == [(500, 2500)] * 8
    cfg.save(path)
    cfg2 = BridgeConfig.load(path)
    assert cfg2.servo_pulse_ranges == [(500, 2500)] * 8
    assert all(isinstance(r, tuple) for r in cfg2.servo_pulse_ranges)
