"""Procedural EVE/Vector-style eyes for MILO's face display.

Every emotion is two rounded-rectangle "eye blocks" on a 128x64 1-bit
canvas. No pupils, eyebrows, or mouth: expression comes only from each
eye's size, tilt, and position. See README.md in this directory for the
full design rationale and parameter reference.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

WIDTH, HEIGHT = 128, 64


@dataclass(frozen=True)
class Eye:
    w: int = 32
    h: int = 32
    radius: int = 10
    tilt: float = 0.0  # degrees, positive = counter-clockwise (PIL convention)
    dx: int = 0  # offset from resting horizontal position
    dy: int = 0  # offset from resting vertical position


@dataclass(frozen=True)
class EyeFrame:
    left: Eye
    right: Eye
    gap: int = 16  # px between the two eyes' resting edges


def _rounded_rect(eye: Eye) -> Image.Image:
    pad = 2
    canvas = Image.new("1", (eye.w + pad * 2, eye.h + pad * 2), 0)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (pad, pad, pad + eye.w, pad + eye.h), radius=eye.radius, fill=1
    )
    if eye.tilt:
        canvas = canvas.rotate(eye.tilt, expand=True, resample=Image.BICUBIC)
    return canvas


def render_frame(frame: EyeFrame) -> Image.Image:
    canvas = Image.new("1", (WIDTH, HEIGHT), 0)
    left_img = _rounded_rect(frame.left)
    right_img = _rounded_rect(frame.right)
    total_w = left_img.width + frame.gap + right_img.width
    start_x = (WIDTH - total_w) // 2
    cy = HEIGHT // 2

    left_x = start_x + frame.left.dx
    left_y = cy - left_img.height // 2 + frame.left.dy
    canvas.paste(left_img, (left_x, left_y))

    right_x = start_x + left_img.width + frame.gap + frame.right.dx
    right_y = cy - right_img.height // 2 + frame.right.dy
    canvas.paste(right_img, (right_x, right_y))

    return canvas


def _pulse(frame: EyeFrame, factor: float) -> EyeFrame:
    """Scale both eyes' height by `factor`, for a subtle talk/love pulse."""
    def scaled(eye: Eye) -> Eye:
        return Eye(w=eye.w, h=round(eye.h * factor), radius=eye.radius,
                    tilt=eye.tilt, dx=eye.dx, dy=eye.dy)
    return EyeFrame(scaled(frame.left), scaled(frame.right), gap=frame.gap)


def _talk(base: EyeFrame) -> list[EyeFrame]:
    """Generic 2-frame speaking pulse layered on any base emotion frame."""
    return [base, _pulse(base, 0.85)]


_IDLE = EyeFrame(Eye(), Eye())
_HAPPY = EyeFrame(Eye(h=16, tilt=-14, dy=-2), Eye(h=16, tilt=14, dy=-2))
_SAD = EyeFrame(Eye(h=20, tilt=16, dy=6), Eye(h=20, tilt=-16, dy=6))
_ANGRY = EyeFrame(
    Eye(h=20, tilt=16, dy=2), Eye(h=20, tilt=-16, dy=2), gap=10,
)
_SLEEPY = EyeFrame(Eye(h=8, dy=4), Eye(h=8, dy=4))
_SURPRISED = EyeFrame(Eye(w=40, h=40, dy=-4), Eye(w=40, h=40, dy=-4), gap=18)
_CONFUSED = EyeFrame(Eye(), Eye(w=28, h=28, tilt=18, dy=-10))
_LOVE = EyeFrame(Eye(h=18, tilt=-10, dy=-2), Eye(h=18, tilt=10, dy=-2))
_THINKING_1 = EyeFrame(Eye(h=28, dx=-4, dy=-2), Eye(w=28, h=26, tilt=8, dx=-4, dy=-6))
_THINKING_2 = EyeFrame(Eye(h=28, dx=4, dy=-2), Eye(w=28, h=26, tilt=8, dx=4, dy=-6))
_EXCITED_1 = EyeFrame(Eye(w=36, h=36, dy=-6), Eye(w=36, h=36, dy=-6), gap=18)
_EXCITED_2 = EyeFrame(Eye(w=36, h=36, dy=4), Eye(w=36, h=36, dy=4), gap=18)

EMOTIONS: dict[str, list[EyeFrame]] = {
    "idle": [_IDLE],
    "idle_blink": [
        EyeFrame(Eye(h=18), Eye(h=18)),
        EyeFrame(Eye(h=6), Eye(h=6)),
        EyeFrame(Eye(h=18), Eye(h=18)),
        EyeFrame(Eye(h=32), Eye(h=32)),
    ],
    "happy": [_HAPPY],
    "sad": [_SAD],
    "angry": [_ANGRY],
    "love": [_LOVE, _pulse(_LOVE, 1.2)],
    "sleepy": [_SLEEPY],
    "surprised": [_SURPRISED],
    "confused": [_CONFUSED],
    "thinking": [_THINKING_1, _THINKING_2],
    "excited": [_EXCITED_1, _EXCITED_2],
    "talk_happy": _talk(_HAPPY),
    "talk_sad": _talk(_SAD),
    "talk_angry": _talk(_ANGRY),
    "talk_confused": _talk(_CONFUSED),
    "talk_love": _talk(_LOVE),
    "talk_sleepy": _talk(_SLEEPY),
    "talk_surprised": _talk(_SURPRISED),
    "talk_thinking": _talk(_THINKING_1),
    "talk_excited": _talk(_EXCITED_1),
}
