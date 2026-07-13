import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from generate_faces import generate  # noqa: E402


def test_generate_writes_one_file_per_single_frame_face(tmp_path):
    count = generate(tmp_path)
    assert (tmp_path / "idle.png").exists()
    assert (tmp_path / "happy.png").exists()
    assert count > 0


def test_generate_writes_indexed_files_for_multi_frame_faces(tmp_path):
    generate(tmp_path)
    assert (tmp_path / "idle_blink_1.png").exists()
    assert (tmp_path / "idle_blink_4.png").exists()
    assert not (tmp_path / "idle_blink.png").exists()
    assert (tmp_path / "love_1.png").exists()
    assert (tmp_path / "love_2.png").exists()


def test_generate_return_count_matches_total_frames():
    import eyes  # noqa: E402  (already on sys.path from the import above)
    expected = sum(len(frames) for frames in eyes.EMOTIONS.values())

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert generate(Path(d)) == expected
