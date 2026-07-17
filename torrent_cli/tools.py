"""Tool schemas exposed to the LLM.

Two tools:
  - search_prowlarr: fan out across indexers and return ranked releases.
  - grab_release: send a chosen release to the download client. This one is
    gated behind an explicit user confirmation in the agent loop.

grab_release takes the small integer `id` from the most recent search rather
than a raw guid, which is far more reliable for smaller local models.
"""

from __future__ import annotations

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "search_prowlarr",
        "description": (
            "Search all configured torrent/Usenet indexers via Prowlarr and return "
            "matching releases ranked by seeders. Call this to find something the "
            "user asked for. Turn a vague request into a good query (e.g. 'that "
            "linux distro from canonical' -> 'ubuntu')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms, e.g. 'ubuntu 24.04' or 'big buck bunny'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of results to return (default 15).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "grab_release",
        "description": (
            "Send a release to the download client to start downloading it. Only call "
            "this for a release id that appeared in the most recent search results, and "
            "only once the user has agreed to download it. The user will be asked to "
            "confirm before the download actually starts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "The id of the release from the most recent search results.",
                },
            },
            "required": ["id"],
        },
    },
]

SYSTEM_PROMPT = """You are torrent-cli, a concise assistant that helps the user \
find and download torrents through Prowlarr.

Workflow:
1. When the user asks for something, call `search_prowlarr` with a short, \
distinctive query — usually just the core name (e.g. "alpine", "ubuntu 24.04", \
"big buck bunny"). Avoid filler words like "linux", "iso", or "download"; \
indexers match titles literally, so extra words can hide good results.
2. Look at the returned releases (already ranked by seeders) and recommend the \
single best one. Prefer high seeders, a sensible file size, and a title that \
clearly matches the request. Briefly say why in one sentence.
3. To download, call `grab_release` with that release's id. The user is asked to \
confirm before anything downloads, so it is fine to propose a grab — but never \
grab something the user hasn't agreed to.
4. If a search returns nothing, try one reformulated query, then tell the user.

Keep replies short. The user already sees the results table, so don't re-list \
every release — just give your recommendation and next step."""
