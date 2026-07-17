"""Anthropic Messages API backend."""

from __future__ import annotations

import anthropic

from .base import AssistantTurn, Provider, ToolCall

MAX_TOKENS = 2048


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str) -> None:
        self.model = model
        # A missing key raises a clear error at construction rather than mid-search.
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=_to_anthropic_messages(messages),
            tools=[_to_anthropic_tool(t) for t in tools],
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))

        return AssistantTurn(text="".join(text_parts).strip(), tool_calls=tool_calls)


def _to_anthropic_tool(tool: dict) -> dict:
    return {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": tool["parameters"],
    }


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert neutral history to Anthropic's format.

    Consecutive `tool` results are merged into a single user message carrying
    multiple tool_result blocks, which is what the API expects after an
    assistant turn that made several tool calls.
    """
    out: list[dict] = []
    pending_tool_results: list[dict] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for msg in messages:
        role = msg["role"]
        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": msg["tool_call_id"],
                    "content": msg["content"],
                }
            )
            continue

        flush_tool_results()

        if role == "user":
            out.append({"role": "user", "content": msg["content"]})
        elif role == "assistant":
            blocks: list[dict] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg.get("tool_calls", []):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            out.append({"role": "assistant", "content": blocks or msg.get("content", "")})

    flush_tool_results()
    return out
