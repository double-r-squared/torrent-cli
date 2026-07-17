"""LLM provider backends: a common interface over Anthropic and Ollama."""

from __future__ import annotations

from ..config import Config
from .base import AssistantTurn, Provider, ToolCall

__all__ = ["AssistantTurn", "Provider", "ToolCall", "build_provider"]


def build_provider(config: Config) -> Provider:
    """Instantiate the provider named in the config."""
    model = config.resolved_model()
    if config.provider == "ollama":
        from .ollama_provider import OllamaProvider

        return OllamaProvider(model=model, host=config.ollama_host)
    if config.provider == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(model=model, api_key=config.anthropic_api_key)
    raise ValueError(f"Unknown provider: {config.provider!r} (expected 'ollama' or 'anthropic')")
