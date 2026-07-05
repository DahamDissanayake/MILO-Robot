"""Sound direction from the stereo mic pair via GCC-PHAT.

The 10-15 cm mic baseline gives at most ~±440 µs of inter-channel delay —
enough for a coarse bearing (left / center / right), which is all Milo needs
to turn toward a speaker.
"""

from __future__ import annotations

import math

import numpy as np

SPEED_OF_SOUND = 343.0  # m/s
SAMPLE_RATE = 16_000
DEFAULT_MIC_DISTANCE_M = 0.12


def gcc_phat(left: np.ndarray, right: np.ndarray, max_delay_s: float, fs: int = SAMPLE_RATE) -> float:
    """Delay of ``right`` relative to ``left`` in seconds (positive = sound
    arrived at the left mic first, i.e. source is to the left)."""
    n = len(left) + len(right)
    nfft = 1 << (n - 1).bit_length()
    lf = np.fft.rfft(left.astype(np.float64), nfft)
    rf = np.fft.rfft(right.astype(np.float64), nfft)
    cross = lf * np.conj(rf)
    magnitude = np.abs(cross)
    magnitude[magnitude < 1e-12] = 1e-12
    correlation = np.fft.irfft(cross / magnitude, nfft)

    max_shift = min(int(max_delay_s * fs), nfft // 2)
    # Rearrange so index 0 = -max_shift ... max_shift.
    correlation = np.concatenate((correlation[-max_shift:], correlation[: max_shift + 1]))
    shift = int(np.argmax(np.abs(correlation))) - max_shift
    return -shift / fs


def bearing_from_delay(delay_s: float, mic_distance_m: float = DEFAULT_MIC_DISTANCE_M) -> float:
    """Bearing in degrees: negative = left, positive = right, 0 = ahead."""
    x = (delay_s * SPEED_OF_SOUND) / mic_distance_m
    return -math.degrees(math.asin(max(-1.0, min(1.0, x))))


def classify(bearing_deg: float, dead_zone_deg: float = 15.0) -> str:
    if bearing_deg < -dead_zone_deg:
        return "left"
    if bearing_deg > dead_zone_deg:
        return "right"
    return "center"


def estimate_bearing(
    stereo: np.ndarray, mic_distance_m: float = DEFAULT_MIC_DISTANCE_M, fs: int = SAMPLE_RATE
) -> float:
    """Bearing (degrees) for an (n, 2) int16 stereo segment; ch0=L, ch1=R."""
    max_delay = mic_distance_m / SPEED_OF_SOUND
    delay = gcc_phat(stereo[:, 0], stereo[:, 1], max_delay_s=max_delay, fs=fs)
    return bearing_from_delay(delay, mic_distance_m)
