import asyncio
from types import SimpleNamespace

from milo_brain.mcp_client import MiloMcpClient, _to_ollama_tool, _tool_result_to_dict


def test_to_ollama_tool_maps_mcp_tool_shape():
    tool = SimpleNamespace(
        name="walk",
        description="Continuous velocity walk.",
        inputSchema={"type": "object", "properties": {"vx": {"type": "number"}}, "required": ["vx"]},
    )
    assert _to_ollama_tool(tool) == {
        "type": "function",
        "function": {
            "name": "walk",
            "description": "Continuous velocity walk.",
            "parameters": {"type": "object", "properties": {"vx": {"type": "number"}}, "required": ["vx"]},
        },
    }


def test_to_ollama_tool_defaults_missing_description_to_empty_string():
    tool = SimpleNamespace(name="stop", description=None, inputSchema={"type": "object", "properties": {}})
    result = _to_ollama_tool(tool)
    assert result["function"]["description"] == ""


def test_tool_result_prefers_structured_content():
    result = SimpleNamespace(structuredContent={"ok": True}, content=[])
    assert _tool_result_to_dict(result) == {"ok": True}


def test_tool_result_falls_back_to_json_text_block():
    block = SimpleNamespace(type="text", text='{"ok": true, "mode": "raw"}')
    result = SimpleNamespace(structuredContent=None, content=[block])
    assert _tool_result_to_dict(result) == {"ok": True, "mode": "raw"}


def test_tool_result_falls_back_to_plain_text_when_not_json():
    block = SimpleNamespace(type="text", text="not json")
    result = SimpleNamespace(structuredContent=None, content=[block])
    assert _tool_result_to_dict(result) == {"text": "not json"}


def test_tool_result_empty_when_nothing_usable():
    result = SimpleNamespace(structuredContent=None, content=[])
    assert _tool_result_to_dict(result) == {}


def test_connected_is_false_before_connect():
    client = MiloMcpClient("http://x", token="t", peer_id="p")
    assert client.connected is False


def test_connected_is_true_once_a_session_exists():
    client = MiloMcpClient("http://x", token="t", peer_id="p")
    client._session = object()  # simulate what a completed connect() leaves behind
    assert client.connected is True


def test_connected_is_false_after_close():
    client = MiloMcpClient("http://x", token="t", peer_id="p")
    client._session = object()
    asyncio.run(client.close())
    assert client.connected is False
