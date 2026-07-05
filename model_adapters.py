from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    id: str
    name: str
    input: Dict[str, Any]


class ModelAdapter:
    provider: str

    async def complete(
        self,
        *,
        model: str,
        system: Any,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int,
    ) -> Any:
        raise NotImplementedError

    def extract_text(self, response: Any) -> str:
        raise NotImplementedError

    def extract_tool_calls(self, response: Any) -> List[ToolCall]:
        raise NotImplementedError

    def assistant_message(self, response: Any) -> Dict[str, Any]:
        raise NotImplementedError

    def tool_result_message(self, results: List[Dict[str, str]]) -> Dict[str, Any]:
        raise NotImplementedError


class AnthropicAdapter(ModelAdapter):
    provider = "anthropic"

    def __init__(self, api_key: str, timeout: float = 90.0) -> None:
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic(api_key=api_key, timeout=timeout)

    async def complete(
        self,
        *,
        model: str,
        system: Any,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int,
    ) -> Any:
        return await self.client.messages.create(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )

    def extract_text(self, response: Any) -> str:
        parts: List[str] = []
        for block in getattr(response, "content", []) or []:
            if _block_value(block, "type") == "text":
                text = _block_value(block, "text", "")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()

    def extract_tool_calls(self, response: Any) -> List[ToolCall]:
        calls: List[ToolCall] = []
        for block in getattr(response, "content", []) or []:
            if _block_value(block, "type") == "tool_use":
                calls.append(ToolCall(
                    id=str(_block_value(block, "id", "")),
                    name=str(_block_value(block, "name", "")),
                    input=_block_value(block, "input", {}) or {},
                ))
        return calls

    def assistant_message(self, response: Any) -> Dict[str, Any]:
        return {
            "role": "assistant",
            "content": [_anthropic_block_to_dict(b) for b in getattr(response, "content", []) or []],
        }

    def tool_result_message(self, results: List[Dict[str, str]]) -> Dict[str, Any]:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": result["id"],
                    "content": result["content"][:20000],
                }
                for result in results
            ],
        }


class OpenAICompatibleAdapter(ModelAdapter):
    provider = "openai-compatible"

    def __init__(self, api_key: str, base_url: str = "", timeout: float = 90.0) -> None:
        from openai import AsyncOpenAI

        kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)

    async def complete(
        self,
        *,
        model: str,
        system: Any,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int,
    ) -> Any:
        request_messages = [{"role": "system", "content": _system_to_text(system)}] + messages
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": request_messages,
            "tools": _openai_tools(tools) if tools else None,
        }
        if model.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
        return await self.client.chat.completions.create(**kwargs)

    def extract_text(self, response: Any) -> str:
        message = response.choices[0].message if getattr(response, "choices", None) else None
        return (getattr(message, "content", "") or "").strip() if message else ""

    def extract_tool_calls(self, response: Any) -> List[ToolCall]:
        message = response.choices[0].message if getattr(response, "choices", None) else None
        calls: List[ToolCall] = []
        for call in getattr(message, "tool_calls", []) or []:
            function = getattr(call, "function", None)
            raw_args = getattr(function, "arguments", "{}") or "{}"
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = {}
            calls.append(ToolCall(
                id=str(getattr(call, "id", "")),
                name=str(getattr(function, "name", "")),
                input=parsed_args,
            ))
        return calls

    def assistant_message(self, response: Any) -> Dict[str, Any]:
        message = response.choices[0].message
        tool_calls = []
        for call in getattr(message, "tool_calls", []) or []:
            function = getattr(call, "function", None)
            tool_calls.append({
                "id": getattr(call, "id", ""),
                "type": "function",
                "function": {
                    "name": getattr(function, "name", ""),
                    "arguments": getattr(function, "arguments", "{}") or "{}",
                },
            })
        return {
            "role": "assistant",
            "content": getattr(message, "content", None),
            "tool_calls": tool_calls,
        }

    def tool_result_message(self, results: List[Dict[str, str]]) -> Dict[str, Any]:
        # The OpenAI chat API expects one tool message per tool call. The
        # cognition loop appends each returned message separately for this adapter.
        return {"role": "tool", "tool_call_id": results[0]["id"], "content": results[0]["content"][:20000]}


def create_model_adapter(provider: str, api_key: str, base_url: str = "") -> ModelAdapter:
    provider_key = (provider or "anthropic").strip().lower()
    if provider_key in {"anthropic", "claude"}:
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for MODEL_PROVIDER=anthropic.")
        return AnthropicAdapter(api_key=api_key)
    if provider_key in {"openai", "openai-compatible", "compatible", "xai", "groq", "deepseek", "mistral", "zai"}:
        if not api_key:
            raise RuntimeError(f"API key is required for MODEL_PROVIDER={provider}.")
        return OpenAICompatibleAdapter(api_key=api_key, base_url=base_url)
    raise RuntimeError(f"Unsupported MODEL_PROVIDER={provider!r}.")


def provider_api_key(provider: str, env: Dict[str, str]) -> str:
    provider_key = (provider or "").strip().lower()
    key_names = {
        "anthropic": "ANTHROPIC_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openai-compatible": "OPENAI_API_KEY",
        "compatible": "OPENAI_API_KEY",
        "xai": "XAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "zai": "ZAI_API_KEY",
    }
    return env.get(key_names.get(provider_key, ""), "").strip()


def _block_value(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _anthropic_block_to_dict(block: Any) -> Dict[str, Any]:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    block_type = _block_value(block, "type")
    if block_type == "text":
        return {"type": "text", "text": _block_value(block, "text", "")}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": _block_value(block, "id", ""),
            "name": _block_value(block, "name", ""),
            "input": _block_value(block, "input", {}) or {},
        }
    return {"type": str(block_type or "text"), "text": str(block)}


def _system_to_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts: List[str] = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "\n\n".join(part for part in parts if part)
    return str(system)


def _openai_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted = []
    for tool in tools:
        converted.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return converted
