"""Speaker screen: plays a generated tone via AudioIO, no dependency on the mic screen."""

from __future__ import annotations

import asyncio

import numpy as np
from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from milo_bridge.drivers.audio import SAMPLE_RATE, AudioIO

from iot_tester.results_log import ResultRecorder
from iot_tester.widgets import ask_pass_fail

TONE_HZ = 440.0
TONE_DURATION_S = 1.0


def generate_tone(
    frequency_hz: float = TONE_HZ, duration_s: float = TONE_DURATION_S, sample_rate: int = SAMPLE_RATE
) -> bytes:
    """Mono s16le PCM sine tone."""
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    tone = np.sin(2 * np.pi * frequency_hz * t)
    pcm = (tone * 32767 * 0.8).astype(np.int16)
    return pcm.tobytes()


class SpeakerScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_mount(self) -> None:
        self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        try:
            audio = AudioIO()
            await container.mount(Static(f"Playing a {TONE_HZ:.0f} Hz test tone..."))
            await asyncio.to_thread(audio.play_pcm, generate_tone())
            audio.close()
        except Exception as exc:
            await container.mount(Static(f"Could not play audio: {exc}"))
            self.recorder.record("Speaker", "Tone playback", False, note=str(exc))
            self.recorder.flush()
            return

        passed, note = await ask_pass_fail(container, "Did you hear a clear tone?")
        self.recorder.record("Speaker", "Tone playback", passed, note)
        self.recorder.flush()

        await container.mount(Static("Speaker test complete. Press Escape to return to menu."))
