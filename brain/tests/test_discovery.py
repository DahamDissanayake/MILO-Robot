from pathlib import Path

from milo_common.auth import PairedStore, derive_token

from milo_brain.net.discovery import RobotRecord, record_from_properties, select_robot


def rec(robot_id, *, busy=False, pairing=False, name=None):
    return RobotRecord(
        robot_id=robot_id, name=name or robot_id, host="10.0.0.5", port=8765,
        busy=busy, pairing=pairing,
    )


def store_with(tmp_path: Path, *pairs: tuple[str, int]) -> PairedStore:
    store = PairedStore(tmp_path / "paired.json")
    for peer_id, priority in pairs:
        store.add(peer_id, derive_token("123456", "b", peer_id), priority=priority)
    return store


def test_record_from_txt_properties_bytes_and_str():
    record = record_from_properties(
        "192.168.1.7", 8765,
        {b"id": b"milo-1", b"name": b"milo", b"busy": b"1", "pairing": "1"},
    )
    assert record == RobotRecord(
        robot_id="milo-1", name="milo", host="192.168.1.7", port=8765,
        busy=True, pairing=True,
    )
    assert record.url == "ws://192.168.1.7:8765"


def test_record_without_id_is_ignored():
    assert record_from_properties("1.2.3.4", 1, {b"name": b"x"}) is None


def test_select_prefers_highest_priority_paired(tmp_path):
    store = store_with(tmp_path, ("laptop", 1), ("desktop", 5))
    choice = select_robot([rec("laptop"), rec("desktop")], store)
    assert choice is not None
    record, needs_pairing = choice
    assert record.robot_id == "desktop" and not needs_pairing


def test_select_paired_wins_even_if_busy(tmp_path):
    # busy no longer disqualifies an already-paired robot -- the robot
    # accepts multiple simultaneous brains now (see
    # bridge/milo_bridge/net/server.py's connected_brains), so the highest
    # -priority paired robot still wins regardless of busy.
    store = store_with(tmp_path, ("laptop", 1), ("desktop", 5))
    record, _ = select_robot([rec("laptop"), rec("desktop", busy=True)], store)
    assert record.robot_id == "desktop"


def test_select_falls_back_to_pairing_mode_robot(tmp_path):
    store = store_with(tmp_path)  # nothing paired
    choice = select_robot([rec("newbie", pairing=True), rec("stranger")], store)
    assert choice is not None
    record, needs_pairing = choice
    assert record.robot_id == "newbie" and needs_pairing


def test_select_a_busy_paired_robot_instead_of_none(tmp_path):
    store = store_with(tmp_path, ("desktop", 5))
    choice = select_robot([rec("desktop", busy=True), rec("stranger")], store)
    assert choice is not None
    assert choice[0].robot_id == "desktop"


def test_select_none_when_nothing_paired_or_pairing(tmp_path):
    store = store_with(tmp_path)  # nothing paired
    assert select_robot([rec("stranger")], store) is None
    assert select_robot([], store) is None


def test_select_prefers_manual_target_even_over_a_higher_priority_paired_robot(tmp_path):
    # The Connect Robots tab's pick always wins for one tick, regardless of
    # what the passive auto-reconnect policy would otherwise choose.
    store = store_with(tmp_path, ("laptop", 1), ("desktop", 5))
    choice = select_robot(
        [rec("laptop"), rec("desktop")], store, manual_target="laptop"
    )
    assert choice is not None
    record, needs_pairing = choice
    assert record.robot_id == "laptop" and not needs_pairing


def test_select_manual_target_on_an_unpaired_robot_needs_pairing(tmp_path):
    store = store_with(tmp_path)  # nothing paired
    choice = select_robot([rec("newbie")], store, manual_target="newbie")
    assert choice is not None
    record, needs_pairing = choice
    assert record.robot_id == "newbie" and needs_pairing


def test_select_manual_target_works_even_if_busy(tmp_path):
    store = store_with(tmp_path, ("desktop", 5))
    choice = select_robot(
        [rec("desktop", busy=True), rec("laptop")], store, manual_target="desktop"
    )
    assert choice is not None and choice[0].robot_id == "desktop"


def test_select_manual_target_falls_through_if_absent(tmp_path):
    store = store_with(tmp_path, ("desktop", 5))
    choice = select_robot([rec("desktop")], store, manual_target="ghost")
    assert choice is not None and choice[0].robot_id == "desktop"
