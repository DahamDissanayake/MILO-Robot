# TUI Conversation View + Model-Ready + Graceful LLM Degrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failing LLM degrades gracefully (fallback reply, logged once, reason surfaced) instead of crash-spamming; the brain TUI home screen shows a live conversation view (You/Milo) and whether the model is ready to respond.

**Architecture:** (1) `OllamaClient` tracks a `status`/`error`; `CognitionAgent.on_utterance` catches LLM failures and returns a fallback. (2) A new `ConversationLog` ring buffer, owned by `CognitionSessionFactory`, is written by `RobotCognitionSession` on each reply. (3) `DashboardScreen` gains a `ChatPanel` and a model-ready line on `ModelPanel`, driven by the existing 1s poll via the `factory` it already receives.

**Tech Stack:** Python 3.14, Textual, pytest + pytest-asyncio.

## Global Constraints

- A failed LLM turn must NOT raise out of `on_utterance` — it returns a fallback `AgentResult` and logs a single `warning` (no per-utterance traceback).
- `OllamaClient.status` values are exactly: `"unknown" | "responding" | "ready" | "error"`.
- No unit test hits a real Ollama / real model — the LLM client is faked (existing pattern).
- Brain suite run from `brain/` after each task (baseline 158).
- Commit messages: no AI co-author trailer.

---

### Task 1: LLM status + graceful degrade

**Files:**
- Modify: `brain/milo_brain/llm/agent.py`
- Test: `brain/tests/test_agent.py`

**Interfaces:**
- Produces: `OllamaClient.status` (`"unknown"|"responding"|"ready"|"error"`) and `.error: str | None`, updated by `chat()`. `CognitionAgent.on_utterance` never raises on an LLM failure — it returns `AgentResult(reply=<fallback>)`, logs once, and skips fact-writing/name-learning for that turn.

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_agent.py`:

```python
def test_ollama_client_status_tracks_success(monkeypatch):
    import httpx
    def fake_stream(self, method, url, json):
        return _FakeStreamCtx([
            json_lib.dumps({"message": {"role": "assistant", "content": "hi"}, "done": True,
                            "prompt_eval_count": 1, "prompt_eval_duration": 1_000_000}),
        ])
    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    client = OllamaClient()
    assert client.status == "unknown"
    asyncio_run_chat(client, "sys", [{"role": "user", "content": "hey"}])
    assert client.status == "ready"
    assert client.error is None


def test_ollama_client_status_goes_error_on_failure(monkeypatch):
    import httpx
    class _BoomCtx:
        async def __aenter__(self): raise RuntimeError("500 boom")
        async def __aexit__(self, *e): return False
    monkeypatch.setattr(httpx.AsyncClient, "stream", lambda self, m, u, json: _BoomCtx())
    client = OllamaClient()
    import pytest as _pytest
    with _pytest.raises(RuntimeError):
        asyncio_run_chat(client, "sys", [{"role": "user", "content": "hey"}])
    assert client.status == "error"
    assert client.error and "boom" in client.error


def test_on_utterance_gives_a_fallback_when_the_llm_fails(caplog):
    import logging

    class _FailingLlm:
        async def chat(self, system, messages, tools=None):
            raise RuntimeError("500 Internal Server Error")

    graph = FakeGraph()
    agent = CognitionAgent(_FailingLlm(), graph, FakeMcp())
    with caplog.at_level(logging.WARNING, logger="milo_brain.llm.agent"):
        result = asyncio.run(agent.on_utterance("hello", DAHAM, None))
    assert result.reply  # a non-empty fallback, not a crash
    assert "blank" in result.reply.lower() or "again" in result.reply.lower()
    # a failed turn writes no facts
    assert not any(op == "upsert_node" and kw.get("type") == "fact" for op, kw in graph.calls)
    assert any(r.levelno == logging.WARNING for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `brain/`): `../.venv/Scripts/python.exe -m pytest tests/test_agent.py -k "status or fallback" -v`
Expected: FAIL — `OllamaClient` has no `status`; `on_utterance` currently propagates the LLM error.

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/llm/agent.py`, `OllamaClient.__init__` — after `self._rate_tracker = rate_tracker`, add:

```python
        self.status = "unknown"  # "unknown" | "responding" | "ready" | "error"
        self.error: str | None = None
```

In `OllamaClient.chat`, set status around the request. Change the method body so that right after `import httpx` it sets `self.status = "responding"`, wrap the `async with httpx.AsyncClient(...)` block in try/except, and set ready/error accordingly. Concretely, replace from `import httpx` down through the `async with ... as response:` streaming block so it reads:

```python
        import httpx

        self.status = "responding"
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        else:
            payload["format"] = "json"

        role = "assistant"
        content_parts: list[str] = []
        tool_calls: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        message = chunk.get("message") or {}
                        role = message.get("role", role)
                        piece = message.get("content", "")
                        if piece:
                            content_parts.append(piece)
                            if self._rate_tracker is not None:
                                self._rate_tracker.record_output_token()
                        if message.get("tool_calls"):
                            tool_calls = message["tool_calls"]
                        if chunk.get("done") and self._rate_tracker is not None:
                            count = chunk.get("prompt_eval_count")
                            duration = chunk.get("prompt_eval_duration")
                            if count is not None and duration is not None:
                                self._rate_tracker.record_prompt_eval(count, duration)
        except Exception as exc:
            self.status = "error"
            self.error = str(exc)[:200]
            raise

        self.status = "ready"
        self.error = None
        result: dict = {"role": role, "content": "".join(content_parts)}
        if tool_calls:
            result["tool_calls"] = tool_calls
        return result
```

In `CognitionAgent.on_utterance`, wrap the tool-round loop so a failure returns a fallback. Replace the block:

```python
        result = AgentResult(reply="Sorry, I got a bit stuck there.")
        for _ in range(MAX_TOOL_ROUNDS):
            message = await self._llm.chat(SYSTEM_PROMPT, messages, tools=tools)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                result = sanitize(parse_llm_json(message.get("content", "")))
                break
            messages.append({"role": "assistant", "content": message.get("content", ""), "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                arguments = fn.get("arguments") or {}
                if self._mcp is not None:
                    tool_result = await self._mcp.call_tool(name, **arguments)
                else:
                    tool_result = {"ok": False, "error": "mcp unavailable"}
                messages.append({"role": "tool", "name": name, "content": json.dumps(tool_result)})
```

with:

```python
        result = AgentResult(reply="Sorry, I got a bit stuck there.")
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                message = await self._llm.chat(SYSTEM_PROMPT, messages, tools=tools)
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    result = sanitize(parse_llm_json(message.get("content", "")))
                    break
                messages.append({"role": "assistant", "content": message.get("content", ""), "tool_calls": tool_calls})
                for call in tool_calls:
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    arguments = fn.get("arguments") or {}
                    if self._mcp is not None:
                        tool_result = await self._mcp.call_tool(name, **arguments)
                    else:
                        tool_result = {"ok": False, "error": "mcp unavailable"}
                    messages.append({"role": "tool", "name": name, "content": json.dumps(tool_result)})
        except Exception as exc:
            log.warning("LLM call failed (%s); giving a fallback reply", exc)
            fallback = AgentResult(reply="Sorry — my mind went blank for a second. Can you say that again?")
            self._history.append({"role": "assistant", "content": fallback.reply})
            return fallback
```

(The lines after the loop — `self._history.append(...)`, `_maybe_learn_name`, `_write_facts`, `return replace(...)` — are unchanged and run only on the success path.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_agent.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full brain suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all pass.

```bash
git add brain/milo_brain/llm/agent.py brain/tests/test_agent.py
git commit -m "feat(brain): track LLM status and give a fallback reply instead of crashing on an LLM error"
```

---

### Task 2: ConversationLog + session records + factory exposure

**Files:**
- Create: `brain/milo_brain/conversation.py`
- Modify: `brain/milo_brain/session.py`
- Test: `brain/tests/test_conversation.py` (new), `brain/tests/test_cognition_session.py`

**Interfaces:**
- Produces: `ConversationLog(maxlen=50)` with `add(heard, reply)` and `recent(n) -> list[Exchange]` (oldest→newest of the last n); `Exchange(heard, reply, ts)`. `RobotCognitionSession.__init__` gains `conversation: ConversationLog | None = None`; `_handle_segment` records `(transcript.text, result.reply)` after a reply. `CognitionSessionFactory` owns a `ConversationLog` (attr `conversation`), passes it to each session, and exposes `llm_status() -> tuple[str, str | None]` reading `self._llm.status`/`.error`.

- [ ] **Step 1: Write the failing tests**

Create `brain/tests/test_conversation.py`:

```python
from milo_brain.conversation import ConversationLog, Exchange


def test_add_and_recent_are_ordered_oldest_to_newest():
    log = ConversationLog(maxlen=10)
    log.add("hi", "hello")
    log.add("how are you", "good")
    recent = log.recent(5)
    assert [(e.heard, e.reply) for e in recent] == [("hi", "hello"), ("how are you", "good")]
    assert all(isinstance(e, Exchange) for e in recent)


def test_recent_caps_at_n_and_buffer_is_bounded():
    log = ConversationLog(maxlen=3)
    for i in range(5):
        log.add(f"q{i}", f"a{i}")
    assert [e.heard for e in log.recent(10)] == ["q2", "q3", "q4"]  # maxlen drops oldest
    assert [e.heard for e in log.recent(2)] == ["q3", "q4"]         # recent(n) caps
```

In `brain/tests/test_cognition_session.py`, add a test that a completed hearing→reply records an exchange. Use the existing `build_session` (add a `ConversationLog` to it): change `build_session` to construct and attach one, or add a focused test constructing a `RobotCognitionSession` with a `ConversationLog` and driving one segment. Simplest focused test:

```python
def test_session_records_a_conversation_exchange():
    from milo_brain.conversation import ConversationLog

    def answers(op, header):
        if op == "match_face":
            return {"match": {"id": 1, "type": "person", "props": {"name": "Daham"}}, "similarity": 0.98}
        return {"neighbors": []} if op == "neighbors" else {"nodes": []} if op == "recent_events" else {}

    async def main():
        brain_sock, robot_sock = socket_pair()
        graph = GraphClient(brain_sock)
        convo = ConversationLog()
        session = RobotCognitionSession(
            brain_sock, Peer(id="milo-1", name="milo"),
            vad=VadSegmenter(is_speech=energy_detector, min_silence_ms=60, pre_roll_frames=2),
            asr=FakeAsr(), vision=FakeVision(), tts=FakeTts(),
            agent=CognitionAgent(FakeLlm(), graph, FakeMcp()), graph=graph, mcp=FakeMcp(),
            conversation=convo,
        )

        async def robot():
            while True:
                msg = await robot_sock.recv()
                if msg.t == protocol.T_GRAPH:
                    await robot_sock.send(protocol.T_GRAPH_RESULT, id=msg.get("id"), **answers(msg.get("op"), dict(msg.header)))

        robot_task = asyncio.create_task(robot())
        # Drive one closed speech segment straight through _handle_segment.
        seg = session._vad  # build a segment via the same helper the loop uses
        # Feed loud frames then silence so a segment closes:
        segment = None
        t = 0.0
        for loud in [True] * 10 + [False] * 5:
            segment = session._vad.push(loud_frame() if loud else quiet_frame(), t) or segment
            t += 0.02
        assert segment is not None
        await session._handle_segment(segment)
        robot_task.cancel()
        return convo.recent(5)

    exchanges = asyncio.run(main())
    assert len(exchanges) == 1
    assert exchanges[0].heard == "hello milo"      # FakeAsr transcript
    assert exchanges[0].reply == "Hey Daham!"      # FakeLlm reply
```

(`FakeAsr`/`FakeLlm`/`FakeTts`/`FakeMcp`/`loud_frame`/`quiet_frame`/`energy_detector`/`socket_pair`/`GraphClient`/`Peer`/`VadSegmenter`/`CognitionAgent`/`protocol` are all already imported/defined in this test file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_conversation.py tests/test_cognition_session.py -k "conversation or records" -v`
Expected: FAIL — `conversation` module doesn't exist; `RobotCognitionSession` has no `conversation` param.

- [ ] **Step 3: Write the implementation**

Create `brain/milo_brain/conversation.py`:

```python
"""A small bounded log of spoken exchanges (what Milo heard and replied),
shown live on the brain TUI's conversation view."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Exchange:
    heard: str
    reply: str
    ts: float


class ConversationLog:
    def __init__(self, maxlen: int = 50):
        self._items: deque[Exchange] = deque(maxlen=maxlen)

    def add(self, heard: str, reply: str) -> None:
        self._items.append(Exchange(heard=heard, reply=reply, ts=time.time()))

    def recent(self, n: int) -> list[Exchange]:
        items = list(self._items)
        return items[-n:] if n < len(items) else items
```

In `brain/milo_brain/session.py`, `RobotCognitionSession.__init__`: add `conversation=None` to the signature (keyword, after `face_match_threshold`) and store it:

```python
        face_match_threshold: float = 0.45,
        conversation=None,
    ):
        ...
        self._conversation = conversation
```

In `_handle_segment`, record the exchange after the reply is produced. Change:

```python
        result = await self._agent.on_utterance(
            transcript.text, self._current_person, self._current_embedding_b64
        )
        await self._respond(result)
```

to:

```python
        result = await self._agent.on_utterance(
            transcript.text, self._current_person, self._current_embedding_b64
        )
        if self._conversation is not None and result.reply:
            self._conversation.add(transcript.text, result.reply)
        await self._respond(result)
```

In `CognitionSessionFactory.__init__`, after `self.current_session ... = None` (from the pipeline-status work), add:

```python
        from .conversation import ConversationLog
        self.conversation = ConversationLog()
```

Add a method to `CognitionSessionFactory`:

```python
    def llm_status(self) -> tuple[str, str | None]:
        return (getattr(self._llm, "status", "unknown"), getattr(self._llm, "error", None))
```

In `CognitionSessionFactory.handle`, pass the conversation log into the session — add `conversation=self.conversation,` to the `RobotCognitionSession(...)` constructor call (alongside the existing kwargs).

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_conversation.py tests/test_cognition_session.py -v`
Expected: all pass (including the existing session tests — the new `conversation` kwarg defaults to `None`, so `build_session` without it is unaffected).

- [ ] **Step 5: Run the full brain suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all pass.

```bash
git add brain/milo_brain/conversation.py brain/milo_brain/session.py brain/tests/test_conversation.py brain/tests/test_cognition_session.py
git commit -m "feat(brain): record spoken exchanges in a ConversationLog exposed by the factory"
```

---

### Task 3: TUI conversation panel + model-ready indicator

**Files:**
- Modify: `brain/milo_brain/tui/dashboard.py`
- Test: `brain/tests/test_tui_dashboard.py`

**Interfaces:**
- Consumes: `factory.conversation.recent(n)` and `factory.llm_status()` (Task 2).
- Produces: a new `ChatPanel` on `DashboardScreen`; `ModelPanel.render_model` gains an `llm_status` argument rendered as a `Model: ready|responding…|error — <reason>|—` line; `refresh_from` drives both from `factory` (unchanged signature).

- [ ] **Step 1: Write the failing tests**

In `brain/tests/test_tui_dashboard.py`, add `ChatPanel` to the dashboard import, and add:

```python
def test_model_panel_shows_llm_ready_state():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small", llm_model="llama3.2:3b")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self): return {}
            def llm_status(self): return ("ready", None)
            class _Convo:
                def recent(self, n): return []
            conversation = _Convo()

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            model = str(screen.query_one(ModelPanel).content)
            assert "ready" in model


def test_model_panel_shows_llm_error_reason():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self): return {}
            def llm_status(self): return ("error", "500 out of memory")
            class _Convo:
                def recent(self, n): return []
            conversation = _Convo()

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            model = str(screen.query_one(ModelPanel).content)
            assert "error" in model and "out of memory" in model

    asyncio.run(scenario())


def test_chat_panel_shows_recent_exchanges():
    async def scenario():
        from milo_brain.conversation import Exchange
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self): return {}
            def llm_status(self): return ("ready", None)
            class _Convo:
                def recent(self, n):
                    return [Exchange(heard="hi milo", reply="hey there", ts=0.0)]
            conversation = _Convo()

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            chat = str(screen.query_one(ChatPanel).content)
            assert "hi milo" in chat and "hey there" in chat

    asyncio.run(scenario())


def test_chat_panel_and_model_degrade_when_factory_is_none():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())  # no factory
            chat = str(screen.query_one(ChatPanel).content)
            model = str(screen.query_one(ModelPanel).content)
            assert "no conversation" in chat.lower()
            assert "Model:" in model  # renders a "—" state without crashing

    asyncio.run(scenario())
```

Wrap the first test's `asyncio.run(scenario())` call in too (mirror the others).

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_dashboard.py -v`
Expected: FAIL — `ChatPanel` doesn't exist; `ModelPanel` shows no LLM status.

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/tui/dashboard.py`:

Change `ModelPanel.render_model` to accept and render the LLM status. Replace the method:

```python
class ModelPanel(Static):
    def render_model(
        self, llm_model: str, whisper_model: str, piper_voice: str,
        tokens_per_sec_in: float, tokens_per_sec_out: float,
        llm_status: tuple[str, str | None] = ("unknown", None),
    ) -> None:
        state, error = llm_status
        if state == "error" and error:
            ready_line = f"Model: error — {error}"
        elif state == "responding":
            ready_line = "Model: responding…"
        elif state == "ready":
            ready_line = "Model: ready"
        else:
            ready_line = "Model: —"
        self.update(
            f"[b]Model[/b]\n"
            f"LLM: {llm_model}\n"
            f"Whisper: {whisper_model}\n"
            f"Piper: {piper_voice}\n"
            f"Tokens/s  in: {tokens_per_sec_in:.1f} ^   out: {tokens_per_sec_out:.1f} v\n"
            f"{ready_line}\n"
            f"[dim](m to change model)[/dim]"
        )
```

Add a `ChatPanel` class (after `PipelinesPanel`):

```python
class ChatPanel(Static):
    MAX_SHOWN = 6

    def render_chat(self, exchanges) -> None:
        lines = ["[b]Conversation[/b]"]
        if not exchanges:
            lines.append("[dim]no conversation yet[/dim]")
        else:
            for ex in exchanges:
                lines.append(f"[b]You:[/b] {ex.heard}")
                lines.append(f"[b]Milo:[/b] {ex.reply}")
        self.update("\n".join(lines))
```

In `DashboardScreen.compose`, add the chat panel inside the `Vertical()` after `PipelinesPanel`:

```python
            yield PipelinesPanel(id="pipelines-panel")
            yield ChatPanel(id="chat-panel")
```

In `DashboardScreen.refresh_from`, drive the new panel and the model status. Replace the `ModelPanel` render call and add the `ChatPanel` update:

```python
        llm_status = factory.llm_status() if factory is not None else ("unknown", None)
        self.query_one(ModelPanel).render_model(
            cfg.llm_model, cfg.whisper_model, cfg.piper_voice,
            rate_tracker.tokens_per_sec_in, rate_tracker.tokens_per_sec_out,
            llm_status,
        )
        self.query_one(PipelinesPanel).render_pipelines(
            factory.pipeline_status() if factory is not None else {}
        )
        exchanges = factory.conversation.recent(ChatPanel.MAX_SHOWN) if factory is not None else []
        self.query_one(ChatPanel).render_chat(exchanges)
```

(`ChatPanel` is a `Static`, so the existing `DashboardScreen Static` CSS rule styles it as a bordered panel automatically — no CSS change needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_dashboard.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full brain suite**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all pass, including `test_tui_app.py` (its `_refresh_dashboard` drives `refresh_from`).

- [ ] **Step 6: Manual verification (optional, needs the robot)**

If a robot + a working model are available, connect and speak: the Conversation panel fills with You/Milo lines and the Model panel shows `ready` (or `responding…` mid-reply, `error — …` if the model can't load). If not, the tests are the authoritative check — note honestly.

- [ ] **Step 7: Commit**

```bash
git add brain/milo_brain/tui/dashboard.py brain/tests/test_tui_dashboard.py
git commit -m "feat(brain): TUI conversation view + model-ready indicator on the dashboard"
```
