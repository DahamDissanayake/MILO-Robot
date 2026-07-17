from milo_brain.llm.token_rate import TokenRateTracker


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def test_tokens_per_sec_out_counts_tokens_within_the_window():
    clock = FakeClock()
    tracker = TokenRateTracker(clock=clock)
    for _ in range(4):
        tracker.record_output_token()
        clock.advance(0.1)
    assert tracker.tokens_per_sec_out == 2.0  # 4 tokens / 2.0s window


def test_tokens_per_sec_out_drops_tokens_older_than_the_window():
    clock = FakeClock()
    tracker = TokenRateTracker(clock=clock)
    tracker.record_output_token()
    clock.advance(3.0)  # older than WINDOW_S (2.0s)
    tracker.record_output_token()
    assert tracker.tokens_per_sec_out == 1 / TokenRateTracker.WINDOW_S


def test_tokens_per_sec_out_is_zero_with_no_tokens_recorded():
    tracker = TokenRateTracker(clock=FakeClock())
    assert tracker.tokens_per_sec_out == 0.0


def test_tokens_per_sec_in_reflects_the_last_prompt_eval():
    tracker = TokenRateTracker(clock=FakeClock())
    tracker.record_prompt_eval(token_count=150, duration_ns=300_000_000)  # 0.3s
    assert tracker.tokens_per_sec_in == 500.0
    tracker.record_prompt_eval(token_count=10, duration_ns=1_000_000_000)  # 1s
    assert tracker.tokens_per_sec_in == 10.0


def test_tokens_per_sec_in_is_zero_before_any_exchange():
    tracker = TokenRateTracker(clock=FakeClock())
    assert tracker.tokens_per_sec_in == 0.0


def test_record_prompt_eval_handles_zero_duration_without_dividing_by_zero():
    tracker = TokenRateTracker(clock=FakeClock())
    tracker.record_prompt_eval(token_count=5, duration_ns=0)
    assert tracker.tokens_per_sec_in == 0.0
