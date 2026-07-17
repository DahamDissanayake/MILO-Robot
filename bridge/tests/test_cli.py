from milo_bridge import cli
from milo_bridge.config import BridgeConfig
from milo_common.auth import PairedStore


def test_mcp_pair_mints_and_persists_a_token(tmp_path, monkeypatch, capsys):
    cfg = BridgeConfig(data_dir=str(tmp_path))
    monkeypatch.setattr(BridgeConfig, "load", classmethod(lambda cls: cfg))

    cli.main(["mcp-pair", "--name", "my-laptop"])

    out = capsys.readouterr().out
    assert "my-laptop" in out
    store = PairedStore(cfg.paired_path)
    assert store.is_paired("my-laptop")
    # The printed token is the same one that was persisted.
    printed_token = out.strip().splitlines()[-1].split()[-1]
    assert bytes.fromhex(printed_token) == store.token_for("my-laptop")
