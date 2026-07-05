from pathlib import Path

from milo_common.auth import PairedStore, derive_token

from milo_bridge.net.discovery import BrainRecord, record_from_properties, select_brain


def rec(brain_id, *, busy=False, pairing=False, name=None):
    return BrainRecord(
        brain_id=brain_id, name=name or brain_id, host="10.0.0.5", port=8765,
        busy=busy, pairing=pairing,
    )


def store_with(tmp_path: Path, *pairs: tuple[str, int]) -> PairedStore:
    store = PairedStore(tmp_path / "paired.json")
    for peer_id, priority in pairs:
        store.add(peer_id, derive_token("123456", "r", peer_id), priority=priority)
    return store


def test_record_from_txt_properties_bytes_and_str():
    record = record_from_properties(
        "192.168.1.7", 8765,
        {b"id": b"brain-1", b"name": b"desk", b"tier": b"large", b"busy": b"1", "pairing": "1"},
    )
    assert record == BrainRecord(
        brain_id="brain-1", name="desk", host="192.168.1.7", port=8765,
        tier="large", busy=True, pairing=True,
    )
    assert record.url == "ws://192.168.1.7:8765"


def test_record_without_id_is_ignored():
    assert record_from_properties("1.2.3.4", 1, {b"name": b"x"}) is None


def test_select_prefers_highest_priority_paired(tmp_path):
    store = store_with(tmp_path, ("laptop", 1), ("desktop", 5))
    choice = select_brain([rec("laptop"), rec("desktop")], store)
    assert choice is not None
    record, needs_pairing = choice
    assert record.brain_id == "desktop" and not needs_pairing


def test_select_skips_busy_paired(tmp_path):
    store = store_with(tmp_path, ("laptop", 1), ("desktop", 5))
    record, _ = select_brain([rec("laptop"), rec("desktop", busy=True)], store)
    assert record.brain_id == "laptop"


def test_select_falls_back_to_pairing_mode_brain(tmp_path):
    store = store_with(tmp_path)  # nothing paired
    choice = select_brain([rec("newbie", pairing=True), rec("stranger")], store)
    assert choice is not None
    record, needs_pairing = choice
    assert record.brain_id == "newbie" and needs_pairing


def test_select_none_when_only_busy_or_strangers(tmp_path):
    store = store_with(tmp_path, ("desktop", 5))
    assert select_brain([rec("desktop", busy=True), rec("stranger")], store) is None
    assert select_brain([], store) is None
