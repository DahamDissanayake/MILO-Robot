import asyncio
import json

import httpx

from milo_brain.llm.agent import (
    SYSTEM_PROMPT,
    VALID_FACES,
    CognitionAgent,
    OllamaClient,
    extract_name,
    parse_llm_json,
    sanitize,
)


def test_system_prompt_face_list_is_derived_from_valid_faces():
    """SYSTEM_PROMPT's face list must be built from VALID_FACES, not hand
    duplicated -- otherwise the two can silently drift apart."""
    for face in VALID_FACES:
        assert face in SYSTEM_PROMPT


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
    """Each entry in ``turns`` is one raw message dict to return, in order,
    across the tool-calling loop's rounds."""

    def __init__(self, turns=None):
        self.turns = turns if turns is not None else [
            {"role": "assistant", "content": '{"reply": "Hello!", "facts": []}'}
        ]
        self.calls: list[dict] = []

    async def chat(self, system, messages, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self.turns.pop(0)


class FakeMcp:
    def __init__(self, tools=None):
        self._tools = tools or [{"type": "function", "function": {"name": "run_pose", "description": "", "parameters": {}}}]
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        return self._tools

    async def call_tool(self, tool_name, **arguments):
        # Parameter named tool_name, not name -- set_face/run_pose/set_mode
        # all take a kwarg literally called `name`, which would collide.
        self.calls.append((tool_name, arguments))
        return {"ok": True}


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


def test_sanitize_drops_face_and_move_keeps_reply_and_facts():
    result = sanitize({"reply": "x", "facts": [1, " ok "], "face": "ignored", "move": "ignored"})
    assert result.reply == "x"
    assert result.facts == ["1", " ok "]
    assert not hasattr(result, "face")
    assert not hasattr(result, "move")


def test_extract_name_variants():
    assert extract_name("My name is Daham") == "Daham"
    assert extract_name("I'm Sarah!") == "Sarah"
    assert extract_name("call me Bob") == "Bob"
    assert extract_name("Daham") == "Daham"
    assert extract_name("well that is a very long sentence about nothing") is None


# --- agent flows -------------------------------------------------------------

def test_known_person_gets_contextual_reply_with_no_tool_calls():
    llm = FakeLlm([{"role": "assistant", "content": '{"reply": "Hi Daham!", "facts": ["Daham has an exam tomorrow"]}'}])
    graph = FakeGraph()
    mcp = FakeMcp()
    agent = CognitionAgent(llm, graph, mcp)

    result = asyncio.run(agent.on_utterance("I have an exam tomorrow", DAHAM, None))
    assert result.reply == "Hi Daham!"

    sent = str(llm.calls[0]["messages"])
    assert "likes robots" in sent and "met Daham yesterday" in sent
    ops = [op for op, _ in graph.calls]
    assert "upsert_node" in ops and "upsert_edge" in ops


def test_tool_calls_are_executed_and_looped_until_a_final_reply():
    llm = FakeLlm([
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "run_pose", "arguments": {"name": "wave"}}}
        ]},
        {"role": "assistant", "content": '{"reply": "Done waving!", "facts": []}'},
    ])
    mcp = FakeMcp()
    agent = CognitionAgent(llm, FakeGraph(), mcp)

    result = asyncio.run(agent.on_utterance("wave at me", DAHAM, None))
    assert result.reply == "Done waving!"
    assert mcp.calls == [("run_pose", {"name": "wave"})]
    assert len(llm.calls) == 2
    # Round 2's messages include the tool call and its result.
    round_two_messages = llm.calls[1]["messages"]
    assert any(m.get("role") == "tool" for m in round_two_messages)


def test_tool_loop_gives_up_gracefully_after_max_rounds():
    keep_calling = {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "run_pose", "arguments": {"name": "wave"}}}
    ]}
    llm = FakeLlm([keep_calling, keep_calling, keep_calling, keep_calling])
    mcp = FakeMcp()
    agent = CognitionAgent(llm, FakeGraph(), mcp)

    result = asyncio.run(agent.on_utterance("wave forever", DAHAM, None))
    assert result.reply  # some graceful fallback reply, not a crash
    assert len(llm.calls) == 4  # MAX_TOOL_ROUNDS


def test_on_utterance_works_without_an_mcp_client():
    llm = FakeLlm([{"role": "assistant", "content": '{"reply": "hi", "facts": []}'}])
    agent = CognitionAgent(llm, FakeGraph(), mcp=None)
    result = asyncio.run(agent.on_utterance("hello", DAHAM, None))
    assert result.reply == "hi"
    assert llm.calls[0]["tools"] is None


def test_tool_schemas_are_fetched_once_and_cached_across_utterances():
    class CountingMcp(FakeMcp):
        def __init__(self):
            super().__init__()
            self.list_tools_calls = 0

        async def list_tools(self):
            self.list_tools_calls += 1
            return await super().list_tools()

    llm = FakeLlm([
        {"role": "assistant", "content": '{"reply": "hi", "facts": []}'},
        {"role": "assistant", "content": '{"reply": "hi again", "facts": []}'},
    ])
    mcp = CountingMcp()
    agent = CognitionAgent(llm, FakeGraph(), mcp)
    asyncio.run(agent.on_utterance("hello", DAHAM, None))
    asyncio.run(agent.on_utterance("hello again", DAHAM, None))
    assert mcp.list_tools_calls == 1  # fetched once at first use, not per utterance


def test_unknown_person_flow_sets_face_directly_via_mcp():
    mcp = FakeMcp()
    agent = CognitionAgent(FakeLlm(), FakeGraph(), mcp)

    first = asyncio.run(agent.on_utterance("hello there", None, "ZmFrZQ=="))
    assert "name" in first.reply.lower()
    assert ("set_face", {"name": "surprised"}) in mcp.calls

    second = asyncio.run(agent.on_utterance("My name is Sarah", None, "ZmFrZQ=="))
    assert second.new_person_name == "Sarah"
    assert ("set_face", {"name": "excited"}) in mcp.calls
    assert ("run_pose", {"name": "wave"}) in mcp.calls


def test_naming_flow_reprompts_and_sets_confused_face():
    mcp = FakeMcp()
    agent = CognitionAgent(FakeLlm(), FakeGraph(), mcp)
    asyncio.run(agent.on_utterance("hi", None, None))
    retry = asyncio.run(agent.on_utterance("ehh whatever who cares honestly like", None, None))
    assert "name" in retry.reply.lower()
    assert ("set_face", {"name": "confused"}) in mcp.calls
    done = asyncio.run(agent.on_utterance("I'm Bob", None, None))
    assert done.new_person_name == "Bob"


def test_empty_transcript_is_ignored():
    result = asyncio.run(CognitionAgent(FakeLlm(), FakeGraph(), FakeMcp()).on_utterance("  ", DAHAM, None))
    assert result.reply == ""
