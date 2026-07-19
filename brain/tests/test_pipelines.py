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


class _FakeSileroResult:
    def __init__(self, value: float):
        self._value = value

    def item(self) -> float:
        return self._value


class _FakeSileroModel:
    """Mimics the real Silero model's own minimum-chunk-length check (see
    silero-vad's vad_annotator.forward: raises when sr / n_samples > 31.25,
    i.e. under 512 samples at 16 kHz) so this test fails the same way
    production did if the detector ever hands it a raw 20 ms frame."""

    def __init__(self):
        self.call_sizes: list[int] = []

    def __call__(self, tensor, sr: int) -> _FakeSileroResult:
        n = tensor.shape[-1]
        if sr / n > 31.25:
            raise ValueError("Input audio chunk is too short")
        self.call_sizes.append(n)
        return _FakeSileroResult(1.0)


def test_silero_detector_buffers_20ms_frames_to_the_models_minimum_chunk():
    from milo_brain.pipelines.vad import SileroSpeechDetector

    model = _FakeSileroModel()
    detector = SileroSpeechDetector(model=model)
    frame = np.zeros(FRAME, dtype=np.int16)  # 320 samples: the wire protocol's 20 ms frame

    for _ in range(5):
        detector(frame)  # must never raise "Input audio chunk is too short"

    assert model.call_sizes, "model was never invoked"
    assert all(n >= 512 for n in model.call_sizes)


def test_silero_detector_status_starts_not_loaded_without_injected_model():
    from milo_brain.pipelines.vad import SileroSpeechDetector

    detector = SileroSpeechDetector()
    assert detector.status == "not_loaded"
    assert detector.error is None


def test_silero_detector_status_is_ready_immediately_when_model_injected():
    from milo_brain.pipelines.vad import SileroSpeechDetector

    detector = SileroSpeechDetector(model=_FakeSileroModel())
    assert detector.status == "ready"


def test_vad_segmenter_status_defaults_to_ready_for_a_plain_fake_detector():
    seg = VadSegmenter(is_speech=energy_detector, min_silence_ms=60)
    assert seg.status == "ready"
    assert seg.error is None


def test_vad_segmenter_status_delegates_to_an_injected_silero_detector():
    from milo_brain.pipelines.vad import SileroSpeechDetector

    detector = SileroSpeechDetector(model=_FakeSileroModel())
    seg = VadSegmenter(is_speech=detector, min_silence_ms=60)
    assert seg.status == "ready"
    assert seg.error is None


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


# --- LazyLoad ------------------------------------------------------------

from milo_brain.pipelines._lazy import LazyLoad


class _Loader(LazyLoad):
    def __init__(self, fail: bool = False):
        super().__init__()
        self.fail = fail
        self.load_calls = 0

    def _load(self) -> None:
        self.load_calls += 1
        if self.fail:
            raise RuntimeError("boom")


def test_lazyload_starts_not_loaded():
    loader = _Loader()
    assert loader.status == "not_loaded"
    assert loader.error is None


def test_lazyload_ensure_loaded_transitions_to_ready():
    loader = _Loader()
    loader.ensure_loaded()
    assert loader.status == "ready"
    assert loader.error is None
    assert loader.load_calls == 1


def test_lazyload_ensure_loaded_is_a_noop_once_ready():
    loader = _Loader()
    loader.ensure_loaded()
    loader.ensure_loaded()
    assert loader.load_calls == 1


def test_lazyload_ensure_loaded_transitions_to_error_and_reraises():
    loader = _Loader(fail=True)
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="boom"):
        loader.ensure_loaded()
    assert loader.status == "error"
    assert loader.error == "boom"


def test_lazyload_ensure_loaded_retries_after_a_previous_error():
    loader = _Loader(fail=True)
    import pytest as _pytest

    with _pytest.raises(RuntimeError):
        loader.ensure_loaded()
    loader.fail = False
    loader.ensure_loaded()
    assert loader.status == "ready"
    assert loader.error is None
    assert loader.load_calls == 2


def test_lazyload_status_is_loading_while_load_runs():
    class _Loader(LazyLoad):
        def __init__(self):
            super().__init__()
            self.observed_status_during_load = None

        def _load(self) -> None:
            # Captures what a concurrent dashboard poll would see while
            # this (slow, blocking) call is still in progress.
            self.observed_status_during_load = self.status

    loader = _Loader()
    loader.ensure_loaded()
    assert loader.observed_status_during_load == "loading"
    assert loader.status == "ready"  # settles to ready once _load() returns


def test_whisper_asr_status_starts_not_loaded():
    from milo_brain.pipelines.asr import WhisperAsr

    asr = WhisperAsr()
    assert asr.status == "not_loaded"
    assert asr.error is None


def test_whisper_asr_falls_back_to_cpu_when_the_configured_device_cant_run(monkeypatch):
    """Reproduces a real production failure: faster_whisper's WhisperModel
    constructs fine on a GPU device (ctranslate2 defers CUDA init), but the
    first real transcribe() call throws because cublas64_12.dll isn't
    loadable on this machine. transcribe() must catch that, fall back to
    CPU, and still return the transcript for the utterance that triggered
    it -- not just log and drop it."""
    import faster_whisper

    from milo_brain.pipelines.asr import WhisperAsr

    class _Segment:
        def __init__(self, text):
            self.text = text
            self.avg_logprob = 0.0

    class _FakeModel:
        def __init__(self, model_size, device, compute_type):
            self.device = device

        def transcribe(self, audio, language, beam_size):
            if self.device != "cpu":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return [_Segment(" hello milo")], None

    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeModel)

    asr = WhisperAsr(model_size="small", device="cuda")
    mono = np.zeros(1600, dtype=np.int16)

    result = asr.transcribe(mono)
    assert result.text == "hello milo"
    assert asr._device_in_use == "cpu"

    # Already fell back -- the next call must go straight to cpu, not retry cuda.
    result2 = asr.transcribe(mono)
    assert result2.text == "hello milo"


def test_whisper_asr_reraises_when_already_on_cpu():
    """No fallback left once already on cpu -- a real transcribe failure
    there must propagate, not silently loop."""
    from milo_brain.pipelines.asr import WhisperAsr

    class _FakeModel:
        def __init__(self, model_size, device, compute_type):
            pass

        def transcribe(self, audio, language, beam_size):
            raise RuntimeError("out of memory")

    asr = WhisperAsr(model_size="small", device="cpu")
    asr._model = _FakeModel("small", "cpu", "auto")
    asr._device_in_use = "cpu"
    asr.status = "ready"

    with pytest.raises(RuntimeError, match="out of memory"):
        asr.transcribe(np.zeros(1600, dtype=np.int16))


def test_piper_tts_status_starts_not_loaded():
    from milo_brain.pipelines.tts import PiperTts

    tts = PiperTts()
    assert tts.status == "not_loaded"
    assert tts.error is None


def test_piper_downloads_the_voice_when_missing(tmp_path):
    from milo_brain.pipelines.tts import PiperTts

    calls = {"download": 0, "load": 0}

    def fake_download(name, directory):
        calls["download"] += 1
        (directory / f"{name}.onnx").write_bytes(b"model")  # simulate the fetch

    def fake_loader(model_path):
        calls["load"] += 1
        assert model_path.exists()
        return object()  # a stand-in "voice"; synthesize isn't exercised here

    tts = PiperTts(voice="en_US-lessac-medium", voices_dir=tmp_path,
                   download=fake_download, loader=fake_loader)
    tts.ensure_loaded()
    assert calls["download"] == 1 and calls["load"] == 1
    assert tts.status == "ready"


def test_piper_skips_download_when_the_voice_is_already_present(tmp_path):
    from milo_brain.pipelines.tts import PiperTts

    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"model")
    calls = {"download": 0}

    def fake_download(name, directory):
        calls["download"] += 1

    tts = PiperTts(voice="en_US-lessac-medium", voices_dir=tmp_path,
                   download=fake_download, loader=lambda p: object())
    tts.ensure_loaded()
    assert calls["download"] == 0  # already on disk -> no fetch


def test_piper_synthesize_stays_silent_and_logs_once_on_load_failure(tmp_path, caplog):
    import logging
    from milo_brain.pipelines.tts import PiperTts

    load_attempts = {"n": 0}

    def failing_download(name, directory):
        load_attempts["n"] += 1
        raise RuntimeError("network down")

    tts = PiperTts(voice="en_US-lessac-medium", voices_dir=tmp_path,
                   download=failing_download, loader=lambda p: object())

    with caplog.at_level(logging.WARNING, logger="milo_brain.pipelines.tts"):
        assert tts.synthesize("hello") == b""
        assert tts.synthesize("again") == b""
        assert tts.synthesize("and again") == b""

    assert tts.status == "error"
    assert load_attempts["n"] == 1  # only the first call tried to load; the rest short-circuit
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # logged exactly once, not per-utterance


# --- vision status delegation -----------------------------------------------


def test_face_vision_status_defaults_to_ready_for_a_plain_fake_analyzer():
    vision = FaceVision(analyzer=lambda img: [], clock=lambda: 0.0)
    assert vision.status == "ready"
    assert vision.error is None


def test_face_vision_status_delegates_to_a_lazyload_analyzer():
    class FakeAnalyzerLoader(LazyLoad):
        def _load(self):
            pass

        def __call__(self, img):
            return []

    analyzer = FakeAnalyzerLoader()
    vision = FaceVision(analyzer=analyzer, clock=lambda: 0.0)
    assert vision.status == "not_loaded"
    analyzer.ensure_loaded()
    assert vision.status == "ready"


def test_lazyload_concurrent_ensure_loaded_loads_exactly_once():
    import threading
    import time

    class _SlowLoader(LazyLoad):
        def __init__(self):
            super().__init__()
            self.load_calls = 0

        def _load(self):
            self.load_calls += 1
            time.sleep(0.05)  # widen the race window

    loader = _SlowLoader()
    errors = []

    def call():
        try:
            loader.ensure_loaded()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert loader.load_calls == 1     # only one thread ran _load
    assert loader.status == "ready"


def test_insightface_analyzer_status_starts_not_loaded():
    from milo_brain.pipelines.vision import InsightFaceAnalyzer

    analyzer = InsightFaceAnalyzer()
    assert analyzer.status == "not_loaded"
    assert analyzer.error is None
