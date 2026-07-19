# TUI conversation view + model-ready status + graceful LLM degrade

Date: 2026-07-19

## Problem

- When the LLM call fails (e.g. Ollama 500 "model requires more system memory
  than is available"), the error propagates out of `CognitionAgent.on_utterance`
  and crashes `_handle_segment` with a full traceback on **every** utterance;
  the robot just goes silent with no indication why.
- The brain TUI's home screen shows pipeline load status but **not the
  conversation** — the operator can't see what Milo heard and what it replied
  without tailing the raw log.
- The Model panel shows the model name + token rate but not **whether the model
  is actually able to respond right now** (loaded/reachable vs erroring).

## Goals

- An LLM failure degrades gracefully: Milo gives a short fallback reply, the
  failure is logged once (concise, not a per-utterance traceback), and the
  reason is visible in the TUI.
- The TUI home screen has a live **conversation view**: recent "You: …" /
  "Milo: …" exchanges.
- The Model panel shows a **ready/responding/error** indicator for the LLM.

## Non-goals

- No change to model selection or Ollama itself (the memory/model fix is an ops
  action — use a model that fits).
- No change to ASR/TTS/VAD/vision behavior.

## Design

### A. LLM status + graceful degrade (`brain/milo_brain/llm/agent.py`)

`OllamaClient` tracks its own liveness:

```python
self.status = "unknown"   # "unknown" | "responding" | "ready" | "error"
self.error: str | None = None
```

`chat()` sets `status="responding"` before the request, `status="ready"` on a
clean response, and on any exception sets `status="error"`, records a short
`self.error` (e.g. the HTTP status text / message), and re-raises.

`CognitionAgent.on_utterance` wraps the tool-loop LLM calls so a failure never
crashes the session:

```python
try:
    for _ in range(MAX_TOOL_ROUNDS):
        message = await self._llm.chat(...)
        ...
except Exception as exc:
    log.warning("LLM call failed (%s); giving a fallback reply", exc)
    result = AgentResult(reply="Sorry — my mind went blank for a second. Can you say that again?")
    # skip fact-writing / name-learning for a failed turn
    self._history.append({"role": "assistant", "content": result.reply})
    return result
```

The fallback reply still flows to TTS so Milo says *something*; the LLM's
`status`/`error` carry the real reason for the TUI. The log line is a single
`warning`, not an exception traceback, so a persistent failure doesn't spam.

### B. Conversation log (`brain/milo_brain/conversation.py`, `session.py`)

A small ring buffer of exchanges, owned by the factory and shared across
sessions:

```python
@dataclass(frozen=True)
class Exchange:
    heard: str
    reply: str
    ts: float

class ConversationLog:
    def __init__(self, maxlen: int = 50): ...
    def add(self, heard: str, reply: str) -> None: ...
    def recent(self, n: int) -> list[Exchange]: ...
```

`CognitionSessionFactory` constructs one `ConversationLog` and passes it to each
`RobotCognitionSession`. In `_handle_segment`, after a reply is produced, the
session records `conversation.add(transcript.text, result.reply)` (only when
there's a transcript above the confidence gate — the same point it currently
logs "heard"). The factory exposes it as `factory.conversation`, and the LLM
status as `factory.llm_status` (reading `self._llm.status`/`.error`).

### C. TUI: conversation panel + model-ready (`brain/milo_brain/tui/dashboard.py`)

- New `ChatPanel` on `DashboardScreen`: renders the last few exchanges from
  `factory.conversation.recent(n)` as `You: <heard>` / `Milo: <reply>` lines
  (most recent at the bottom), or a muted "no conversation yet" placeholder.
- `ModelPanel.render_model` gains a status line derived from `factory.llm_status`:
  `Model: ready` / `Model: responding…` / `Model: error — <reason>` /
  `Model: —` (unknown). When `factory is None` (pipeline deps missing) it shows
  `Model: —`.
- `DashboardScreen.refresh_from(connector, cfg, rate_tracker, factory=None)`
  already receives `factory`; it now also drives `ChatPanel` and passes the LLM
  status into `ModelPanel`. All on the existing 1s poll — no new timers.

## Error handling

- A failed LLM turn returns a fallback and does not write facts/learn names
  (avoids persisting garbage from a broken turn).
- `factory.conversation`/`llm_status` are read-only from the TUI; a `None`
  factory degrades to the "—"/placeholder states.

## Testing

- `agent.py`: an LLM whose `chat` raises → `on_utterance` returns the fallback
  reply (no exception), logs once, and the client `status` is "error"; a
  successful call leaves `status="ready"`.
- `conversation.py`: `add`/`recent` ring-buffer semantics (bounded, ordered).
- `session.py`: a completed hearing→reply records one `Exchange`; a
  below-confidence / empty transcript records nothing.
- `dashboard.py`: `ChatPanel` renders recent exchanges via a fake factory;
  `ModelPanel` shows the ready/responding/error/— states; a `None` factory
  degrades cleanly. (Driven through `app.run_test()` like the existing panels.)
