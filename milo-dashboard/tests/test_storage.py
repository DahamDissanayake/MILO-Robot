from pathlib import Path

from milo_dashboard.collectors.storage import (
    GRAPH_DB,
    MILO_DIR,
    POLICY,
    collect,
    dir_size,
    file_size,
    keep_partition,
)


def test_milo_paths_match_bridge_conventions():
    assert MILO_DIR == Path.home() / ".milo"
    assert GRAPH_DB == MILO_DIR / "graph.db"
    assert POLICY == MILO_DIR / "policy.onnx"


def test_keep_partition_filters_pseudo_filesystems():
    assert keep_partition("ext4")
    assert keep_partition("vfat")
    assert not keep_partition("tmpfs")
    assert not keep_partition("squashfs")
    assert not keep_partition("")


def test_dir_size_missing_dir_is_none(tmp_path):
    assert dir_size(tmp_path / "nope") is None


def test_dir_size_sums_files(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 10)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 5)
    assert dir_size(tmp_path) == 15


def test_file_size(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"z" * 7)
    assert file_size(p) == 7
    assert file_size(tmp_path / "missing") is None


def test_collect_never_raises():
    snap = collect()
    assert isinstance(snap.disks, tuple)
    for d in snap.disks:
        assert 0.0 <= d.percent <= 100.0
