"""Configuration loading.

Values are resolved with this precedence (highest wins):

    CLI flag  >  environment variable  >  config.toml  >  built-in default

config.toml is looked for in (first found wins):
    ./config.toml
    $XDG_CONFIG_HOME/torrent-cli/config.toml   (or ~/.config/torrent-cli/config.toml)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"

# Maps a config field to its environment variable.
_ENV = {
    "provider": "TORRENT_CLI_PROVIDER",
    "model": "TORRENT_CLI_MODEL",
    "prowlarr_url": "PROWLARR_URL",
    "prowlarr_api_key": "PROWLARR_API_KEY",
    "ollama_host": "OLLAMA_HOST",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "max_results": "TORRENT_CLI_MAX_RESULTS",
}


@dataclass
class Config:
    provider: str = "ollama"
    model: str = ""
    prowlarr_url: str = "http://localhost:9696"
    prowlarr_api_key: str = ""
    ollama_host: str = "http://localhost:11434"
    anthropic_api_key: str = ""
    max_results: int = 15

    def resolved_model(self) -> str:
        """The model to use, falling back to a sensible per-provider default."""
        if self.model:
            return self.model
        return DEFAULT_OLLAMA_MODEL if self.provider == "ollama" else DEFAULT_ANTHROPIC_MODEL


def _config_file_path() -> Path | None:
    local = Path.cwd() / "config.toml"
    if local.is_file():
        return local
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    candidate = base / "torrent-cli" / "config.toml"
    return candidate if candidate.is_file() else None


def load_config(cli_overrides: dict | None = None) -> Config:
    """Build a Config from file, environment, and CLI overrides."""
    data: dict = {}

    path = _config_file_path()
    if path is not None:
        with path.open("rb") as fh:
            data.update(tomllib.load(fh))

    for field, env_name in _ENV.items():
        value = os.environ.get(env_name)
        if value is not None and value != "":
            data[field] = value

    if cli_overrides:
        data.update({k: v for k, v in cli_overrides.items() if v is not None})

    known = {f.name for f in fields(Config)}
    kwargs = {k: v for k, v in data.items() if k in known}

    if "max_results" in kwargs:
        kwargs["max_results"] = int(kwargs["max_results"])

    return Config(**kwargs)
