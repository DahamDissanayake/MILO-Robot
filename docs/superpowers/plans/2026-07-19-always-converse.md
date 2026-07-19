# Always-Converse Agent + Face-Display Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Milo always converse via the LLM (identity becomes optional context, not a gate), stop it inventing junk "person" nodes from ordinary speech, and make the OLED face display fail gracefully instead of crash-spamming.

**Architecture:** (1) `CognitionAgent.on_utterance` (brain/milo_brain/llm/agent.py) always calls the LLM, using a face-matched-or-session-remembered speaker as context; name-learning becomes an explicit-only, non-blocking side effect; `extract_name` is made strict. (2) `FaceDisplay._show` (bridge/milo_bridge/drivers/display.py) swallows a device error once and no-ops thereafter.

**Tech Stack:** Python 3.14, Ollama (unchanged), pytest + pytest-asyncio.

## Global Constraints

- The LLM must be called for every non-empty utterance regardless of whether the speaker is identified. Conversation is never gated on face recognition.
- A person node is created ONLY from an explicit self-introduction ("my name is / i am / i'm / call me / this is X") AND only while the speaker is unidentified.
- No unit test hits a real model / real I2C device — LLM, graph, MCP, and the display device are all injected fakes (the suite's existing pattern).
- Brain task: run `python -m pytest` from `brain/` (baseline 157). Bridge task: run from `bridge/` (baseline 397). Full package suite each time.
- Commit messages: no AI co-author trailer.

---

### Task 1: Agent always-converses; strict, non-blocking name learning

**Files:**
- Modify: `brain/milo_brain/llm/agent.py`
- Test: `brain/tests/test_agent.py`

**Interfaces:**
- Produces: `CognitionAgent.on_utterance(transcript, person, face_embedding_b64)` always calls `self._llm.chat(...)` for non-empty input; identity comes from `person` (face match) or a session-remembered `_session_person`; `extract_name` matches only explicit self-introductions (no bare-word fallback); a person node is created only via `_maybe_learn_name` when unidentified + explicit. `AgentResult.new_person_name` is set when a name is learned.
- Removed: `_awaiting_name`, `_pending_embedding`, and the pre-LLM name-collection branches.

- [ ] **Step 1: Update the tests that encode the old gate + write the new ones**

In `brain/tests/test_agent.py`:

Change `test_extract_name_variants`'s bare-word assertion. Replace:

```python
    assert extract_name("Daham") == "Daham"
```

with:

```python
    assert extract_name("Daham") is None  # a bare word is no longer treated as a name
```

Replace the two old-flow tests `test_unknown_person_flow_sets_face_directly_via_mcp` and `test_naming_flow_reprompts_and_sets_confused_face` (delete both) with these three:

```python
def test_unknown_speaker_still_gets_an_llm_reply():
    """The whole bug: an unidentified speaker must still reach the LLM,
    not a canned name-gate."""
    llm = FakeLlm([{"role": "assistant", "content": '{"reply": "Hey there!", "facts": []}'}])
    graph = FakeGraph()
    agent = CognitionAgent(llm, graph, FakeMcp())

    result = asyncio.run(agent.on_utterance("hello there", None, "ZmFrZQ=="))
    assert result.reply == "Hey there!"
    assert len(llm.calls) == 1          # LLM WAS called despite person=None
    # ordinary greeting is not an introduction -> no person node
    person_upserts = [kw for op, kw in graph.calls
                      if op == "upsert_node" and kw.get("type") == "person"]
    assert person_upserts == []


def test_unknown_speaker_who_introduces_themselves_is_learned_and_remembered():
    llm = FakeLlm([
        {"role": "assistant", "content": '{"reply": "Nice to meet you!", "facts": []}'},
        {"role": "assistant", "content": '{"reply": "You said you like robots.", "facts": []}'},
    ])
    graph = FakeGraph()
    agent = CognitionAgent(llm, graph, FakeMcp())

    named = asyncio.run(agent.on_utterance("My name is Sarah", None, "ZmFrZQ=="))
    assert named.new_person_name == "Sarah"
    person_upserts = [kw for op, kw in graph.calls
                      if op == "upsert_node" and kw.get("type") == "person"]
    assert person_upserts and person_upserts[0]["props"]["name"] == "Sarah"
    assert person_upserts[0].get("embedding") == "ZmFrZQ=="  # face embedding attached

    # Next utterance: still no face match (person=None), but Sarah is the
    # session speaker now, so the LLM gets her as context -- not re-asked.
    graph.calls.clear()
    follow = asyncio.run(agent.on_utterance("do you remember me", None, None))
    assert follow.new_person_name is None
    new_people = [kw for op, kw in graph.calls
                  if op == "upsert_node" and kw.get("type") == "person"]
    assert new_people == []            # not created again


def test_unknown_speaker_chatter_creates_no_person_nodes():
    llm = FakeLlm([{"role": "assistant", "content": '{"reply": "Haha okay.", "facts": []}'}])
    graph = FakeGraph()
    agent = CognitionAgent(llm, graph, FakeMcp())
    result = asyncio.run(agent.on_utterance("ehh whatever who cares honestly", None, None))
    assert result.reply == "Haha okay."
    assert result.new_person_name is None
    person_upserts = [kw for op, kw in graph.calls
                      if op == "upsert_node" and kw.get("type") == "person"]
    assert person_upserts == []
```

(The existing `test_known_person_gets_contextual_reply_with_no_tool_calls`, tool-loop, no-mcp, caching, and empty-transcript tests stay as-is — the `person=DAHAM` path is unchanged.)

- [ ] **Step 2: Run tests to verify they fail**

Run (from `brain/`): `../.venv/Scripts/python.exe -m pytest tests/test_agent.py -v`
Expected: the new tests FAIL (old code gates on `person`, so an unknown speaker returns the canned "what's your name?" and never calls the LLM; `extract_name("Daham")` still returns "Daham").

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/llm/agent.py`:

Add `replace` to the dataclasses import at the top:

```python
from dataclasses import dataclass, field, replace
```

Replace `extract_name` (the whole function) with the strict version:

```python
def extract_name(transcript: str) -> str | None:
    """Pull a name only from an explicit self-introduction. Ordinary short
    phrases are NOT treated as names (that created junk person nodes)."""
    text = transcript.strip().rstrip(".!?")
    if not text:
        return None
    match = re.search(
        r"(?:my name is|i am|i'm|call me|this is)\s+([A-Za-z][\w'-]*(?:\s+[A-Z][\w'-]*)?)",
        text,
        flags=re.I,
    )
    return match.group(1).strip().title() if match else None
```

In `CognitionAgent.__init__`, replace the two name-state attributes:

```python
        self._awaiting_name = False
        self._pending_embedding: str | None = None
```

with:

```python
        self._session_person: dict | None = None
```

Replace the entire `on_utterance` method (from `async def on_utterance` through its `return result`) with:

```python
    async def on_utterance(
        self,
        transcript: str,
        person: dict | None,
        face_embedding_b64: str | None,
    ) -> AgentResult:
        if not transcript.strip():
            return AgentResult(reply="")

        # Face recognition, when it succeeds, identifies the speaker; otherwise
        # fall back to whoever we've been talking with this session (a name
        # learned earlier), else an unknown guest. Conversation is NEVER gated
        # on identity -- the LLM always replies.
        if person is not None:
            self._session_person = person
        speaker = person if person is not None else self._session_person

        context = await self._build_context(speaker)
        self._history.append({"role": "user", "content": transcript})
        self._history = self._history[-12:]
        messages = [
            {"role": "user", "content": f"[memory context]\n{context}"},
            *self._history,
        ]
        tools = await self._get_tools()

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

        self._history.append({"role": "assistant", "content": result.reply})
        new_name = await self._maybe_learn_name(transcript, speaker, face_embedding_b64)
        await self._write_facts(self._session_person, result.facts)
        return replace(result, new_person_name=new_name) if new_name else result

    async def _maybe_learn_name(
        self, transcript: str, speaker: dict | None, face_embedding_b64: str | None
    ) -> str | None:
        """Capture a name only when the speaker explicitly introduces themselves
        AND we don't already have them identified -- so ordinary chatter never
        creates junk person nodes."""
        if speaker is not None:
            return None
        name = extract_name(transcript)
        if not name:
            return None
        request: dict = {"type": "person", "props": {"name": name}}
        if face_embedding_b64:
            request["embedding"] = face_embedding_b64
        created = await self._graph.call("upsert_node", **request)
        node = created.get("node")
        if not node:
            return None
        self._session_person = node
        await self._graph.call(
            "upsert_node", type="event", props={"text": f"met {name}"}
        )
        log.info("learned speaker name: %s (node %s)", name, node.get("id"))
        return name
```

Replace `_build_context` to be None-safe (add the unknown-speaker branch at the top; the rest is unchanged):

```python
    async def _build_context(self, person: dict | None) -> str:
        if person is None:
            return (
                "You are talking to someone you have not identified yet. Chat "
                "naturally; you may ask their name once if it feels right, but "
                "don't insist."
            )
        lines = [f"Speaker: {person.get('props', {}).get('name', 'unknown')}"]
        node_id = person.get("id")
        if node_id is not None:
            neighbors = await self._graph.call("neighbors", node_id=node_id, limit=10)
            for item in neighbors.get("neighbors", []):
                node = item.get("node") or {}
                edge = item.get("edge") or {}
                summary = node.get("props", {}).get("text") or node.get("props", {}).get("name")
                if summary:
                    lines.append(f"- {edge.get('type', 'related')}: {summary}")
        recent = await self._graph.call("recent_events", limit=5)
        for node in recent.get("nodes", []):
            text = node.get("props", {}).get("text")
            if text:
                lines.append(f"- recent event: {text}")
        return "\n".join(lines)
```

Replace `_write_facts`'s first line to be None-safe:

```python
    async def _write_facts(self, person: dict | None, facts: list[str]) -> None:
        node_id = person.get("id") if person else None
        for fact in facts:
            created = await self._graph.call("upsert_node", type="fact", props={"text": fact})
            fact_id = created.get("node", {}).get("id")
            if node_id is not None and fact_id is not None:
                await self._graph.call("upsert_edge", src=node_id, dst=fact_id, type="said")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_agent.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full brain suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all pass — in particular `test_cognition_session.py::test_full_hearing_to_speaking_loop` (its fake supplies a matched person, so the LLM path runs) and `test_off_center_speech_calls_turn_via_mcp`.

```bash
git add brain/milo_brain/llm/agent.py brain/tests/test_agent.py
git commit -m "fix(brain): always converse via the LLM; identity is context, not a gate"
```

---

### Task 2: Face display fails gracefully

**Files:**
- Modify: `bridge/milo_bridge/drivers/display.py`
- Test: `bridge/tests/test_display.py`

**Interfaces:**
- Produces: `FaceDisplay._show` never propagates a device error; the first failure sets `self._device_failed = True`, logs once, and every subsequent `_show` is a silent no-op (so `set_face`, `_animate`, `_idle_loop`, `_blink` all run harmlessly on a dead display).

- [ ] **Step 1: Read the existing test file, then write the failing tests**

Read `bridge/tests/test_display.py` first to match its fixtures (how it builds a `FaceDisplay` with a fake device + a temp assets dir). Add tests modeled on that style:

```python
# (adapt fixture construction to match the existing tests in this file)

def test_show_swallows_a_device_error_and_no_ops_after(tmp_path):
    class _FlakyDevice:
        def __init__(self):
            self.calls = 0
        def display(self, image):
            self.calls += 1
            raise RuntimeError("I2C device not found on address: 0x3C")

    device = _FlakyDevice()
    face = _make_face(device, tmp_path)   # use this file's existing helper/pattern

    # set_face must NOT raise even though the device is dead
    asyncio.run(face.set_face("idle"))
    assert face._device_failed is True
    assert device.calls == 1              # tried once

    # a second face op is a silent no-op -- device.display not called again
    asyncio.run(face.set_face("happy"))
    assert device.calls == 1
```

If the file has no reusable helper to build a `FaceDisplay` with a temp assets dir containing an `idle.png`, add a minimal one in the test (create a 128x64 1-bit `idle.png` with PIL in `tmp_path`, then `FaceDisplay(device, tmp_path)`).

- [ ] **Step 2: Run the test to verify it fails**

Run (from `bridge/`): `../.venv/Scripts/python.exe -m pytest tests/test_display.py -k swallows -v`
Expected: FAIL — currently `_show` lets the `RuntimeError` propagate out of `set_face`, and `_device_failed` doesn't exist.

- [ ] **Step 3: Write the implementation**

In `bridge/milo_bridge/drivers/display.py`, add `self._device_failed = False` in `FaceDisplay.__init__` (after `self._device = device`):

```python
    def __init__(self, device, assets_dir: Path, rng: random.Random | None = None):
        self._device = device
        self._device_failed = False
        ...
```

Replace `_show`:

```python
    async def _show(self, image: Image.Image) -> None:
        # device.display() is a blocking I2C transfer of the full frame
        # buffer -- measured ~100ms on hardware, roughly 5x the 20ms budget
        # of the servo tick loop. Calling it directly here would stall the
        # whole event loop for that entire duration every time the face
        # animates. A missing/unwired OLED (no I2C device at 0x3C) raises
        # here; swallow it once and no-op thereafter so a dead display can't
        # kill the idle-loop task or spam tracebacks -- the robot just runs
        # faceless.
        if self._device_failed:
            return
        try:
            await asyncio.to_thread(self._device.display, image)
        except Exception as exc:
            self._device_failed = True
            log.warning("face display unavailable (%s); continuing without a face", exc)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_display.py -v`
Expected: all pass (existing display tests use a working fake device, so they still pass — the try/except is transparent on success).

- [ ] **Step 5: Run the full bridge suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `bridge/`)
Expected: all pass.

```bash
git add bridge/milo_bridge/drivers/display.py bridge/tests/test_display.py
git commit -m "fix(bridge): face display degrades to no-op when the OLED isn't present"
```
