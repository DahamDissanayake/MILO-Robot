"""Microphones screen: records via AudioIO, live L/R RMS meter, saves a WAV."""

from __future__ import annotations

import asyncio
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from milo_bridge.drivers.audio import SAMPLE_RATE, AudioIO, rms

from iot_tester.results_log import ResultRecorder
from iot_tester.widgets import ask_pass_fail

RECORD_SECONDS = 3.0
RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"


def split_channels(pcm: bytes) -> tuple[bytes, bytes]:
    """Deinterleave stereo s16le PCM into (left, right) mono byte strings."""
    samples = np.frombuffer(pcm, dtype=np.int16)
    left = samples[0::2].tobytes()
    right = samples[1::2].tobytes()
    return left, right


def level_bar(level: float, max_level: float = 4000.0, width: int = 30) -> str:
    """ASCII bar for an RMS level, clamped to [0, width]."""
    filled = min(width, max(0, int(level / max_level * width)))
    return "#" * filled + "-" * (width - filled)


def save_wav(path: Path, pcm: bytes, channels: int = 2, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)


class MicScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="mic-levels")
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_mount(self) -> None:
        self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        levels = self.query_one("#mic-levels", Static)
        audio = AudioIO()

        await container.mount(
            Static(f"Recording {RECORD_SECONDS:.0f}s -- speak or clap near each mic...")
        )
        chunks: list[bytes] = []
        deadline = asyncio.get_running_loop().time() + RECORD_SECONDS
        try:
            async for frame in audio.capture_frames():
                chunks.append(frame)
                left, right = split_channels(frame)
                levels.update(f"L [{level_bar(rms(left))}]\nR [{level_bar(rms(right))}]")
                if asyncio.get_running_loop().time() >= deadline:
                    break
        except Exception as exc:
            await container.mount(Static(f"Recording failed: {exc}"))
            self.recorder.record("Microphones", "Recording", False, note=str(exc))
            self.recorder.flush()
            return

        pcm = b"".join(chunks)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        wav_path = RESULTS_DIR / f"mic-test-{timestamp}.wav"
        save_wav(wav_path, pcm)
        await container.mount(Static(f"Saved recording to {wav_path.name}"))

        passed, note = await ask_pass_fail(
            container, "Did the level meter respond when you spoke/clapped near each mic?"
        )
        self.recorder.record("Microphones", "Recording", passed, note)
        self.recorder.flush()

        await container.mount(Static("Microphone tests complete. Press Escape to return to menu."))
