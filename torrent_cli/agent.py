"""The agent loop: LLM <-> tools <-> Prowlarr, with a human gate on grabs."""

from __future__ import annotations

import json

from .prowlarr import ProwlarrClient, ProwlarrError, Release
from .providers import Provider, ToolCall
from .tools import SYSTEM_PROMPT, TOOL_SCHEMAS
from .ui import UI

MAX_TOOL_ITERATIONS = 6


class Agent:
    def __init__(self, provider: Provider, prowlarr: ProwlarrClient, ui: UI, max_results: int = 15) -> None:
        self.provider = provider
        self.prowlarr = prowlarr
        self.ui = ui
        self.max_results = max_results
        self.messages: list[dict] = []
        # id -> Release from the most recent search, used to resolve grab_release.
        self.last_results: dict[int, Release] = {}

    def reset(self) -> None:
        self.messages.clear()
        self.last_results.clear()

    def handle(self, user_text: str) -> None:
        """Run one user turn to completion (through any number of tool calls)."""
        self.messages.append({"role": "user", "content": user_text})

        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                turn = self.provider.chat(SYSTEM_PROMPT, self.messages, TOOL_SCHEMAS)
            except Exception as exc:  # noqa: BLE001 - surface any backend failure cleanly
                self.ui.error(f"LLM request failed: {exc}")
                return

            self.messages.append(
                {"role": "assistant", "content": turn.text, "tool_calls": turn.tool_calls}
            )
            if turn.text:
                self.ui.assistant(turn.text)

            if not turn.tool_calls:
                return

            for call in turn.tool_calls:
                result = self._run_tool(call)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": result,
                    }
                )

        self.ui.error("Stopped after too many tool calls without a final answer.")

    def _run_tool(self, call: ToolCall) -> str:
        if call.name == "search_prowlarr":
            return self._tool_search(call.arguments)
        if call.name == "grab_release":
            return self._tool_grab(call.arguments)
        if call.name == "list_indexers":
            return self._tool_list_indexers()
        if call.name == "find_indexers":
            return self._tool_find_indexers(call.arguments)
        if call.name == "add_indexer":
            return self._tool_add_indexer(call.arguments)
        return json.dumps({"error": f"unknown tool {call.name}"})

    def _tool_list_indexers(self) -> str:
        try:
            indexers = self.prowlarr.list_indexers()
        except ProwlarrError as exc:
            self.ui.error(str(exc))
            return json.dumps({"error": str(exc)})
        self.ui.indexers(indexers)
        return json.dumps({"count": len(indexers), "indexers": [i.__dict__ for i in indexers]})

    def _tool_find_indexers(self, args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return json.dumps({"error": "query was empty"})
        try:
            defs = self.prowlarr.find_indexer_definitions(query)
        except ProwlarrError as exc:
            self.ui.error(str(exc))
            return json.dumps({"error": str(exc)})
        self.ui.indexer_matches(query, defs)
        return json.dumps({"query": query, "count": len(defs), "definitions": [d.__dict__ for d in defs]})

    def _tool_add_indexer(self, args: dict) -> str:
        name = str(args.get("name", "")).strip()
        if not name:
            return json.dumps({"error": "name was empty"})
        try:
            indexer = self.prowlarr.add_indexer(name)
        except ProwlarrError as exc:
            self.ui.error(str(exc))
            return json.dumps({"error": str(exc)})
        self.ui.success(f"Added indexer: {indexer.name}")
        return json.dumps({"status": "added", "indexer": indexer.__dict__})

    def _tool_search(self, args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return json.dumps({"error": "query was empty"})
        limit = int(args.get("limit") or self.max_results)

        try:
            with self.ui.searching(query):
                releases = self.prowlarr.search(query, limit=limit)
        except ProwlarrError as exc:
            self.ui.error(str(exc))
            return json.dumps({"error": str(exc)})

        self.last_results = {r.id: r for r in releases}

        if not releases:
            self.ui.info(f"No results for “{query}”.")
            return json.dumps({"query": query, "count": 0, "results": []})

        self.ui.render_results(query, releases)
        return json.dumps(
            {"query": query, "count": len(releases), "results": [r.to_summary() for r in releases]}
        )

    def _tool_grab(self, args: dict) -> str:
        try:
            release_id = int(args.get("id"))
        except (TypeError, ValueError):
            return json.dumps({"error": "grab_release needs an integer id from the last search"})

        release = self.last_results.get(release_id)
        if release is None:
            return json.dumps(
                {"error": f"no release with id {release_id} in the last search results"}
            )

        # The human gate: nothing downloads until the user says yes.
        if not self.ui.confirm_grab(release):
            self.ui.info("Okay — not downloading that one.")
            return json.dumps(
                {
                    "status": "declined",
                    "note": "User declined the grab. Ask what they'd like to change or search again.",
                }
            )

        try:
            self.prowlarr.grab(release)
        except ProwlarrError as exc:
            self.ui.error(str(exc))
            return json.dumps({"status": "error", "error": str(exc)})

        self.ui.success(f"Sent to download client: {release.title}")
        return json.dumps({"status": "grabbed", "title": release.title})
