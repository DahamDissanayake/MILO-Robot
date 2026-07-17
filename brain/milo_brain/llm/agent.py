"""The cognition loop (spec §6): utterance + identity + memory -> reply.

For every utterance the agent builds context from Milo's graph (who is
speaking, what Milo knows about them, recent events), asks the LLM for a
structured response, and returns reply text + face + movement intent + facts
to write back. Also owns the unknown-person naming flow.

The LLM client and graph client are injected; tests use fakes.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

VALID_FACES = {
    "happy", "sad", "angry", "surprised", "sleepy", "love", "excited",
    "confused", "thinking", "idle",
}

MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = """You are Milo, a small four-legged robot with a camera, microphones and an OLED face.
You are curious, warm and a little playful. Keep replies to 1-3 short spoken sentences.

You have tools to move (walk, run_pose, turn, set_mode, reset, standby, relax,
hold, stop), check your own state (get_imu_state, get_status), change your
face (set_face -- one of: happy sad angry surprised sleepy love excited
confused thinking idle), and speak something unprompted (speak). Use them
when it fits the moment; check get_imu_state before an ambitious movement if
you're unsure about balance.

You know things from your on-board memory graph; context about the speaker
follows. Once you're done (with or without using any tools), reply ONLY with
JSON matching this schema:
{
  "reply": "what you say out loud",
  "facts": ["short new facts about the speaker worth remembering, empty if none"]
}"""


@dataclass(frozen=True)
class AgentResult:
    reply: str
    facts: list[str] = field(default_factory=list)
    new_person_name: str | None = None


class OllamaClient:
    """Minimal Ollama /api/chat wrapper with JSON-format output."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "llama3.2:3b"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Returns the raw assistant message dict (``content`` and, if the
        model requested one or more tool calls, ``tool_calls``). Ollama's
        strict JSON-format mode and its tool-calling mode aren't used
        together, so ``format: "json"`` is only requested when no tools are
        offered -- the final tool-calling turn's JSON-ness instead relies on
        SYSTEM_PROMPT's instructions plus parse_llm_json's existing
        tolerance for stray/non-strict text."""
        import httpx

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        else:
            payload["format"] = "json"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            return response.json()["message"]


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
    return {"reply": text[:200] or "Hmm.", "face": "confused", "move": "none", "facts": []}


def sanitize(data: dict) -> AgentResult:
    facts = [str(f)[:300] for f in data.get("facts", []) if str(f).strip()][:5]
    return AgentResult(reply=str(data.get("reply", ""))[:600], facts=facts)


def extract_name(transcript: str) -> str | None:
    """Pull a name out of an answer to 'what's your name?'."""
    text = transcript.strip().rstrip(".!?")
    if not text:
        return None
    patterns = [
        r"(?:my name is|i am|i'm|it's|its|call me|this is)\s+([A-Za-z][\w'-]*(?:\s+[A-Z][\w'-]*)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).strip().title()
    words = text.split()
    if len(words) <= 2 and all(w[:1].isalpha() for w in words):
        return text.title()
    return None


class CognitionAgent:
    def __init__(self, llm, graph, mcp=None):
        """``llm``: object with async chat(system, messages, tools=None) -> dict.
        ``graph``: object with async call(op, **kwargs) -> dict (the wire API).
        ``mcp``: object with async list_tools() -> list[dict] and async
        call_tool(tool_name, **kwargs) -> dict (MiloMcpClient, or None if
        this robot has no reachable MCP server)."""
        self._llm = llm
        self._graph = graph
        self._mcp = mcp
        self._tools: list[dict] | None = None
        self._tools_loaded = False
        self._awaiting_name = False
        self._pending_embedding: str | None = None
        self._history: list[dict] = []

    async def _get_tools(self) -> list[dict] | None:
        """Fetches the bridge's MCP tool schemas once (at first use, not
        per utterance) and caches them for the rest of this session."""
        if not self._tools_loaded:
            self._tools = await self._mcp.list_tools() if self._mcp is not None else None
            self._tools_loaded = True
        return self._tools

    async def on_utterance(
        self,
        transcript: str,
        person: dict | None,
        face_embedding_b64: str | None,
    ) -> AgentResult:
        if not transcript.strip():
            return AgentResult(reply="")

        if self._awaiting_name:
            name = extract_name(transcript)
            if name:
                pending = self._pending_embedding
                self._awaiting_name = False
                self._pending_embedding = None
                request = {"type": "person", "props": {"name": name}}
                if pending:
                    request["embedding"] = pending
                created = await self._graph.call("upsert_node", **request)
                node_id = created.get("node", {}).get("id")
                await self._graph.call(
                    "upsert_node", type="event",
                    props={"text": f"met {name} for the first time"},
                )
                log.info("new person: %s (node %s)", name, node_id)
                if self._mcp is not None:
                    await self._mcp.call_tool("set_face", name="excited")
                    await self._mcp.call_tool("run_pose", name="wave")
                return AgentResult(
                    reply=f"Nice to meet you, {name}! I'll remember you.",
                    new_person_name=name,
                )
            if self._mcp is not None:
                await self._mcp.call_tool("set_face", name="confused")
            return AgentResult(reply="Sorry, I didn't catch your name — what should I call you?")

        if person is None:
            self._awaiting_name = True
            self._pending_embedding = face_embedding_b64
            if self._mcp is not None:
                await self._mcp.call_tool("set_face", name="surprised")
            return AgentResult(reply="Hi! I don't think we've met — what's your name?")

        context = await self._build_context(person)
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
        await self._write_facts(person, result.facts)
        return result

    async def _build_context(self, person: dict) -> str:
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

    async def _write_facts(self, person: dict, facts: list[str]) -> None:
        node_id = person.get("id")
        for fact in facts:
            created = await self._graph.call("upsert_node", type="fact", props={"text": fact})
            fact_id = created.get("node", {}).get("id")
            if node_id is not None and fact_id is not None:
                await self._graph.call(
                    "upsert_edge", src=node_id, dst=fact_id, type="said"
                )
