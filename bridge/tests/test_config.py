import json
from pathlib import Path

from milo_bridge.config import BridgeConfig
from milo_bridge.webapp.auth import verify_password


def test_load_seeds_web_credentials_on_first_run(tmp_path, caplog):
    path = tmp_path / "config.json"
    with caplog.at_level("WARNING"):
        cfg = BridgeConfig.load(path)
    assert cfg.web_username == "dama"
    assert cfg.web_password_hash != ""

    # The generated password is logged once so the operator can log in.
    warning_text = "\n".join(r.message for r in caplog.records)
    assert "generated" in warning_text.lower()
    import re
    match = re.search(r"password[^:]*:\s*(\S+)", warning_text)
    assert match, f"no password found in log output: {warning_text!r}"
    generated_password = match.group(1)
    assert verify_password(generated_password, cfg.web_password_hash)

    # It must not be the old hardcoded default.
    assert not verify_password("MILO@gate", cfg.web_password_hash)

    # Second load reads the saved file back — must NOT re-seed/re-hash/re-log.
    caplog.clear()
    with caplog.at_level("WARNING"):
        cfg2 = BridgeConfig.load(path)
    assert cfg2.web_password_hash == cfg.web_password_hash
    assert not any("generated" in r.message.lower() for r in caplog.records)


def test_load_seeds_a_different_password_per_config(tmp_path):
    cfg_a = BridgeConfig.load(tmp_path / "a" / "config.json")
    cfg_b = BridgeConfig.load(tmp_path / "b" / "config.json")
    assert cfg_a.web_password_hash != cfg_b.web_password_hash


def test_servo_pulse_ranges_round_trip_through_json(tmp_path):
    path = tmp_path / "config.json"
    cfg = BridgeConfig.load(path)
    assert cfg.servo_pulse_ranges == [(500, 2500)] * 8
    cfg.save(path)
    cfg2 = BridgeConfig.load(path)
    assert cfg2.servo_pulse_ranges == [(500, 2500)] * 8
    assert all(isinstance(r, tuple) for r in cfg2.servo_pulse_ranges)


def test_load_drops_stale_renamed_field_instead_of_crashing(tmp_path):
    # Reproduces the real outage: a config.json saved before servo_trims
    # was renamed to servo_pulse_ranges must not crash BridgeConfig.load().
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({
            "robot_id": "milo-existing",
            "web_username": "dama",
            "web_password_hash": "salt$hash",
            "servo_trims": [0, 0, 0, 0, 0, 0, 0, 0],
        }),
        encoding="utf-8",
    )
    cfg = BridgeConfig.load(path)
    assert cfg.robot_id == "milo-existing"
    assert cfg.web_password_hash == "salt$hash"
    assert cfg.servo_pulse_ranges == [(500, 2500)] * 8

    # The stale key must not resurface in the file that's about to be
    # loaded again on the next restart.
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "servo_trims" not in saved


def test_mcp_port_defaults_and_round_trips(tmp_path: Path):
    path = tmp_path / "config.json"
    cfg = BridgeConfig.load(path)
    assert cfg.mcp_port == 8766

    cfg.mcp_port = 9999
    cfg.save(path)
    reloaded = BridgeConfig.load(path)
    assert reloaded.mcp_port == 9999
