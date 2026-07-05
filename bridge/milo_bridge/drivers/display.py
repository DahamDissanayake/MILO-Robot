"""SSD1306 face display: Sesame's face personality, rendered from Python.

Face art is the original firmware bitmap library converted to PNGs
(``bridge/assets/faces/<name>_<frame>.png``, see tools/convert_faces.py).
Animation modes and the idle-blink behavior match the ESP32 firmware:
random 3-7 s blink interval, 30% chance of a double blink 120-220 ms apart.

The output device is injected; anything with ``display(PIL.Image)`` works
(luma.oled's ssd1306 device on the Pi, a recorder in tests).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from enum import Enum
from pathlib import Path

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 128, 64

# Some faces the firmware names have no art in the bitmap library (it declares
# them as weak symbols — e.g. "stand"). Fall back rather than crash.
FALLBACK_FACE = "idle"

BLINK_MIN_S = 3.0
BLINK_MAX_S = 7.0
DOUBLE_BLINK_CHANCE = 0.30
DOUBLE_BLINK_GAP_S = (0.120, 0.220)
DEFAULT_FPS = 8.0


class AnimMode(Enum):
    LOOP = "loop"
    ONCE = "once"
    BOOMERANG = "boomerang"


def next_blink_delay(rng: random.Random) -> float:
    return rng.uniform(BLINK_MIN_S, BLINK_MAX_S)


def should_double_blink(rng: random.Random) -> bool:
    return rng.random() < DOUBLE_BLINK_CHANCE


def load_face_frames(assets_dir: Path, name: str) -> list[Image.Image]:
    """Frames ``<name>.png`` or ``<name>_1.png, <name>_2.png, ...`` in order."""
    single = assets_dir / f"{name}.png"
    if single.exists():
        return [Image.open(single).convert("1")]
    pattern = re.compile(rf"^{re.escape(name)}_(\d+)\.png$")
    frames = sorted(
        (
            (int(m.group(1)), p)
            for p in assets_dir.glob(f"{name}_*.png")
            if (m := pattern.match(p.name))
        ),
    )
    return [Image.open(p).convert("1") for _, p in frames]


def render_pin_image(pin: str) -> Image.Image:
    """Big pairing PIN, drawn large enough to read across a room."""
    image = Image.new("1", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    draw.text((6, 4), "PAIR ME!", fill=1)
    spaced = " ".join(pin)
    # Scale the default bitmap font up 3x for the digits.
    small = Image.new("1", (WIDTH, 16), 0)
    ImageDraw.Draw(small).text((6, 2), spaced, fill=1)
    big = small.resize((WIDTH * 3, 48), Image.NEAREST).crop((0, 0, WIDTH, 48))
    image.paste(big, (0, 16))
    return image


class FaceDisplay:
    def __init__(self, device, assets_dir: Path, rng: random.Random | None = None):
        self._device = device
        self._assets_dir = Path(assets_dir)
        self._rng = rng or random.Random()
        self._cache: dict[str, list[Image.Image]] = {}
        self.current_face: str | None = None
        self._anim_task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None

    @classmethod
    def from_hardware(cls, assets_dir: Path) -> "FaceDisplay":
        from luma.core.interface.serial import i2c  # type: ignore
        from luma.oled.device import ssd1306  # type: ignore

        return cls(ssd1306(i2c(port=1, address=0x3C)), assets_dir)

    def _frames(self, name: str) -> list[Image.Image]:
        if name not in self._cache:
            frames = load_face_frames(self._assets_dir, name)
            if not frames:
                raise KeyError(f"no face asset named {name!r} in {self._assets_dir}")
            self._cache[name] = frames
        return self._cache[name]

    def _show(self, image: Image.Image) -> None:
        self._device.display(image)

    async def set_face(
        self, name: str, mode: AnimMode = AnimMode.ONCE, fps: float = DEFAULT_FPS
    ) -> None:
        """Show a face; multi-frame faces animate per ``mode`` in a background task."""
        self._cancel_anim()
        try:
            frames = self._frames(name)
        except KeyError:
            if name == FALLBACK_FACE:
                raise
            log.warning("no art for face %r, falling back to %r", name, FALLBACK_FACE)
            name = FALLBACK_FACE
            frames = self._frames(name)
        self.current_face = name
        self._show(frames[0])
        if len(frames) > 1:
            self._anim_task = asyncio.create_task(self._animate(frames, mode, fps))

    async def _animate(self, frames: list[Image.Image], mode: AnimMode, fps: float) -> None:
        delay = 1.0 / fps
        if mode is AnimMode.ONCE:
            order = frames[1:]
        elif mode is AnimMode.BOOMERANG:
            order = frames[1:] + frames[-2::-1]
        else:
            order = frames[1:] + frames  # then repeats below
        try:
            while True:
                for frame in order:
                    await asyncio.sleep(delay)
                    self._show(frame)
                if mode is AnimMode.ONCE:
                    return
                if mode is AnimMode.BOOMERANG:
                    order = frames[1:] + frames[-2::-1]
        except asyncio.CancelledError:
            raise

    async def show_pin(self, pin: str) -> None:
        self._cancel_anim()
        self.stop_idle()
        self.current_face = None
        self._show(render_pin_image(pin))

    def start_idle(self) -> None:
        """Idle face + random blinking, until stop_idle()."""
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_loop())

    def stop_idle(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None

    async def _idle_loop(self) -> None:
        await self.set_face("idle", AnimMode.BOOMERANG)
        while True:
            await asyncio.sleep(next_blink_delay(self._rng))
            await self._blink()
            if should_double_blink(self._rng):
                await asyncio.sleep(self._rng.uniform(*DOUBLE_BLINK_GAP_S))
                await self._blink()

    async def _blink(self) -> None:
        blink = self._frames("idle_blink")
        for frame in blink:
            self._show(frame)
            await asyncio.sleep(1.0 / DEFAULT_FPS / 2)
        await self.set_face("idle", AnimMode.BOOMERANG)

    def _cancel_anim(self) -> None:
        if self._anim_task is not None:
            self._anim_task.cancel()
            self._anim_task = None
