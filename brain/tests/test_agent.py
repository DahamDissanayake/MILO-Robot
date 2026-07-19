import asyncio
import json
import json as json_lib

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
from milo_brain.llm.token_rate import TokenRateTracker


def test_system_prompt_face_list_is_derived_from_valid_faces():
    """SYSTEM_PROMPT's face list must be built from VALID_FACES, not hand
    duplicated -- otherwise the two can silently drift apart."""
    for face in VALID_FACES:
        assert face in SYSTEM_PROMPT


class _FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeStreamResponse(self._lines)

    async def __aexit__(self, *exc):
        return False


def test_chat_without_tools_requests_json_format(monkeypatch):
    captured = {}

    def fake_stream(self, method, url, json):
        captured.update(json)
        return _FakeStreamCtx([
            json_lib.dumps({"message": {"role": "assistant", "content": "hi"}, "done": False}),
            json_lib.dumps({
                "message": {"role": "assistant", "content": ""}, "done": True,
                "prompt_eval_count": 5, "prompt_eval_duration": 100_000_000,
            }),
        ])

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    client = OllamaClient()
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "hey"}])
    assert captured["format"] == "json"
    assert "tools" not in captured
    assert message == {"role": "assistant", "content": "hi"}


def test_chat_with_tools_omits_json_format_and_forwards_tools(monkeypatch):
    captured = {}

    def fake_stream(self, method, url, json):
        captured.update(json)
        return _FakeStreamCtx([
            json_lib.dumps({
                "message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "walk", "arguments": {"vx": 0.1}}}
                ]},
                "done": False,
            }),
            json_lib.dumps({
                "message": {"role": "assistant", "content": ""}, "done": True,
                "prompt_eval_count": 8, "prompt_eval_duration": 200_000_000,
            }),
        ])

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    client = OllamaClient()
    tools = [{"type": "function", "function": {"name": "walk", "description": "", "parameters": {}}}]
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "walk forward"}], tools=tools)
    assert "format" not in captured
    assert captured["tools"] == tools
    assert message["tool_calls"][0]["function"]["name"] == "walk"


def test_chat_feeds_token_rate_tracker_from_streamed_chunks(monkeypatch):
    def fake_stream(self, method, url, json):
        return _FakeStreamCtx([
            json_lib.dumps({"message": {"role": "assistant", "content": "Hel"}, "done": False}),
            json_lib.dumps({"message": {"role": "assistant", "content": "lo"}, "done": False}),
            json_lib.dumps({
                "message": {"role": "assistant", "content": ""}, "done": True,
                "prompt_eval_count": 100, "prompt_eval_duration": 200_000_000,  # -> 500 tok/s
            }),
        ])

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    tracker = TokenRateTracker()
    client = OllamaClient(rate_tracker=tracker)
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "hey"}])
    assert message["content"] == "Hello"
    assert tracker.tokens_per_sec_out > 0
    assert tracker.tokens_per_sec_in == 500.0


def test_chat_without_a_rate_tracker_still_works(monkeypatch):
    def fake_stream(self, method, url, json):
        return _FakeStreamCtx([
            json_lib.dumps({
                "message": {"role": "assistant", "content": "ok"}, "done": True,
                "prompt_eval_count": 1, "prompt_eval_duration": 1_000_000,
            }),
        ])

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    client = OllamaClient()  # no rate_tracker
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "hey"}])
    assert message == {"role": "assistant", "content": "ok"}


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
    assert extract_name("This is Alice") == "Alice"
    assert extract_name("Daham") is None  # a bare word is no longer treated as a name
    assert extract_name("well that is a very long sentence about nothing") is None
    # "i am/i'm/this is" + a lowercase word is ordinary speech, not a name --
    # these must NOT be captured (they used to create junk person nodes).
    assert extract_name("I am tired") is None
    assert extract_name("I'm good thanks") is None
    assert extract_name("this is fun") is None
    # ...but "my name is"/"call me" are unambiguous even lowercased.
    assert extract_name("my name is daham") == "Daham"


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
    agent = CognitionAgent(llm, FakeGraph(), mcp, use_tools=True)

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
    agent = CognitionAgent(llm, FakeGraph(), mcp, use_tools=True)

    result = asyncio.run(agent.on_utterance("wave forever", DAHAM, None))
    assert result.reply  # some graceful fallback reply, not a crash
    assert len(llm.calls) == 4  # MAX_TOOL_ROUNDS


def test_on_utterance_works_without_an_mcp_client():
    llm = FakeLlm([{"role": "assistant", "content": '{"reply": "hi", "facts": []}'}])
    agent = CognitionAgent(llm, FakeGraph(), mcp=None, use_tools=True)
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
    agent = CognitionAgent(llm, FakeGraph(), mcp, use_tools=True)
    asyncio.run(agent.on_utterance("hello", DAHAM, None))
    asyncio.run(agent.on_utterance("hello again", DAHAM, None))
    assert mcp.list_tools_calls == 1  # fetched once at first use, not per utterance


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


def test_warm_up_makes_a_throwaway_chat_to_preload_the_model():
    llm = FakeLlm([{"role": "assistant", "content": "ok"}])
    agent = CognitionAgent(llm, FakeGraph(), FakeMcp())
    asyncio.run(agent.warm_up())
    assert len(llm.calls) == 1  # one throwaway chat issued to cold-load Ollama


def test_empty_transcript_is_ignored():
    result = asyncio.run(CognitionAgent(FakeLlm(), FakeGraph(), FakeMcp()).on_utterance("  ", DAHAM, None))
    assert result.reply == ""
