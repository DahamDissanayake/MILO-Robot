"""One-off converter: Sesame ``face-bitmaps.h`` PROGMEM arrays -> PNG face assets.

Usage:
    python bridge/tools/convert_faces.py hardware/reference-sesame/face-bitmaps.h bridge/assets/faces

Bitmaps are 128x64 1-bit, 8 horizontal pixels per byte, MSB first (image2cpp
default). Symbols are ``epd_bitmap_<face>`` with optional ``_<n>`` frame
suffixes; face names may themselves contain underscores (``talk_happy``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image

WIDTH, HEIGHT = 128, 64
EXPECTED_BYTES = WIDTH * HEIGHT // 8

ARRAY_RE = re.compile(
    r"epd_bitmap_(?P<symbol>\w+?)\s*\[\]\s*PROGMEM\s*=\s*\{(?P<body>.*?)\};",
    re.DOTALL,
)


def split_symbol(symbol: str) -> tuple[str, int | None]:
    """``talk_happy_2`` -> (``talk_happy``, 2); ``idle_blink`` -> (``idle_blink``, None)."""
    m = re.fullmatch(r"(.+)_(\d+)", symbol)
    if m:
        return m.group(1), int(m.group(2))
    return symbol, None


def parse_bitmaps(header_text: str) -> dict[str, list[bytes]]:
    """Face name -> ordered frame data.

    The unsuffixed symbol is frame 0; ``_1, _2, ...`` follow it (the firmware's
    ``rest`` + ``rest_1`` + ``rest_2`` is a 3-frame animation).
    """
    frames: dict[str, dict[int, bytes]] = {}
    for m in ARRAY_RE.finditer(header_text):
        name, frame_no = split_symbol(m.group("symbol"))
        values = re.findall(r"0[xX][0-9a-fA-F]{1,2}", m.group("body"))
        data = bytes(int(v, 16) for v in values)
        if len(data) != EXPECTED_BYTES:
            continue  # skip anything that isn't a 128x64 face
        frames.setdefault(name, {})[frame_no if frame_no is not None else 0] = data
    return {name: [d for _, d in sorted(by_no.items())] for name, by_no in frames.items()}


def bitmap_to_image(data: bytes) -> Image.Image:
    image = Image.new("1", (WIDTH, HEIGHT), 0)
    pixels = image.load()
    for i, byte in enumerate(data):
        y, x_base = divmod(i, WIDTH // 8)
        for bit in range(8):
            if byte & (0x80 >> bit):
                pixels[x_base * 8 + bit, y] = 1
    return image


def convert(header_path: Path, out_dir: Path) -> int:
    faces = parse_bitmaps(header_path.read_text(encoding="utf-8", errors="replace"))
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, frames in faces.items():
        multi = len(frames) > 1
        for idx, data in enumerate(frames, start=1):
            filename = f"{name}_{idx}.png" if multi else f"{name}.png"
            bitmap_to_image(data).save(out_dir / filename)
            written += 1
    return written


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    count = convert(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"wrote {count} face frames")
