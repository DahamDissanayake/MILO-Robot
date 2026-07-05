import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import convert_faces  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
HEADER = REPO_ROOT / "hardware" / "reference-sesame" / "face-bitmaps.h"


def _fake_array(symbol: str) -> str:
    data = ", ".join(["0xFF"] * convert_faces.EXPECTED_BYTES)
    return f"const unsigned char epd_bitmap_{symbol} [] PROGMEM = {{ {data} }};\n"


def test_split_symbol_handles_underscored_names():
    assert convert_faces.split_symbol("talk_happy_2") == ("talk_happy", 2)
    assert convert_faces.split_symbol("idle_blink") == ("idle_blink", None)
    assert convert_faces.split_symbol("walk_1") == ("walk", 1)


def test_parse_and_render_synthetic_header(tmp_path: Path):
    header = _fake_array("testface") + _fake_array("anim_1") + _fake_array("anim_2")
    faces = convert_faces.parse_bitmaps(header)
    assert set(faces) == {"testface", "anim"}
    assert len(faces["anim"]) == 2

    image = convert_faces.bitmap_to_image(bytes([0x80] + [0x00] * (convert_faces.EXPECTED_BYTES - 1)))
    assert image.size == (128, 64)
    assert image.getpixel((0, 0)) == 1
    assert image.getpixel((1, 0)) == 0


def test_convert_writes_pngs(tmp_path: Path):
    header_file = tmp_path / "faces.h"
    header_file.write_text(_fake_array("solo") + _fake_array("duo_1") + _fake_array("duo_2"))
    out = tmp_path / "out"
    written = convert_faces.convert(header_file, out)
    assert written == 3
    assert (out / "solo.png").exists()
    assert (out / "duo_1.png").exists() and (out / "duo_2.png").exists()
    assert Image.open(out / "solo.png").size == (128, 64)


def test_real_sesame_header_contains_the_face_library():
    faces = convert_faces.parse_bitmaps(HEADER.read_text(encoding="utf-8", errors="replace"))
    # Faces the bridge relies on. Note "stand" has no art in the Sesame library
    # (weak symbol in the firmware) — the display driver falls back for it.
    for needed in ("rest", "wave", "walk", "idle", "idle_blink", "sleepy", "excited"):
        assert needed in faces, f"missing face {needed!r}"
    assert len(faces["rest"]) == 3  # rest + rest_1 + rest_2 are one animation
    assert len(faces["idle_blink"]) == 4
