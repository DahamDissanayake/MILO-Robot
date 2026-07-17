"""Live tokens/sec tracking for the TUI's model panel."""

from __future__ import annotations

import time
from collections import deque


class TokenRateTracker:
    """Tracks LLM token throughput for the dashboard's up/down indicator.

    ``tokens_per_sec_out`` is a genuinely live rolling rate over WINDOW_S,
    fed by one ``record_output_token()`` call per streamed chunk -- Ollama's
    streaming granularity for /api/chat is one token per non-empty content
    chunk during generation. Ollama evaluates the prompt synchronously
    before the first token, so there's no per-chunk signal for the "up"
    side -- ``tokens_per_sec_in`` is just the most recently measured
    prompt-eval rate, updated once per exchange rather than continuously.
    """

    WINDOW_S = 2.0

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._output_times: deque[float] = deque()
        self._last_prompt_rate = 0.0

    def record_output_token(self) -> None:
        self._output_times.append(self._clock())
        self._trim()

    def record_prompt_eval(self, token_count: int, duration_ns: int) -> None:
        self._last_prompt_rate = token_count / (duration_ns / 1e9) if duration_ns else 0.0

    def _trim(self) -> None:
        cutoff = self._clock() - self.WINDOW_S
        while self._output_times and self._output_times[0] < cutoff:
            self._output_times.popleft()

    @property
    def tokens_per_sec_out(self) -> float:
        self._trim()
        return len(self._output_times) / self.WINDOW_S

    @property
    def tokens_per_sec_in(self) -> float:
        return self._last_prompt_rate
