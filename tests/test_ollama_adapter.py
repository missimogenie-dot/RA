"""OllamaAdapter parsing tests — canned /api/chat responses, no network."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_adapters import OllamaAdapter, create_model_adapter, provider_api_key

CHAT_RESPONSE = {
    "model": "qwen3.6:27b",
    "message": {
        "role": "assistant",
        "content": "The garden is quiet today.",
        "thinking": "The human asked about the garden; I recall tomatoes.",
    },
    "done": True,
}

TOOL_RESPONSE = {
    "model": "qwen3.6:27b",
    "message": {
        "role": "assistant",
        "content": "",
        "thinking": "I should check memory first.",
        "tool_calls": [
            {"function": {"name": "recall_memory", "arguments": {"query": "tomatoes"}}},
            {"function": {"name": "library_list", "arguments": {}}},
        ],
    },
    "done": True,
}


def adapter() -> OllamaAdapter:
    return OllamaAdapter(base_url="http://localhost:11434")


def test_text_and_thinking_are_separate_channels():
    a = adapter()
    assert a.extract_text(CHAT_RESPONSE) == "The garden is quiet today."
    assert "tomatoes" in a.extract_thinking(CHAT_RESPONSE)
    # thinking never enters the history message
    assert "thinking" not in a.assistant_message(CHAT_RESPONSE)


def test_structured_tool_calls_with_synthesised_ids():
    a = adapter()
    calls = a.extract_tool_calls(TOOL_RESPONSE)
    assert [c.name for c in calls] == ["recall_memory", "library_list"]
    assert calls[0].input == {"query": "tomatoes"}
    assert calls[0].id == "recall_memory#0"
    # string arguments are tolerated too
    stringy = {"message": {"tool_calls": [
        {"function": {"name": "x", "arguments": '{"a": 1}'}},
    ]}}
    assert a.extract_tool_calls(stringy)[0].input == {"a": 1}


def test_tool_result_message_recovers_tool_name():
    a = adapter()
    msg = a.tool_result_message([{"id": "recall_memory#0", "content": "2 matches"}])
    assert msg == {"role": "tool", "tool_name": "recall_memory", "content": "2 matches"}


def test_assistant_message_keeps_tool_calls_for_history():
    a = adapter()
    msg = a.assistant_message(TOOL_RESPONSE)
    assert msg["role"] == "assistant"
    assert len(msg["tool_calls"]) == 2
    assert "thinking" not in msg


def test_factory_and_keyless_provider():
    a = create_model_adapter("ollama", api_key="", base_url="")
    assert isinstance(a, OllamaAdapter)
    assert provider_api_key("ollama", {"OPENAI_API_KEY": "sk-x"}) == ""
