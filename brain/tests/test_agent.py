import asyncio
import json
import json as json_lib

import httpx

from milo_brain.llm.agent import (
    SYSTEM_PROMPT,
    VALID_FACES,
    CognitionAgent,
    OllamaClient,
    describe_relation,
    extract_keywords,
    extract_name,
    parse_llm_json,
    repair_tool_args,
    sanitize,
    summarize_node,
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


class SearchableFakeGraph:
    """A FakeGraph variant with a configurable keyword -> nodes index and a
    configurable neighbor list, for testing _build_context's whole-graph
    keyword recall (search_text) both independently of and overlapping with
    the direct-neighbors path."""

    def __init__(self, search_index=None, neighbors=None):
        self.calls: list[tuple[str, dict]] = []
        self._next_id = 100
        self._search_index = search_index or {}
        self._neighbors = neighbors or []

    async def call(self, op, **kwargs):
        self.calls.append((op, kwargs))
        if op == "upsert_node":
            self._next_id += 1
            return {"node": {"id": self._next_id, "type": kwargs.get("type"),
                             "props": kwargs.get("props", {})}}
        if op == "neighbors":
            return {"neighbors": self._neighbors}
        if op == "recent_events":
            return {"nodes": []}
        if op == "search_text":
            return {"nodes": self._search_index.get(kwargs.get("q", ""), [])}
        return {"nodes": []}


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
    assert result["reply"]
    assert "face" not in result
    assert "move" not in result


def test_sanitize_drops_face_and_move_keeps_reply_and_facts():
    result = sanitize({"reply": "x", "facts": [1, " ok "], "face": "ignored", "move": "ignored"})
    assert result.reply == "x"
    assert result.facts == ["1", " ok "]
    assert not hasattr(result, "face")
    assert not hasattr(result, "move")


def test_sanitize_keeps_valid_entities_and_drops_invalid_relation():
    data = {
        "reply": "ok", "facts": [],
        "entities": [
            {"name": "Jane", "kind": "person", "relation": "supervisor_of", "with": "speaker"},
            {"name": "Rex", "kind": "animal", "relation": "made_up_relation", "with": "speaker"},
        ],
    }
    result = sanitize(data)
    assert len(result.entities) == 1
    assert result.entities[0] == {"name": "Jane", "kind": "person", "relation": "supervisor_of", "with": "speaker"}


def test_sanitize_drops_entity_with_unknown_kind():
    data = {"reply": "ok", "facts": [], "entities": [
        {"name": "Rex", "kind": "robot", "relation": "owns", "with": "speaker"},
    ]}
    assert sanitize(data).entities == []


def test_sanitize_caps_story_and_topic_length_and_treats_null_as_none():
    data = {"reply": "ok", "facts": [], "story": "x" * 600, "topic": None}
    result = sanitize(data)
    assert len(result.story) == 500
    assert result.topic is None


def test_sanitize_handles_missing_entities_story_topic_fields():
    result = sanitize({"reply": "ok", "facts": []})
    assert result.entities == [] and result.story is None and result.topic is None


def test_repair_tool_args_passes_a_clean_call_through():
    assert repair_tool_args({"name": "wave"}, {"name"}) == {"name": "wave"}


def test_repair_tool_args_unwraps_a_nested_object_wrapper():
    # llama3.2:3b's actual malformed shape: real arg nested under "object",
    # with other tools' params dumped alongside it.
    bad = {"object": {"name": "wave"}, "face": "happy", "vx": 0, "vy": 0, "yaw_rate": 0}
    assert repair_tool_args(bad, {"name"}) == {"name": "wave"}


def test_repair_tool_args_drops_params_the_tool_does_not_declare():
    # face/vx belong to other tools; run_pose only declares name (+cycles).
    bad = {"name": "dance", "face": "excited", "vx": 0.2}
    assert repair_tool_args(bad, {"name", "cycles"}) == {"name": "dance"}


def test_repair_tool_args_parses_a_json_string_payload():
    assert repair_tool_args('{"name": "bow"}', {"name"}) == {"name": "bow"}


def test_repair_tool_args_without_a_known_schema_unwraps_but_keeps_extra_keys():
    # Unknown tool (empty valid_params): still unwrap, but don't filter --
    # we can't know which keys are valid, and dropping them all would be worse.
    assert repair_tool_args({"object": {"vx": 0.2, "vy": 0.0}}, set()) == {"vx": 0.2, "vy": 0.0}


def test_repair_tool_args_handles_non_dict_garbage():
    assert repair_tool_args("not json", {"name"}) == {}
    assert repair_tool_args(["happy"], {"name"}) == {}


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


def test_extract_keywords_prefers_proper_nouns_and_drops_stopwords():
    kws = extract_keywords("Have you seen Jane lately about her new project")
    assert "Jane" in kws
    assert "have" not in [k.lower() for k in kws]
    assert kws[0] == "Jane"  # capitalized proper noun ranked first


def test_extract_keywords_caps_at_max_keywords():
    kws = extract_keywords("apple banana cherry dragon elephant flamingo giraffe", max_keywords=3)
    assert len(kws) == 3


def test_extract_keywords_deduplicates_case_insensitively():
    kws = extract_keywords("Japan japan JAPAN trip")
    assert len(kws) == 2  # "Japan" and "trip", not three separate "japan" entries


def test_describe_relation_is_direction_aware():
    assert describe_relation("supervisor_of", viewer_is_src=True) == "supervisor of"
    assert describe_relation("supervisor_of", viewer_is_src=False) == "reports to"
    assert describe_relation("owns", viewer_is_src=True) == "owns"
    assert describe_relation("owns", viewer_is_src=False) == "belongs to"
    assert describe_relation("friend_of", viewer_is_src=True) == "friend of"
    assert describe_relation("friend_of", viewer_is_src=False) == "friend of"


def test_describe_relation_falls_back_to_the_raw_type_for_structural_edges():
    assert describe_relation("said", viewer_is_src=True) == "said"
    assert describe_relation("told", viewer_is_src=False) == "told"


def test_summarize_node_prefers_text_then_name_then_none():
    assert summarize_node({"props": {"text": "likes robots", "name": "ignored"}}) == "likes robots"
    assert summarize_node({"props": {"name": "Jane"}}) == "Jane"
    assert summarize_node({"props": {}}) is None


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


def test_build_context_recalls_keyword_matches_beyond_direct_neighbors():
    story_node = {"id": 55, "type": "story", "props": {"text": "trip to Japan last year"}}
    graph = SearchableFakeGraph(search_index={"Japan": [story_node]})
    agent = CognitionAgent(FakeLlm(), graph, FakeMcp())

    context = asyncio.run(agent._build_context(DAHAM, "tell me about Japan"))
    assert "trip to Japan last year" in context
    search_calls = [kw for op, kw in graph.calls if op == "search_text"]
    assert any(kw.get("q") == "Japan" for kw in search_calls)


def test_build_context_does_not_duplicate_a_node_that_is_both_neighbor_and_keyword_match():
    fact_node = {"id": 42, "type": "fact", "props": {"text": "likes robots"}}
    graph = SearchableFakeGraph(
        neighbors=[{"edge": {"type": "said", "src": DAHAM["id"], "dst": 42}, "node": fact_node}],
        search_index={"robots": [fact_node]},
    )
    context = asyncio.run(CognitionAgent(FakeLlm(), graph, FakeMcp())._build_context(DAHAM, "tell me about robots"))
    assert context.count("likes robots") == 1  # once from neighbors, not again from the keyword match


def test_build_context_deduplicates_a_node_matched_by_multiple_keywords():
    fact_node = {"id": 7, "type": "fact", "props": {"text": "likes robots"}}
    graph = SearchableFakeGraph(search_index={"robots": [fact_node], "likes": [fact_node]})
    context = asyncio.run(CognitionAgent(FakeLlm(), graph, FakeMcp())._build_context(DAHAM, "robots and likes robots"))
    assert context.count("likes robots") == 1  # matched by two keywords, appears once


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


def test_agent_repairs_a_malformed_tool_call_before_dispatching_it():
    """End to end: the LLM emits llama3.2:3b's malformed run_pose call (name
    nested under "object", plus stray face/vx params). The agent must repair it
    to a clean run_pose(name="wave") using the tool's real schema, so the bridge
    gets a valid call instead of rejecting it."""
    run_pose_schema = {"type": "function", "function": {
        "name": "run_pose", "description": "",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "cycles": {"type": "integer"}}},
    }}
    llm = FakeLlm([
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "run_pose", "arguments": {
                "object": {"name": "wave"}, "face": "happy", "vx": 0, "vy": 0, "yaw_rate": 0}}}
        ]},
        {"role": "assistant", "content": '{"reply": "There you go!", "facts": []}'},
    ])
    mcp = FakeMcp(tools=[run_pose_schema])
    agent = CognitionAgent(llm, FakeGraph(), mcp, use_tools=True)

    result = asyncio.run(agent.on_utterance("wave and look happy", DAHAM, None))
    assert result.reply == "There you go!"
    assert mcp.calls == [("run_pose", {"name": "wave"})]  # repaired, not the raw garbage


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


def test_on_utterance_writes_entity_relation_story_and_topic():
    llm = FakeLlm([{"role": "assistant", "content": json.dumps({
        "reply": "Got it!",
        "facts": [],
        "entities": [{"name": "Jane", "kind": "person", "relation": "supervisor_of", "with": "speaker"}],
        "story": "told me about her trip to Japan last year",
        "topic": "the weather has been nice lately",
    })}])
    graph = FakeGraph()
    agent = CognitionAgent(llm, graph, FakeMcp())

    result = asyncio.run(agent.on_utterance(
        "Jane is my supervisor, she just got back from Japan", DAHAM, None))
    assert result.reply == "Got it!"

    person_creates = [kw for op, kw in graph.calls if op == "upsert_node" and kw.get("type") == "person"]
    assert person_creates and person_creates[0]["props"]["name"] == "Jane"

    edge_calls = [kw for op, kw in graph.calls if op == "upsert_edge"]
    assert len(edge_calls) == 2  # supervisor_of + told, topic gets no edge

    relation_edge = next(kw for kw in edge_calls if kw["type"] == "supervisor_of")
    assert relation_edge["dst"] == DAHAM["id"]  # Jane --supervisor_of--> Daham
    assert isinstance(relation_edge["src"], int)

    story_creates = [kw for op, kw in graph.calls if op == "upsert_node" and kw.get("type") == "story"]
    assert story_creates and "Japan" in story_creates[0]["props"]["text"]
    told_edge = next(kw for kw in edge_calls if kw["type"] == "told")
    assert told_edge["src"] == DAHAM["id"]

    topic_creates = [kw for op, kw in graph.calls if op == "upsert_node" and kw.get("type") == "topic"]
    assert topic_creates and "weather" in topic_creates[0]["props"]["text"]


class GraphWithExistingJane(FakeGraph):
    async def call(self, op, **kwargs):
        if op == "query" and kwargs.get("type") == "person":
            self.calls.append((op, kwargs))
            return {"nodes": [{"id": 42, "type": "person", "props": {"name": "Jane"}}]}
        return await super().call(op, **kwargs)


def test_entity_relation_reuses_existing_person_by_case_insensitive_name():
    llm = FakeLlm([{"role": "assistant", "content": json.dumps({
        "reply": "ok", "facts": [],
        "entities": [{"name": "jane", "kind": "person", "relation": "supervisor_of", "with": "speaker"}],
    })}])
    graph = GraphWithExistingJane()
    agent = CognitionAgent(llm, graph, FakeMcp())
    asyncio.run(agent.on_utterance("jane is my supervisor", DAHAM, None))

    person_creates = [kw for op, kw in graph.calls if op == "upsert_node" and kw.get("type") == "person"]
    assert person_creates == []  # reused id 42, not created again
    edge_calls = [kw for op, kw in graph.calls if op == "upsert_edge"]
    assert edge_calls[0]["src"] == 42
