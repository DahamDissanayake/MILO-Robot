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

SYSTEM_PROMPT = """You are Milo, a small four-legged robot with a camera, microphones and an OLED face.
You are curious, warm and a little playful. Keep replies to 1-3 short spoken sentences.

You know things from your on-board memory graph; context about the speaker follows.
Respond ONLY with JSON matching this schema:
{
  "reply": "what you say out loud",
  "face": "one of: happy sad angry surprised sleepy love excited confused thinking idle",
  "move": "one of: none wave dance bow point pushup",
  "facts": ["short new facts about the speaker worth remembering, empty if none"]
}"""


@dataclass(frozen=True)
class AgentResult:
    reply: str
    face: str = "happy"
    move: str = "none"
    facts: list[str] = field(default_factory=list)
    new_person_name: str | None = None


class OllamaClient:
    """Minimal Ollama /api/chat wrapper with JSON-format output."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "llama3.2:3b"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def chat(self, system: str, messages: list[dict]) -> str:
        import httpx

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "format": "json",
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            return response.json()["message"]["content"]


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
    face = str(data.get("face", "happy")).lower()
    move = str(data.get("move", "none")).lower()
    facts = [str(f)[:300] for f in data.get("facts", []) if str(f).strip()][:5]
    return AgentResult(
        reply=str(data.get("reply", ""))[:600],
        face=face if face in VALID_FACES else "happy",
        move=move if move in {"none", "wave", "dance", "bow", "point", "pushup"} else "none",
        facts=facts,
    )


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
    def __init__(self, llm, graph):
        """``llm``: object with async chat(system, messages) -> str.
        ``graph``: object with async call(op, **kwargs) -> dict (the wire API)."""
        self._llm = llm
        self._graph = graph
        self._awaiting_name = False
        self._pending_embedding: str | None = None  # b64, may be None even while waiting
        self._history: list[dict] = []

    async def on_utterance(
        self,
        transcript: str,
        person: dict | None,           # matched person node (or None = unknown)
        face_embedding_b64: str | None,
    ) -> AgentResult:
        if not transcript.strip():
            return AgentResult(reply="", face="idle")

        # --- unknown-person naming flow (spec F.5) -------------------------
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
                return AgentResult(
                    reply=f"Nice to meet you, {name}! I'll remember you.",
                    face="excited", move="wave", new_person_name=name,
                )
            return AgentResult(
                reply="Sorry, I didn't catch your name — what should I call you?",
                face="confused",
            )

        if person is None:
            self._awaiting_name = True
            self._pending_embedding = face_embedding_b64
            return AgentResult(
                reply="Hi! I don't think we've met — what's your name?",
                face="surprised",
            )

        # --- normal conversation -------------------------------------------
        context = await self._build_context(person)
        self._history.append({"role": "user", "content": transcript})
        self._history = self._history[-12:]
        messages = [
            {"role": "user", "content": f"[memory context]\n{context}"},
            *self._history,
        ]
        raw = await self._llm.chat(SYSTEM_PROMPT, messages)
        result = sanitize(parse_llm_json(raw))
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
