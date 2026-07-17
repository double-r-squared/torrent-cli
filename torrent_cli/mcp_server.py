"""MCP server exposing torrent-cli's Prowlarr operations to LLM agents.

Run it with `torrent-cli mcp` (or the `torrent-cli-mcp` console script). It
speaks MCP over stdio, so any MCP client — Claude Desktop, Claude Code, other
agents — can drive search / grab / add_indexer against your Prowlarr.

This is the LLM-facing front door. Tool-call approval happens in the MCP client
(the human using Claude approves the grab there), so tools execute directly here
and never prompt. It shares the same ProwlarrClient capability layer as the
terminal REPL and the direct CLI subcommands.

Config (Prowlarr URL + API key) is read from config.toml / environment, the same
as the rest of the tool — set PROWLARR_URL and PROWLARR_API_KEY in the MCP
client's server config, or point it at a working directory with a config.toml.

Note: with stdio transport, stdout is the protocol channel — never print to it.
"""

from __future__ import annotations

import json

from .config import Config, load_config
from .prowlarr import ProwlarrClient, ProwlarrError, Release


def build_server(config: Config):
    """Construct the FastMCP server. Imports mcp lazily so the base install
    doesn't require it."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise SystemExit(
            "The MCP server needs the 'mcp' package. Install it with:\n"
            "    pip install 'torrent-cli[mcp]'"
        ) from exc

    server = FastMCP("torrent-cli")
    prowlarr = ProwlarrClient(config.prowlarr_url, config.prowlarr_api_key)
    last_results: dict[int, Release] = {}

    @server.tool()
    def search(query: str, limit: int = 15) -> str:
        """Search all configured Prowlarr indexers and return releases ranked by
        seeders. Returns JSON; use a result's `id` with grab()."""
        try:
            releases = prowlarr.search(query, limit=limit)
        except ProwlarrError as exc:
            return json.dumps({"error": str(exc)})
        last_results.clear()
        last_results.update({r.id: r for r in releases})
        return json.dumps(
            {"query": query, "count": len(releases), "results": [r.to_summary() for r in releases]}
        )

    @server.tool()
    def grab(id: int) -> str:
        """Send a release from the most recent search() to the download client.
        `id` is the release id returned by search()."""
        release = last_results.get(id)
        if release is None:
            return json.dumps({"error": f"no release with id {id}; call search() first"})
        try:
            prowlarr.grab(release)
        except ProwlarrError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"status": "grabbed", "title": release.title})

    @server.tool()
    def add_indexer(name: str) -> str:
        """Add a public Prowlarr indexer by its exact name (e.g. 'LinuxTracker').
        Use find_indexers() to discover names. Public indexers only; private ones
        needing credentials must be added in the Prowlarr web UI."""
        try:
            indexer = prowlarr.add_indexer(name)
        except ProwlarrError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"status": "added", "indexer": indexer.__dict__})

    @server.tool()
    def list_indexers() -> str:
        """List the indexers currently configured in Prowlarr."""
        try:
            indexers = prowlarr.list_indexers()
        except ProwlarrError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"count": len(indexers), "indexers": [i.__dict__ for i in indexers]})

    @server.tool()
    def find_indexers(query: str) -> str:
        """Search Prowlarr's catalog of available indexer definitions by name.
        Returns names you can pass to add_indexer()."""
        try:
            defs = prowlarr.find_indexer_definitions(query)
        except ProwlarrError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(
            {"query": query, "count": len(defs), "definitions": [d.__dict__ for d in defs]}
        )

    return server


def main() -> None:
    build_server(load_config()).run()  # stdio transport


if __name__ == "__main__":
    main()
