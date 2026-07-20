# TTS Female Voice Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both of Milo's text-to-speech paths (the brain's Piper voice, and the web dashboard's espeak-ng voice) default to a female voice instead of the current male defaults.

**Architecture:** Two independent, unrelated TTS engines each get a one-line default-value change: `PiperTts`'s voice name (brain-side spoken replies) and `synth_pcm`'s `espeak-ng` invocation (web dashboard text-input speech). No new code paths, no behavior change beyond voice identity.

**Tech Stack:** Python, Piper TTS (`piper` package), espeak-ng (external binary), pytest.

## Global Constraints

- New Piper voice: `en_US-amy-medium` (same "medium" quality tier as the current `en_US-lessac-medium` default — no change in model size or synthesis speed).
- New espeak-ng voice flag: `-v en+f3` (espeak-ng's built-in female English variant — no new dependency).
- No change to error handling, degrade-to-silence behavior, or audio framing in either path.

---

### Task 1: Brain default voice → `en_US-amy-medium`

**Files:**
- Modify: `brain/milo_brain/config.py:62`
- Modify: `brain/milo_brain/pipelines/tts.py:46`
- Modify: `brain/README.md:251`
- Test: `brain/tests/test_pipelines.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `BrainConfig().piper_voice == "en_US-amy-medium"`, `PiperTts()._voice_name == "en_US-amy-medium"` — both are read by `brain/milo_brain/session.py:224` (`PiperTts(cfg.piper_voice, ...)`), unchanged call site.

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_pipelines.py`, near the other `PiperTts`/config tests:

```python
def test_default_piper_voice_is_female():
    from milo_brain.config import BrainConfig
    from milo_brain.pipelines.tts import PiperTts

    assert BrainConfig().piper_voice == "en_US-amy-medium"
    assert PiperTts()._voice_name == "en_US-amy-medium"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain && python -m pytest tests/test_pipelines.py::test_default_piper_voice_is_female -v`
Expected: FAIL — both assertions fail, current default is `"en_US-lessac-medium"`.

- [ ] **Step 3: Change the defaults**

In `brain/milo_brain/config.py`, line 62:

```python
    piper_voice: str = "en_US-amy-medium"
```

In `brain/milo_brain/pipelines/tts.py`, line 46:

```python
    def __init__(self, voice: str = "en_US-amy-medium", voices_dir=None,
                 download=None, loader=None):
```

In `brain/README.md`, line 251:

```markdown
| `piper_voice` | `en_US-amy-medium` | Piper TTS voice model name. |
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain && python -m pytest tests/test_pipelines.py::test_default_piper_voice_is_female -v`
Expected: PASS

- [ ] **Step 5: Run the full brain pipelines test suite to confirm nothing else broke**

Run: `cd brain && python -m pytest tests/test_pipelines.py -v`
Expected: all PASS — the existing `PiperTts` tests (`test_piper_downloads_the_voice_when_missing`, etc.) all pass `voice="en_US-lessac-medium"` explicitly, so they're unaffected by the default change.

- [ ] **Step 6: Commit**

```bash
git add brain/milo_brain/config.py brain/milo_brain/pipelines/tts.py brain/README.md brain/tests/test_pipelines.py
git commit -m "fix: default brain TTS to a female Piper voice (en_US-amy-medium)"
```

---

### Task 2: Web dashboard espeak-ng voice → female

**Files:**
- Modify: `bridge/milo_bridge/webapp/api/speak.py:20`
- Test: `bridge/tests/webapp/test_media_endpoints.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `synth_pcm`'s `asyncio.create_subprocess_exec` call includes `"-v", "en+f3"` in its argv, ahead of `--stdout`.

- [ ] **Step 1: Write the failing test**

Add to `bridge/tests/webapp/test_media_endpoints.py`, near the other `synth_pcm` tests:

```python
async def test_synth_pcm_uses_female_voice(monkeypatch):
    import milo_bridge.webapp.api.speak as speak_mod

    fake_proc = _FakeProc(communicate_result=(b"H" * 44 + b"PCMDATA", b""), returncode=0)
    captured_args = []

    async def fake_create(*args, **kwargs):
        captured_args.extend(args)
        return fake_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
    await speak_mod.synth_pcm("x")
    assert "-v" in captured_args
    assert captured_args[captured_args.index("-v") + 1] == "en+f3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bridge && python -m pytest tests/webapp/test_media_endpoints.py::test_synth_pcm_uses_female_voice -v`
Expected: FAIL — `"-v"` not in `captured_args`, current argv is `["espeak-ng", "--stdout", "-a", "120", "--", text]`.

- [ ] **Step 3: Add the voice flag**

In `bridge/milo_bridge/webapp/api/speak.py`, in `synth_pcm`:

```python
async def synth_pcm(text: str, timeout_s: float = 10.0) -> bytes | None:
    proc = await asyncio.create_subprocess_exec(
        "espeak-ng", "-v", "en+f3", "--stdout", "-a", "120", "--", text,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bridge && python -m pytest tests/webapp/test_media_endpoints.py::test_synth_pcm_uses_female_voice -v`
Expected: PASS

- [ ] **Step 5: Run the full media endpoints test suite to confirm nothing else broke**

Run: `cd bridge && python -m pytest tests/webapp/test_media_endpoints.py -v`
Expected: all PASS — the existing `synth_pcm` tests (`test_synth_pcm_strips_wav_header`, `test_synth_pcm_nonzero_rc_returns_none`, `test_synth_pcm_timeout_kills_process`) all use `fake_create(*args, **kwargs)` which ignores its arguments, so they're unaffected by the extra argv entries.

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/webapp/api/speak.py bridge/tests/webapp/test_media_endpoints.py
git commit -m "fix: web dashboard TTS uses espeak-ng's female voice (en+f3)"
```

---

### Task 3: Manual audio verification

**Files:** none (manual verification only, per this repo's existing practice for audio changes — see `docs/superpowers/specs/2026-07-19-tts-resilience-and-disconnect-design.md`'s testing section).

- [ ] **Step 1: Verify the brain's Piper voice**

On a machine with the brain installed (or in a scratch venv with `piper-tts` installed), run:

```bash
python -c "
from milo_brain.pipelines.tts import PiperTts
tts = PiperTts()
pcm = tts.synthesize('Hello, this is a voice check.')
open('/tmp/voice_check.raw', 'wb').write(pcm)
print(f'{len(pcm)} bytes written, voice={tts._voice_name}')
"
```

This downloads `en_US-amy-medium` on first run. Play `/tmp/voice_check.raw` back (16 kHz mono s16le PCM, e.g. `ffplay -f s16le -ar 16000 -ac 1 /tmp/voice_check.raw`) and confirm it sounds female.

- [ ] **Step 2: Verify the web dashboard's espeak-ng voice**

With `espeak-ng` installed, run:

```bash
espeak-ng -v en+f3 --stdout -a 120 -- "Hello, this is a voice check." > /tmp/voice_check.wav
```

Play `/tmp/voice_check.wav` and confirm it sounds female, distinct from the previous default male `espeak-ng` voice.

- [ ] **Step 3: Note completion**

No commit for this task — it's manual listening verification, not a code change.

## Self-Review Notes

- **Spec coverage:** Task 1 covers spec section A (brain default voice); Task 2 covers spec section B (web dashboard espeak-ng voice); Task 3 covers the spec's manual-verification testing requirement. Spec's "Error handling" section requires no code change (verified: neither task touches the failure-path logic in `PiperTts.synthesize` or `synth_pcm`'s non-zero-rc/timeout handling).
- **No placeholders:** every step has complete, runnable code and exact commands.
- **Type/name consistency:** `_voice_name` (the actual `PiperTts` attribute, confirmed in `tts.py`), `synth_pcm` signature (`text: str, timeout_s: float = 10.0`), and `_FakeProc` (existing test fixture in `test_media_endpoints.py`) are all used exactly as they exist in the codebase today.
