"""The cognition loop (spec §6): utterance + identity + memory -> reply.

For every utterance the agent builds context from Milo's graph (who is
speaking, what Milo knows about them, recent events), then lets the LLM call
MCP tools (movement, face, speech) as it sees fit and finally reply with a
small JSON object carrying reply text + new facts to write back. Also owns
the unknown-person naming flow.

The LLM client and graph client are injected; tests use fakes.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace

from milo_common.graph_types import RELATION_TYPES

from .token_rate import TokenRateTracker

log = logging.getLogger(__name__)

VALID_FACES = {
    "happy", "sad", "angry", "surprised", "sleepy", "love", "excited",
    "confused", "thinking", "idle",
}

MAX_TOOL_ROUNDS = 4

_RELATIONS = ", ".join(sorted(RELATION_TYPES))

SYSTEM_PROMPT = f"""You are Milo, a small four-legged robot with a camera, microphones and an OLED face.
You are curious, warm and a little playful. Keep replies to 1-3 short spoken sentences.

## Acting on requests
When the user asks you to physically DO something, you MUST call a tool:
- A trick or body movement -> call run_pose with the pose `name`. Poses include
  wave, dance, bow, point, pushup, shake, shrug, crab, look_up, look_down, rest,
  stand, turn_left, turn_right (see the run_pose tool for the full list).
- To walk around -> call walk with vx/vy (m/s) and yaw_rate (deg/s); walk(0,0,0) stops.
- An emotion or expression on your face -> call set_face with `name`, one of:
  {", ".join(sorted(VALID_FACES))}.
You may also check your balance/state (get_imu_state, get_status) or say
something unprompted (speak). Check get_imu_state before an ambitious movement
if you're unsure about balance.

Tool-call rules (follow exactly):
- run_pose is for BODY actions; set_face is ONLY for the {len(VALID_FACES)} face
  expressions listed above. "bow", "wave" and "dance" are POSES (run_pose), never faces.
- Each tool takes ONLY its own arguments. Never wrap them in an "object" key, and
  never put a face name or vx/vy into run_pose.
- Worked examples:
  - "wave at me"        -> run_pose(name="wave")
  - "do a little dance" -> run_pose(name="dance")
  - "turn left"         -> run_pose(name="turn_left")
  - "you look sad"      -> set_face(name="happy")
  - "walk forward"      -> walk(vx=0.2, vy=0.0, yaw_rate=0.0)

You know things from your on-board memory graph; context about the speaker
follows. Once you're done (with or without using any tools), reply ONLY with
JSON matching this schema:
{{
  "reply": "what you say out loud",
  "facts": ["short new facts about the speaker worth remembering, empty if none"],
  "entities": [
    {{"name": "their name", "kind": "person or animal",
      "relation": "one of: {_RELATIONS}", "with": "speaker or another name mentioned this turn"}}
  ],
  "story": "a longer narrative the speaker just recounted, or null",
  "topic": "a general note if this exchange wasn't really about the speaker, or null"
}}
Only include "entities" when the speaker described a relationship (e.g.
"she is my supervisor", "this is my dog Rex") -- leave it empty otherwise.
Only set "story" when the speaker recounted something that happened to
them, not for ordinary chat. Only set "topic" for exchanges that aren't
really about the speaker personally."""


@dataclass(frozen=True)
class AgentResult:
    reply: str
    facts: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    story: str | None = None
    topic: str | None = None
    new_person_name: str | None = None


class OllamaClient:
    """Minimal Ollama /api/chat wrapper with JSON-format output."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "llama3.2:3b",
        rate_tracker: TokenRateTracker | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._rate_tracker = rate_tracker
        self.status = "unknown"  # "unknown" | "responding" | "ready" | "error"
        self.error: str | None = None

    async def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Returns the raw assistant message dict (``content`` and, if the
        model requested one or more tool calls, ``tool_calls``). Ollama's
        strict JSON-format mode and its tool-calling mode aren't used
        together, so ``format: "json"`` is only requested when no tools are
        offered -- the final tool-calling turn's JSON-ness instead relies on
        SYSTEM_PROMPT's instructions plus parse_llm_json's existing
        tolerance for stray/non-strict text.

        Streams the response (rather than one blocking POST) so a
        TokenRateTracker, if given, reports a live tokens/sec rate to the
        TUI while the model is still generating, not just a number after
        the fact."""
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


def parse_llm_json(raw: str) -> dict:
    """Parse the model's JSON, tolerating markdown fences and stray text."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {"reply": text[:200] or "Hmm.", "facts": []}


def sanitize(data: dict) -> AgentResult:
    facts = [str(f)[:300] for f in data.get("facts", []) if str(f).strip()][:5]

    entities = []
    raw_entities = data.get("entities")
    for e in raw_entities if isinstance(raw_entities, list) else []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name", "")).strip()[:100]
        kind = str(e.get("kind", "")).strip().lower()
        relation = str(e.get("relation", "")).strip().lower()
        with_name = str(e.get("with", "")).strip()[:100] or None
        if not name or kind not in {"person", "animal"} or relation not in RELATION_TYPES:
            continue
        entities.append({"name": name, "kind": kind, "relation": relation, "with": with_name})
    entities = entities[:5]

    def _clean_text(value, max_len=500):
        if not value:
            return None
        cleaned = str(value)[:max_len].strip()
        return cleaned or None

    story = _clean_text(data.get("story"))
    topic = _clean_text(data.get("topic"))

    return AgentResult(
        reply=str(data.get("reply", ""))[:600], facts=facts,
        entities=entities, story=story, topic=topic,
    )


# Keys small models nest the real arguments under instead of passing them flat
# (llama3.2:3b routinely emits run_pose({"object": {"name": "wave"}, ...})).
_ARG_WRAPPER_KEYS = ("object", "arguments", "args", "params", "parameters", "kwargs")


def repair_tool_args(arguments, valid_params: set[str]) -> dict:
    """Best-effort fix for a small model's malformed tool-call arguments so an
    otherwise-correct call still reaches the robot instead of being rejected by
    the bridge. Two observed failure modes from llama3.2:3b:

    1. The real args are nested under an ``"object"``/``"arguments"`` wrapper key
       -- unwrap it (merging the inner dict up, inner values winning).
    2. Params from *other* tools are dumped into one call (e.g. ``face`` and
       ``vx`` inside a run_pose call) -- drop anything the tool's schema
       doesn't declare.

    ``valid_params`` is the tool's declared parameter names (empty if unknown,
    in which case we unwrap but don't filter). Accepts a JSON string too, since
    Ollama occasionally hands the arguments back as a string."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
    if not isinstance(arguments, dict):
        return {}
    merged = dict(arguments)
    for wrapper in _ARG_WRAPPER_KEYS:
        inner = merged.get(wrapper)
        if isinstance(inner, dict) and wrapper not in valid_params:
            merged.pop(wrapper)
            for key, value in inner.items():
                merged.setdefault(key, value)
    if valid_params:
        merged = {k: v for k, v in merged.items() if k in valid_params}
    return merged


def extract_name(transcript: str) -> str | None:
    """Pull a name only from an explicit self-introduction. Ordinary short
    phrases are NOT treated as names (that created junk person nodes).

    "my name is X" / "call me X" are unambiguous, so the name may be any
    word. "i am X" / "i'm X" / "this is X" also match ordinary sentences
    ("I am tired", "this is fun"), so for those the captured name must be
    Capitalized -- Whisper capitalizes proper nouns, so a real name ("I'm
    Daham") still matches while a common adjective ("i am tired") doesn't.
    The intro phrase is matched case-insensitively; the name's casing is
    matched literally (note the scoped ``(?i:...)`` rather than a global
    re.I, which would defeat the capitalization check)."""
    text = transcript.strip().rstrip(".!?")
    if not text:
        return None
    any_name = r"([A-Za-z][\w'-]*(?:\s+[A-Z][\w'-]*)?)"
    cap_name = r"([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?)"
    strong = re.search(r"(?i:my name is|call me)\s+" + any_name, text)
    if strong:
        return strong.group(1).strip().title()
    weak = re.search(r"(?i:i am|i'?m|this is)\s+" + cap_name, text)
    if weak:
        return weak.group(1).strip().title()
    return None


STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "and", "or", "but", "to",
    "of", "in", "on", "at", "for", "with", "this", "that", "have", "has",
    "had", "you", "your", "i", "me", "my", "we", "our", "they", "them",
    "he", "she", "it", "its", "about", "just", "really", "very", "so",
    "not", "do", "does", "did", "will", "would", "can", "could", "should",
    "from", "as", "be", "been", "was", "were", "there", "here", "what",
    "when", "where", "who", "how", "why",
})


def extract_keywords(transcript: str, max_keywords: int = 5) -> list[str]:
    """Cheap keyword pull for graph-wide recall: capitalized proper nouns
    first, then longer words, stopwords and short words dropped. No LLM
    call -- this runs before the reply-generating call, so it can't lean
    on that turn's own extraction (see CognitionAgent._build_context)."""
    words = re.findall(r"[A-Za-z][\w'-]*", transcript)
    candidates = [w for w in words if w.lower() not in STOPWORDS and len(w) >= 4]
    candidates.sort(key=lambda w: (not w[0].isupper(), -len(w)))
    seen: set[str] = set()
    out: list[str] = []
    for w in candidates:
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
        if len(out) >= max_keywords:
            break
    return out


# Direction-aware phrasing for RELATION_TYPES edges: (phrase when the node
# being described is the edge's src, phrase when it's the dst). Structural
# edge types (said/told/mentions/met) aren't in this table and read fine
# using the raw edge type as-is.
RELATION_PHRASING: dict[str, tuple[str, str]] = {
    "supervisor_of": ("supervisor of", "reports to"),
    "reports_to": ("reports to", "supervisor of"),
    "parent_of": ("parent of", "child of"),
    "child_of": ("child of", "parent of"),
    "sibling_of": ("sibling of", "sibling of"),
    "spouse_of": ("spouse of", "spouse of"),
    "friend_of": ("friend of", "friend of"),
    "knows": ("knows", "knows"),
    "owns": ("owns", "belongs to"),
    "belongs_to": ("belongs to", "owns"),
}


def describe_relation(edge_type: str, viewer_is_src: bool) -> str:
    phrasing = RELATION_PHRASING.get(edge_type)
    if phrasing is None:
        return edge_type
    return phrasing[0] if viewer_is_src else phrasing[1]


def summarize_node(node: dict) -> str | None:
    props = node.get("props", {})
    return props.get("text") or props.get("name")


class CognitionAgent:
    def __init__(self, llm, graph, mcp=None, use_tools: bool = False):
        """``llm``: object with async chat(system, messages, tools=None) -> dict.
        ``graph``: object with async call(op, **kwargs) -> dict (the wire API).
        ``mcp``: object with async list_tools() -> list[dict] and async
        call_tool(tool_name, **kwargs) -> dict (MiloMcpClient, or None if
        this robot has no reachable MCP server).
        ``use_tools``: offer the LLM the robot's MCP tools for autonomous
        movement/face calls. OFF by default -- small models (e.g. llama3.2:3b)
        do tool-calling unreliably (empty replies + garbage tool args), which
        breaks the spoken reply; with tools off, Ollama's strict JSON mode
        gives clean conversational replies. The robot still reacts to sound
        direction and animates its face while speaking (both direct, not
        LLM-driven). Turn it on for a capable large-tier model."""
        self._llm = llm
        self._graph = graph
        self._mcp = mcp
        self._use_tools = use_tools
        self._tools: list[dict] | None = None
        self._tools_loaded = False
        self._session_person: dict | None = None
        self._history: list[dict] = []

    async def _get_tools(self) -> list[dict] | None:
        """Fetches the bridge's MCP tool schemas once (at first use, not
        per utterance) and caches them for the rest of this session."""
        if not self._tools_loaded:
            self._tools = await self._mcp.list_tools() if self._mcp is not None else None
            self._tools_loaded = True
        return self._tools

    def _valid_params_for(self, tool_name: str) -> set[str]:
        """The parameter names a given tool declares, from the cached schemas --
        used to strip a small model's stray/misplaced tool-call args."""
        for tool in self._tools or []:
            fn = tool.get("function", {})
            if fn.get("name") == tool_name:
                props = (fn.get("parameters") or {}).get("properties") or {}
                return set(props)
        return set()

    async def warm_up(self) -> None:
        """Cold-load the LLM with a tiny throwaway chat. Ollama pulls the model
        into memory on its FIRST request (~20-30s for a 3B on CPU), so without
        this the operator's first real reply eats that whole cold start; doing
        it in the background on connect makes the first real reply prompt."""
        await self._llm.chat(
            "Reply with 'ok'.", [{"role": "user", "content": "warm up"}]
        )

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

        context = await self._build_context(speaker, transcript)
        self._history.append({"role": "user", "content": transcript})
        self._history = self._history[-12:]
        messages = [
            {"role": "user", "content": f"[memory context]\n{context}"},
            *self._history,
        ]
        tools = await self._get_tools() if self._use_tools else None

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
                    arguments = repair_tool_args(
                        fn.get("arguments") or {}, self._valid_params_for(name)
                    )
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

        self._history.append({"role": "assistant", "content": result.reply})
        new_name = await self._maybe_learn_name(transcript, speaker, face_embedding_b64)
        await self._write_memory(self._session_person, result)
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

    async def _build_context(self, person: dict | None, transcript: str) -> str:
        lines: list[str] = []
        person_id = person.get("id") if person else None
        seen_ids: set = {person_id} if person_id is not None else set()
        if person is None:
            lines.append(
                "You are talking to someone you have not identified yet. Chat "
                "naturally; you may ask their name once if it feels right, but "
                "don't insist."
            )
        else:
            lines.append(f"Speaker: {person.get('props', {}).get('name', 'unknown')}")
            if person_id is not None:
                neighbors = await self._graph.call("neighbors", node_id=person_id, limit=10)
                for item in neighbors.get("neighbors", []):
                    node = item.get("node") or {}
                    edge = item.get("edge") or {}
                    summary = summarize_node(node)
                    if not summary:
                        continue
                    if node.get("id") is not None:
                        seen_ids.add(node.get("id"))
                    label = describe_relation(edge.get("type", "related"), edge.get("src") == person_id)
                    lines.append(f"- {label}: {summary}")

        for kw in extract_keywords(transcript):
            result = await self._graph.call("search_text", q=kw, limit=5)
            for node in result.get("nodes", []):
                if node.get("id") in seen_ids:
                    continue
                seen_ids.add(node.get("id"))
                summary = summarize_node(node)
                if summary:
                    lines.append(f"- recalled ({node.get('type')}): {summary}")

        recent = await self._graph.call("recent_events", limit=5)
        for node in recent.get("nodes", []):
            text = node.get("props", {}).get("text")
            if text:
                lines.append(f"- recent event: {text}")
        return "\n".join(lines)

    async def _find_or_create_entity(self, name: str, kind: str) -> dict | None:
        existing = await self._graph.call("query", type=kind, limit=200)
        for node in existing.get("nodes", []):
            if node.get("props", {}).get("name", "").lower() == name.lower():
                return node
        created = await self._graph.call("upsert_node", type=kind, props={"name": name})
        return created.get("node")

    async def _write_memory(self, person: dict | None, result: AgentResult) -> None:
        node_id = person.get("id") if person else None

        for fact in result.facts:
            created = await self._graph.call("upsert_node", type="fact", props={"text": fact})
            fact_id = created.get("node", {}).get("id")
            if node_id is not None and fact_id is not None:
                await self._graph.call("upsert_edge", src=node_id, dst=fact_id, type="said")

        for entity in result.entities:
            target = await self._find_or_create_entity(entity["name"], entity["kind"])
            with_name = entity.get("with")
            if with_name == "speaker":
                subject_id = node_id
            elif with_name:
                # Hardcoded "person": the entities schema gives no kind hint for the
                # `with` party, so a third-party animal (e.g. "Rex is Bella's puppy")
                # will mis-resolve/duplicate Bella as a person node. Fixing this needs
                # a kind hint added to SYSTEM_PROMPT's entities schema (Task 7) plus
                # matching sanitize() validation -- known limitation, not fixed here.
                subject = await self._find_or_create_entity(with_name, "person")
                subject_id = subject["id"] if subject else None
            else:
                subject_id = None
            if subject_id is not None and target is not None:
                await self._graph.call(
                    "upsert_edge", src=target["id"], dst=subject_id, type=entity["relation"]
                )

        if result.story:
            created = await self._graph.call("upsert_node", type="story", props={"text": result.story})
            story_id = created.get("node", {}).get("id")
            if node_id is not None and story_id is not None:
                await self._graph.call("upsert_edge", src=node_id, dst=story_id, type="told")

        if result.topic:
            await self._graph.call("upsert_node", type="topic", props={"text": result.topic})
