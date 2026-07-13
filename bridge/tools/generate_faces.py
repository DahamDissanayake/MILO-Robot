"""Render every face in eyes.EMOTIONS to PNGs.

Usage:
    python bridge/tools/generate_faces.py [out_dir]

Defaults to bridge/assets/faces, overwriting the 20 emotion PNGs that
belong to this generator. Pose-linked art (wave, dance, walk, ...) is not
touched by this script — see bridge/assets/faces/README.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "assets" / "faces"))
from eyes import EMOTIONS, render_frame  # noqa: E402

DEFAULT_OUT = Path(__file__).parent.parent / "assets" / "faces"


def generate(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, frames in EMOTIONS.items():
        multi = len(frames) > 1
        for idx, frame in enumerate(frames, start=1):
            filename = f"{name}_{idx}.png" if multi else f"{name}.png"
            render_frame(frame).save(out_dir / filename)
            written += 1
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    count = generate(target)
    print(f"wrote {count} face frames to {target}")
