import asyncio

import httpx

from milo_brain.llm.agent import (
    CognitionAgent,
    OllamaClient,
    extract_name,
    parse_llm_json,
    sanitize,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_chat_without_tools_requests_json_format(monkeypatch):
    captured = {}

    async def fake_post(self, url, json):
        captured.update(json)
        return _FakeResponse({"message": {"role": "assistant", "content": "hi"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = OllamaClient()
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "hey"}])
    assert captured["format"] == "json"
    assert "tools" not in captured
    assert message == {"role": "assistant", "content": "hi"}


def test_chat_with_tools_omits_json_format_and_forwards_tools(monkeypatch):
    captured = {}

    async def fake_post(self, url, json):
        captured.update(json)
        return _FakeResponse({"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "walk", "arguments": {"vx": 0.1}}}
        ]}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = OllamaClient()
    tools = [{"type": "function", "function": {"name": "walk", "description": "", "parameters": {}}}]
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "walk forward"}], tools=tools)
    assert "format" not in captured
    assert captured["tools"] == tools
    assert message["tool_calls"][0]["function"]["name"] == "walk"


def asyncio_run_chat(client, system, messages, tools=None):
    return asyncio.run(client.chat(system, messages, tools=tools))


class FakeLlm:
    def __init__(self, responses=None):
        self.responses = responses or []
        self.calls: list[list[dict]] = []

    async def chat(self, system, messages):
        self.calls.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return '{"reply": "Hello!", "face": "happy", "move": "none", "facts": []}'


class FakeGraph:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._next_id = 100

    async def call(self, op, **kwargs):
        self.calls.append((op, kwargs))
        if op == "upsert_node":
            self._next_id += 1
            return {"node": {"id": self._next_id, "type": kwargs.get("type"),
                             "props": kwargs.get("props", {})}}
        if op == "neighbors":
            return {"neighbors": [
                {"edge": {"type": "said"}, "node": {"props": {"text": "likes robots"}}},
            ]}
        if op == "recent_events":
            return {"nodes": [{"props": {"text": "met Daham yesterday"}}]}
        return {}


DAHAM = {"id": 1, "type": "person", "props": {"name": "Daham"}}


# --- parsing helpers ---------------------------------------------------------

def test_parse_llm_json_plain_and_fenced():
    assert parse_llm_json('{"reply": "hi"}')["reply"] == "hi"
    fenced = '```json\n{"reply": "hi"}\n```'
    assert parse_llm_json(fenced)["reply"] == "hi"
    embedded = 'Sure! Here you go: {"reply": "hi", "face": "happy"} hope that helps'
    assert parse_llm_json(embedded)["face"] == "happy"


def test_parse_llm_json_garbage_degrades_gracefully():
    result = parse_llm_json("I am not JSON at all")
    assert result["face"] == "confused"
    assert result["reply"]


def test_sanitize_rejects_invalid_face_and_move():
    result = sanitize({"reply": "x", "face": "smug", "move": "backflip", "facts": [1, " ok "]})
    assert result.face == "happy" and result.move == "none"
    assert result.facts == ["1", " ok "] or result.facts == ["1", "ok"]


def test_extract_name_variants():
    assert extract_name("My name is Daham") == "Daham"
    assert extract_name("I'm Sarah!") == "Sarah"
    assert extract_name("call me Bob") == "Bob"
    assert extract_name("Daham") == "Daham"
    assert extract_name("well that is a very long sentence about nothing") is None


# --- agent flows -------------------------------------------------------------

def test_known_person_gets_contextual_reply_and_facts_written():
    llm = FakeLlm(['{"reply": "Hi Daham!", "face": "happy", "move": "wave",'
                   ' "facts": ["Daham has an exam tomorrow"]}'])
    graph = FakeGraph()
    agent = CognitionAgent(llm, graph)

    result = asyncio.run(agent.on_utterance("I have an exam tomorrow", DAHAM, None))
    assert result.reply == "Hi Daham!"
    assert result.move == "wave"

    # Context included the graph material.
    sent = str(llm.calls[0])
    assert "likes robots" in sent and "met Daham yesterday" in sent
    # The fact was stored and linked to the speaker.
    ops = [op for op, _ in graph.calls]
    assert "upsert_node" in ops and "upsert_edge" in ops
    edge = next(kw for op, kw in graph.calls if op == "upsert_edge")
    assert edge["src"] == 1 and edge["type"] == "said"


def test_unknown_person_triggers_naming_flow():
    llm, graph = FakeLlm(), FakeGraph()
    agent = CognitionAgent(llm, graph)

    first = asyncio.run(agent.on_utterance("hello there", None, "ZmFrZQ=="))
    assert "name" in first.reply.lower()
    assert llm.calls == []  # no LLM round-trip for the scripted ask

    second = asyncio.run(agent.on_utterance("My name is Sarah", None, "ZmFrZQ=="))
    assert second.new_person_name == "Sarah"
    assert "Sarah" in second.reply
    created = next(kw for op, kw in graph.calls if op == "upsert_node"
                   and kw.get("type") == "person")
    assert created["props"]["name"] == "Sarah"
    assert created["embedding"] == "ZmFrZQ=="


def test_naming_flow_reprompts_on_unclear_answer():
    agent = CognitionAgent(FakeLlm(), FakeGraph())
    asyncio.run(agent.on_utterance("hi", None, None))
    retry = asyncio.run(agent.on_utterance("ehh whatever who cares honestly like", None, None))
    assert "name" in retry.reply.lower()
    # Still waiting: a clear answer now succeeds.
    done = asyncio.run(agent.on_utterance("I'm Bob", None, None))
    assert done.new_person_name == "Bob"


def test_empty_transcript_is_ignored():
    result = asyncio.run(CognitionAgent(FakeLlm(), FakeGraph()).on_utterance("  ", DAHAM, None))
    assert result.reply == ""
