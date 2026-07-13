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
