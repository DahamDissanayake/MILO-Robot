# TTS: female voice everywhere

Date: 2026-07-20

## Problem

Milo's replies sometimes come out in a deep male voice. There are two
independent TTS paths and both default to a male voice:

1. **Brain-side speech** (`brain/milo_brain/pipelines/tts.py`, `PiperTts`) —
   `BrainConfig.piper_voice` (`brain/milo_brain/config.py:62`) defaults to
   `"en_US-lessac-medium"`, a male Piper voice. This is the voice used for
   every spoken reply when the brain is connected to a robot.
2. **Web dashboard text-to-speech** (`bridge/milo_bridge/webapp/api/speak.py`)
   — `synth_pcm()` shells out to `espeak-ng --stdout -a 120 -- <text>` with no
   `-v` voice flag, so it uses espeak-ng's unspecified default voice, which is
   male.

Neither path lets the voice vary per request — each is a single fixed
default — so the fix is changing both defaults, not adding voice-selection
logic.

## Goals

- Brain-side spoken replies use a female Piper voice by default.
- Web dashboard "speak" (text input → TTS → robot speaker) uses a female
  espeak-ng voice.
- No behavior change beyond the voice identity (same audio pipeline, same
  latency characteristics, same error handling).

## Non-goals

- No per-user/per-request voice selection UI — this is a default-value fix.
- No change to `PiperTts`'s load/download/degrade behavior (already handled
  by `docs/superpowers/specs/2026-07-19-tts-resilience-and-disconnect-design.md`).
- No change to the audio pipeline, framing, or resampling.

## Design

### A. Brain default voice (`brain/milo_brain/config.py`, `brain/README.md`)

```python
piper_voice: str = "en_US-amy-medium"
```

`en_US-amy-medium` is a standard female US-English Piper voice at the same
"medium" quality tier as the current default, so there's no change in model
size, download behavior, or synthesis speed on the Pi/brain host. Update the
config table in `brain/README.md` to match.

### B. Web dashboard espeak-ng voice (`bridge/milo_bridge/webapp/api/speak.py`)

```python
async def synth_pcm(text: str, timeout_s: float = 10.0) -> bytes | None:
    proc = await asyncio.create_subprocess_exec(
        "espeak-ng", "-v", "en+f3", "--stdout", "-a", "120", "--", text,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    ...
```

`en+f3` is espeak-ng's built-in female voice variant of the standard English
voice — no new dependency, no extra download, same binary already required by
`tts_available()`.

## Error handling

Unchanged in both paths: `PiperTts.synthesize` still degrades to silence and
logs once if the (now-female) voice can't be loaded; `synth_pcm` still
returns `None` on a non-zero exit code or timeout. Changing the voice name
doesn't change any failure mode.

## Testing

- `brain/tests/test_pipelines.py`, `brain/tests/test_tui_dashboard.py`: any
  assertion relying on the old default voice name (`"en_US-lessac-medium"`)
  either passes an explicit voice (already does, unaffected) or gets updated
  to the new default where it asserts against `PiperTts()`'s zero-arg default.
- `bridge/tests` covering `/api/speak`: assert `synth_pcm`'s subprocess argv
  includes `"-v", "en+f3"` (via the existing subprocess-mocking pattern in
  that test file, if present) — otherwise this is a one-line manual/log
  verification since the existing tests mock `asyncio.create_subprocess_exec`
  generically.
- Manual verification: one real Piper voice download + one real espeak-ng
  call, listened to, per this repo's existing practice of manually verifying
  audio changes (see the TTS resilience spec's testing section).
