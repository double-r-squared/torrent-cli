"""Provider-neutral message/tool types and the Provider interface.

The agent keeps history in this neutral shape; each provider converts it to its
own wire format on every call. Neutral message shapes:

    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": list[ToolCall]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class AssistantTurn:
    """What the model produced this turn: some text and/or some tool calls."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class Provider(ABC):
    name: str
    model: str

    @abstractmethod
    def chat(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> AssistantTurn:
        """Send one request and return the assistant's turn.

        `tools` is a list of neutral tool schemas:
            {"name": str, "description": str, "parameters": <json schema>}
        """

    def close(self) -> None:  # optional cleanup hook
        pass
