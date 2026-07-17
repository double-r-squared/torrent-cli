"""Direct, non-interactive commands — the human/script front door.

Each runs one operation and exits: no LLM, no REPL. `search` caches its results
so a later `grab <id>` (a separate process) can reference them. Every command
takes --json for machine-readable output.

Same ProwlarrClient capability layer as the REPL and the MCP server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import Config
from .prowlarr import ProwlarrClient, ProwlarrError, Release
from .ui import UI


def _state_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "torrent-cli" / "last_search.json"


def _save_last_search(releases: list[Release]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "id": r.id,
                    "title": r.title,
                    "size_bytes": r.size_bytes,
                    "seeders": r.seeders,
                    "leechers": r.leechers,
                    "indexer": r.indexer,
                    "indexer_id": r.indexer_id,
                    "guid": r.guid,
                    "protocol": r.protocol,
                    "categories": r.categories,
                }
                for r in releases
            ]
        )
    )


def _load_last_search() -> dict[int, Release]:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    results: dict[int, Release] = {}
    for d in data:
        release = Release(
            id=d["id"],
            title=d["title"],
            size_bytes=d.get("size_bytes"),
            seeders=d.get("seeders"),
            leechers=d.get("leechers"),
            indexer=d.get("indexer", ""),
            indexer_id=d.get("indexer_id"),
            guid=d.get("guid", ""),
            protocol=d.get("protocol", "torrent"),
            categories=d.get("categories", []),
        )
        results[release.id] = release
    return results


def run_command(args, config: Config, ui: UI) -> int:
    """Dispatch a direct subcommand. Returns a process exit code."""
    prowlarr = ProwlarrClient(config.prowlarr_url, config.prowlarr_api_key)
    as_json = getattr(args, "json", False)
    try:
        return _dispatch(args, config, ui, prowlarr, as_json)
    except ProwlarrError as exc:
        _emit_error(str(exc), ui, as_json)
        return 1
    finally:
        prowlarr.close()


def _emit_error(message: str, ui: UI, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": message}))
    else:
        ui.error(message)


def _dispatch(args, config: Config, ui: UI, prowlarr: ProwlarrClient, as_json: bool) -> int:
    cmd = args.command

    if cmd == "search":
        query = " ".join(args.query)
        releases = prowlarr.search(query, limit=args.limit or config.max_results)
        _save_last_search(releases)
        if as_json:
            print(json.dumps({"query": query, "count": len(releases),
                              "results": [r.to_summary() for r in releases]}))
        elif not releases:
            ui.info(f"No results for “{query}”.")
        else:
            ui.render_results(query, releases)
        return 0

    if cmd == "grab":
        release = _load_last_search().get(args.id)
        if release is None:
            _emit_error(
                f"No release id {args.id} in the last search. Run `torrent-cli search …` first.",
                ui, as_json,
            )
            return 1
        prowlarr.grab(release)
        if as_json:
            print(json.dumps({"status": "grabbed", "title": release.title}))
        else:
            ui.success(f"Sent to download client: {release.title}")
        return 0

    if cmd == "list-indexers":
        indexers = prowlarr.list_indexers()
        if as_json:
            print(json.dumps({"count": len(indexers), "indexers": [i.__dict__ for i in indexers]}))
        else:
            ui.indexers(indexers)
        return 0

    if cmd == "find-indexers":
        query = " ".join(args.query)
        defs = prowlarr.find_indexer_definitions(query)
        if as_json:
            print(json.dumps({"query": query, "count": len(defs),
                              "definitions": [d.__dict__ for d in defs]}))
        else:
            ui.indexer_matches(query, defs)
        return 0

    if cmd == "add-indexer":
        name = " ".join(args.name)
        indexer = prowlarr.add_indexer(name)
        if as_json:
            print(json.dumps({"status": "added", "indexer": indexer.__dict__}))
        else:
            ui.success(f"Added indexer: {indexer.name}")
        return 0

    ui.error(f"Unknown command {cmd}")
    return 2
