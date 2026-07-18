from milo_bridge.crashlog import CrashLog


def test_record_and_entries_round_trip(tmp_path):
    crash_log = CrashLog(tmp_path / "crashes.log")
    try:
        raise ValueError("boom")
    except ValueError as exc:
        crash_log.record("process", exc, context="test")
    entries = crash_log.entries()
    assert len(entries) == 1
    assert entries[0]["kind"] == "process"
    assert entries[0]["context"] == "test"
    assert entries[0]["error"] == "ValueError: boom"
    assert "Traceback" in entries[0]["traceback"]
    assert isinstance(entries[0]["t"], float)


def test_count_matches_number_of_records(tmp_path):
    crash_log = CrashLog(tmp_path / "crashes.log")
    assert crash_log.count() == 0
    for i in range(3):
        try:
            raise RuntimeError(f"err{i}")
        except RuntimeError as exc:
            crash_log.record("task", exc)
    assert crash_log.count() == 3


def test_entries_returns_most_recent_n(tmp_path):
    crash_log = CrashLog(tmp_path / "crashes.log")
    for i in range(5):
        try:
            raise RuntimeError(f"err{i}")
        except RuntimeError as exc:
            crash_log.record("task", exc)
    entries = crash_log.entries(n=2)
    assert len(entries) == 2
    assert entries[-1]["error"] == "RuntimeError: err4"


def test_clear_resets_count_and_entries(tmp_path):
    crash_log = CrashLog(tmp_path / "crashes.log")
    try:
        raise ValueError("boom")
    except ValueError as exc:
        crash_log.record("process", exc)
    assert crash_log.count() == 1
    crash_log.clear()
    assert crash_log.count() == 0
    assert crash_log.entries() == []


def test_entries_on_nonexistent_file_returns_empty_list(tmp_path):
    crash_log = CrashLog(tmp_path / "does-not-exist.log")
    assert crash_log.entries() == []
    assert crash_log.count() == 0


def test_entries_skips_corrupted_lines(tmp_path):
    path = tmp_path / "crashes.log"
    crash_log = CrashLog(path)
    try:
        raise ValueError("good")
    except ValueError as exc:
        crash_log.record("process", exc)
    with path.open("a", encoding="utf-8") as f:
        f.write("not valid json\n")
    entries = crash_log.entries()
    assert len(entries) == 1
    assert entries[0]["error"] == "ValueError: good"
