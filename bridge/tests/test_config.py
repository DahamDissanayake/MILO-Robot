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
