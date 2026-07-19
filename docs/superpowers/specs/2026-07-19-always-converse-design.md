# Always converse (decouple the LLM from face recognition) + face-display resilience

Date: 2026-07-19

## Problem

Live session: Milo transcribes speech perfectly but its replies are three
canned lines, the brain shows **0 tokens exchanged**, and the graph fills with
junk "person" nodes ("Bye", "Yeah", "The Home").

Root cause in `brain/milo_brain/llm/agent.py::CognitionAgent.on_utterance`: the
LLM is only reached when `person is not None`, and `person` comes **only** from
face recognition (`_on_video` → `match_face` → `_current_person`). When
face-match doesn't return a known person — which is most of the time — the agent
never calls the LLM and instead runs a hard-coded name-collection state machine:

- `person is None` → "Hi! I don't think we've met — what's your name?"
- next utterance → `extract_name` → "Nice to meet you, X!" / "Sorry, I didn't
  catch your name…"

`extract_name` also treats *any* ≤2-word phrase as a name, creating a person
node for it. So Milo is stuck: it never converses, never uses the LLM (0
tokens), loops on name-asking, and manufactures bogus people.

Separately, on the robot the OLED face display isn't on I2C
(`DeviceNotFoundError: I2C device not found on address: 0x3C`); the error
propagates out of `FaceDisplay._show` and kills the idle-loop task with an
"unretrieved task exception," repeated indefinitely.

## Goals

- Milo **always** replies via the LLM, whether or not it recognizes the speaker.
- Identity is *context*, not a gate: a recognized/known speaker personalizes the
  reply from memory; an unknown speaker is just an anonymous guest.
- A name is learned only when the speaker **explicitly** states one, and only
  while unidentified — ordinary chatter never creates person nodes.
- A name given once persists for the session, so Milo doesn't re-ask every turn
  even if face-match keeps failing.
- The face display failing (no I2C device) degrades to a no-op, logged once —
  never a repeated traceback, never a killed task; the robot runs faceless.

## Non-goals

- Face recognition itself is unchanged; it remains a *bonus* identity signal, no
  longer a prerequisite for conversation.
- No change to the LLM/Ollama client, the tool-calling loop, or fact-writing
  beyond making them None-safe.
- Fixing the physical OLED wiring is an operator/hardware task (diagnosed with
  `i2cdetect`), separate from the code resilience here.

## Design

### A. Agent: always converse (`brain/milo_brain/llm/agent.py`)

`CognitionAgent` drops `_awaiting_name`/`_pending_embedding` and gains
`self._session_person = None`. `on_utterance` becomes:

```python
async def on_utterance(self, transcript, person, face_embedding_b64):
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
    messages = [{"role": "user", "content": f"[memory context]\n{context}"}, *self._history]
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
            tool_result = (await self._mcp.call_tool(name, **arguments)
                           if self._mcp is not None else {"ok": False, "error": "mcp unavailable"})
            messages.append({"role": "tool", "name": name, "content": json.dumps(tool_result)})

    self._history.append({"role": "assistant", "content": result.reply})
    new_name = await self._maybe_learn_name(transcript, speaker, face_embedding_b64)
    await self._write_facts(self._session_person, result.facts)
    return replace(result, new_person_name=new_name) if new_name else result
```

New/adjusted helpers:

```python
async def _maybe_learn_name(self, transcript, speaker, face_embedding_b64):
    # Capture a name only when the speaker explicitly introduces themselves
    # AND we don't already have them identified -- so ordinary chatter never
    # creates junk person nodes.
    if speaker is not None:
        return None
    name = extract_name(transcript)
    if not name:
        return None
    request = {"type": "person", "props": {"name": name}}
    if face_embedding_b64:
        request["embedding"] = face_embedding_b64
    created = await self._graph.call("upsert_node", **request)
    node = created.get("node")
    if not node:
        return None
    self._session_person = node
    await self._graph.call("upsert_node", type="event", props={"text": f"met {name}"})
    log.info("learned speaker name: %s (node %s)", name, node.get("id"))
    return name

async def _build_context(self, person):
    if person is None:
        return ("You are talking to someone you have not identified yet. Chat "
                "naturally; you may ask their name once if it feels right, but "
                "don't insist.")
    # (existing known-person context: Speaker line, neighbors, recent events)
    ...

async def _write_facts(self, person, facts):
    node_id = person.get("id") if person else None
    for fact in facts:
        created = await self._graph.call("upsert_node", type="fact", props={"text": fact})
        fact_id = created.get("node", {}).get("id")
        if node_id is not None and fact_id is not None:
            await self._graph.call("upsert_edge", src=node_id, dst=fact_id, type="said")
```

`extract_name` becomes strict — only explicit self-introductions, no bare-word
fallback:

```python
def extract_name(transcript):
    text = transcript.strip().rstrip(".!?")
    if not text:
        return None
    match = re.search(
        r"(?:my name is|i am|i'm|call me|this is)\s+([A-Za-z][\w'-]*(?:\s+[A-Z][\w'-]*)?)",
        text, flags=re.I,
    )
    return match.group(1).strip().title() if match else None
```

(`from dataclasses import replace` is added.)

### B. Face-display resilience (`bridge/milo_bridge/drivers/display.py`)

`FaceDisplay` gains `self._device_failed = False`; `_show` swallows a device
error, logs it once, and thereafter no-ops so no face op can crash a task:

```python
async def _show(self, image):
    if self._device_failed:
        return
    try:
        await asyncio.to_thread(self._device.display, image)
    except Exception as exc:
        self._device_failed = True
        log.warning("face display unavailable (%s); continuing without a face", exc)
```

The idle loop, animations, and `set_face` then all run harmlessly (their draws
become no-ops); the robot operates faceless instead of spewing tracebacks.

## Error handling

- Agent: every path now ends in an LLM reply or the empty-transcript short
  return; `_build_context`/`_write_facts` are None-safe, so an unidentified
  speaker never raises.
- Display: the single try/except in `_show` is the one choke point; after the
  first failure every draw is a silent no-op.

## Testing

- `test_agent.py`: `extract_name("Daham")` is now `None` (bare word); an unknown
  speaker still gets an LLM reply on the first utterance (LLM WAS called);
  saying "My name is Sarah" while unidentified creates a person node and returns
  `new_person_name`; ordinary unknown-speaker chatter creates **no** person
  node. Existing known-person / tool-loop / no-mcp / caching / empty-transcript
  tests keep passing (the `person=DAHAM` path is unchanged).
- `test_cognition_session.py`: the end-to-end hearing→speaking test still passes
  (its fake `match_face` supplies a person → LLM path).
- Display test: a device whose `display()` raises does not propagate out of
  `set_face`/idle; after the first failure `_device_failed` is set and
  `display()` is not called again (logged once).
