import numpy as np
import pytest

from milo_brain.pipelines import direction as dr
from milo_brain.pipelines.tts import chunk_pcm, resample_s16
from milo_brain.pipelines.vad import VadSegmenter, downmix, stereo_from_bytes
from milo_brain.pipelines.vision import FaceObservation, FaceVision

FS = 16_000
FRAME = FS * 20 // 1000  # samples per 20 ms


# --- GCC-PHAT direction ------------------------------------------------------

def stereo_with_delay(delay_samples: int, n: int = 4096, seed: int = 0) -> np.ndarray:
    """Broadband noise burst; positive delay = right channel lags (source left)."""
    rng = np.random.default_rng(seed)
    src = (rng.normal(0, 3000, n)).astype(np.int16)
    left = src
    right = np.roll(src, delay_samples)
    return np.stack([left, right], axis=1)


def test_gcc_phat_recovers_known_delay():
    for delay in (-5, -2, 0, 2, 5):
        stereo = stereo_with_delay(delay)
        max_delay = dr.DEFAULT_MIC_DISTANCE_M / dr.SPEED_OF_SOUND
        measured = dr.gcc_phat(stereo[:, 0], stereo[:, 1], max_delay_s=max_delay, fs=FS)
        # positive roll = right channel lags = positive relative delay
        assert measured == pytest.approx(delay / FS, abs=1.5 / FS)


def test_bearing_sign_convention():
    # Sound arrives at the left mic first -> negative bearing (left).
    left_source = stereo_with_delay(4)   # right lags
    bearing = dr.estimate_bearing(left_source)
    assert bearing < -10
    right_source = stereo_with_delay(-4)
    assert dr.estimate_bearing(right_source) > 10
    centered = stereo_with_delay(0)
    assert abs(dr.estimate_bearing(centered)) < 5


def test_classify_zones():
    assert dr.classify(-40) == "left"
    assert dr.classify(40) == "right"
    assert dr.classify(5) == "center"


def test_bearing_from_delay_clamps():
    assert abs(dr.bearing_from_delay(1.0)) == 90.0  # absurd delay clamps to +-90


# --- VAD segmentation --------------------------------------------------------

def frame_bytes(loud: bool, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    amplitude = 8000 if loud else 50
    samples = rng.normal(0, amplitude, (FRAME, 2)).astype(np.int16)
    return samples.tobytes()


def energy_detector(mono: np.ndarray) -> bool:
    return float(np.sqrt(np.mean(mono.astype(np.float64) ** 2))) > 1000


def test_vad_segments_speech_between_silence():
    seg = VadSegmenter(is_speech=energy_detector, min_silence_ms=60, pre_roll_frames=2)
    out = []
    t = 0.0
    for loud in [False] * 5 + [True] * 10 + [False] * 5:
        result = seg.push(frame_bytes(loud), t)
        if result is not None:
            out.append(result)
        t += 0.02
    assert len(out) == 1
    segment = out[0]
    # 10 speech frames + pre-roll + trailing silence until the gate closed
    assert len(segment.mono) >= 10 * FRAME
    assert segment.stereo.shape[1] == 2
    assert segment.end_ts > segment.start_ts


def test_vad_ignores_pure_silence():
    seg = VadSegmenter(is_speech=energy_detector, min_silence_ms=60)
    for i in range(50):
        assert seg.push(frame_bytes(False), i * 0.02) is None


def test_vad_force_flushes_marathon_speech():
    seg = VadSegmenter(is_speech=energy_detector, min_silence_ms=60, max_segment_s=0.2)
    results = [seg.push(frame_bytes(True), i * 0.02) for i in range(30)]
    assert any(r is not None for r in results)


def test_stereo_helpers():
    stereo = stereo_with_delay(0, n=FRAME)
    round_tripped = stereo_from_bytes(stereo.tobytes())
    assert np.array_equal(round_tripped, stereo)
    mono = downmix(stereo)
    assert mono.dtype == np.int16 and len(mono) == FRAME


# --- TTS helpers -------------------------------------------------------------

def test_chunk_pcm_sizes_and_padding():
    frame_bytes_n = FS * 20 // 1000 * 2  # 640
    pcm = b"\x01\x02" * 1000  # 2000 bytes -> 3 frames won't divide evenly
    chunks = chunk_pcm(pcm)
    assert all(len(c) == frame_bytes_n for c in chunks)
    assert b"".join(chunks)[: len(pcm)] == pcm
    assert chunk_pcm(b"") == []


def test_resample_halves_and_keeps_duration():
    one_second = np.ones(22050, dtype=np.int16) * 1000
    out = resample_s16(one_second, 22050, 16000)
    assert len(out) == 16000
    assert np.all(np.abs(out.astype(int) - 1000) <= 1)
    same = resample_s16(one_second, 16000, 16000)
    assert len(same) == len(one_second)


# --- vision throttle ---------------------------------------------------------

class FakeAnalyzer:
    def __init__(self):
        self.calls = 0

    def __call__(self, image):
        self.calls += 1
        return [FaceObservation(bbox=(0, 0, 10, 10), embedding=np.ones(512, np.float32))]


def tiny_jpeg() -> bytes:
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (128, 64, 32)).save(buf, format="JPEG")
    return buf.getvalue()


def test_vision_throttles_to_analysis_fps():
    now = {"t": 0.0}
    analyzer = FakeAnalyzer()
    vision = FaceVision(analyzer=analyzer, analysis_fps=2.0, clock=lambda: now["t"])
    jpeg = tiny_jpeg()

    assert vision.process_jpeg(jpeg) is not None   # first frame analyzed
    now["t"] = 0.1
    assert vision.process_jpeg(jpeg) is None       # throttled
    now["t"] = 0.6
    assert vision.process_jpeg(jpeg) is not None   # past the interval
    assert analyzer.calls == 2
    assert len(vision.last_faces) == 1
