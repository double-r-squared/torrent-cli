"""Ollama backend (local models via the Ollama chat API)."""

from __future__ import annotations

import ollama

from .base import AssistantTurn, Provider, ToolCall


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, model: str, host: str) -> None:
        self.model = model
        self._client = ollama.Client(host=host)

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        response = self._client.chat(
            model=self.model,
            messages=_to_ollama_messages(system, messages),
            tools=[_to_ollama_tool(t) for t in tools],
        )

        message = response.message
        text = (message.content or "").strip()

        tool_calls: list[ToolCall] = []
        for i, call in enumerate(message.tool_calls or []):
            fn = call.function
            arguments = dict(fn.arguments) if fn.arguments else {}
            tool_calls.append(ToolCall(id=f"call_{i}", name=fn.name, arguments=arguments))

        return AssistantTurn(text=text, tool_calls=tool_calls)


def _to_ollama_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
        },
    }


def _to_ollama_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for msg in messages:
        role = msg["role"]
        if role == "user":
            out.append({"role": "user", "content": msg["content"]})
        elif role == "tool":
            out.append({"role": "tool", "name": msg["name"], "content": msg["content"]})
        elif role == "assistant":
            entry: dict = {"role": "assistant", "content": msg.get("content", "")}
            calls = msg.get("tool_calls", [])
            if calls:
                entry["tool_calls"] = [
                    {"function": {"name": c.name, "arguments": c.arguments}} for c in calls
                ]
            out.append(entry)
    return out
