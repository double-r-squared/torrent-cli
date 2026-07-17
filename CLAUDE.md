# torrent-cli — agent context

Natural-language torrent search over [Prowlarr](https://prowlarr.com), driven by
a local (Ollama) or cloud (Anthropic) LLM, with direct CLI commands, an MCP
server, a live qBittorrent monitoring TUI, and an optional VPN-tunnelled
container stack. Repo: https://github.com/double-r-squared/torrent-cli

## Architecture — one capability layer, several front doors

**Capability layer** (pure API clients, no UI):
- `prowlarr.py` — Prowlarr search/grab, indexer management (list / find-definitions /
  add, incl. credentialed private trackers), query-variant fallback for picky indexers.
- `qbittorrent.py` — download client: add by magnet / .torrent URL / file bytes, plus
  monitoring (`list_torrents`, `transfer_info`, `torrent_files`).

**LLM providers** (`providers/`): unified `Provider.chat(system, messages, tools)` over
Anthropic (`anthropic_provider.py`) and Ollama (`ollama_provider.py`); neutral message /
tool / tool-call types in `base.py`; `build_provider(config)` in `__init__.py`.

**Front doors** (all over the same capability layer):
- **REPL** (`app.py:run_repl` + `agent.py`): conversational; the LLM turns requests into
  tool calls. Tools in `tools.py` (search_prowlarr, grab_release, grab_url,
  list/find/add_indexer). `grab_release` is gated behind a y/n confirm.
- **Direct CLI** (`commands.py`): `search / grab / grab-url / list-indexers /
  find-indexers / add-indexer`, all with `--json`. `search` caches results to a state
  file (`~/.cache/torrent-cli/last_search.json`) so `grab <id>` works across processes.
- **MCP server** (`mcp_server.py`): FastMCP over stdio — `search / grab / grab_url /
  add_indexer / list_indexers / find_indexers`. Optional `mcp` extra. Entry: `torrent-cli mcp`.
- **Monitor TUI** (`tui.py`): full-screen stdlib-`curses` UI — live qBittorrent monitor
  plane (torrent list, download-speed graph, files of selected) + keyboard search picker.
  Compact / compound layouts, nav in the top corners. Entry: `torrent-cli monitor`.

**VPN stack** (`stack.py`): `up / down / vpn-status` drive a docker-compose stack
(gluetun ProtonVPN + qBittorrent-through-VPN + Prowlarr) rendered from settings into
`~/.config/torrent-cli/stack/`; the host network is untouched.

`config.py` — layered config: **CLI flag > env var > config.toml > default**.
`ui.py` — plain-text UI (ANSI colour, auto-degrades; no third-party deps).

## Commands

`torrent-cli` (REPL) · `search|grab|grab-url|list-indexers|find-indexers|add-indexer`
(direct, `--json`) · `monitor` (TUI) · `mcp` (server) · `up|down|vpn-status` (VPN).

## Dev / run

```bash
python3 -m venv .venv && .venv/bin/pip install -e .   # add '.[mcp]' for the MCP server
cp config.example.toml config.toml                    # fill in creds (gitignored)
.venv/bin/python -m torrent_cli <command>
```

Python 3.11+ (uses `tomllib`). Runtime deps: `httpx`, `anthropic`, `ollama`. UI is
stdlib only (Rich was removed for portability); `mcp` is an optional extra.

## Local test environment (this machine)

Prowlarr (`:9696`, LinuxTracker indexer configured) and qBittorrent (`:8080`) run as
linuxserver Docker containers; qBittorrent is registered as Prowlarr's download client.
**Credentials and the Prowlarr API key live in the gitignored `config.toml` — never
commit them.** `docker-compose.yml` is the VPN stack (needs a real ProtonVPN WireGuard
key). The live VPN tunnel has not been tested end-to-end (no ProtonVPN key available);
everything up to the WireGuard handshake is validated.

## Conventions & principles

- **Settings vs main UI:** credentials/config (model, provider, indexers/trackers +
  logins, VPN keys, qB connection) belong in settings/config; the main UI is for
  requests, monitoring, and picking. (See memory `settings-vs-main-ui`.)
- **Human + LLM duality:** every capability is driveable both by a human (CLI/REPL/TUI)
  and by the LLM (tools/MCP). The AI adds *public* trackers; a human adds *credentialed*
  ones — the AI never handles credentials.
- **Portability:** no third-party UI deps — plain-text `ui.py` + stdlib `curses` TUI.
- **Models:** Anthropic default `claude-opus-4-8`; Ollama default `llama3.2:3b` (small —
  unreliable at multi-step tool use; suggest `qwen2.5:7b` / `llama3.1:8b`).
- **Testing:** validated against the real containers. TUI render logic is split into
  pure, unit-tested helpers; the curses layer is pty-smoke-tested. Provider message
  converters and the query-variant fallback have unit tests.

## Gotchas learned (don't re-discover these)

- qBittorrent login returns **204** on success (not 200) — accept any 2xx; bad creds come
  back as 200 `Fails.`.
- qB duplicate-add returns **409** — treat as "already present", not an error.
- gluetun **v3.40+** requires control-server auth — `gluetun-auth.toml` exposes only the
  read-only `/v1/publicip/ip` route so tunnel status can be polled.
- Prowlarr's **Internet Archive** indexer is flaky here (add-test hangs, searches return 0)
  even though archive.org itself is fast/reachable. It's the *only public video* indexer;
  the rest are private (need accounts).
- archive.org `_archive.torrent` URLs sometimes redirect to nodes that **401** — fetch the
  torrent bytes yourself and upload the file to qB rather than handing qB the URL.
- Prowlarr `forceSave=true` does **not** skip the connectivity test for a genuinely-failing
  indexer (e.g. Cloudflare-blocked 1337x); `add_indexer` does a normal add and surfaces the
  failure rather than configuring a broken source.
- With qB behind the VPN, Prowlarr's download-client host is **`protonvpn`** (the gluetun
  service name), not `qbittorrent`.

## Git

Branch before committing if on the default branch; push only when asked. Commit messages
end with the `Co-Authored-By:` / `Claude-Session:` trailers (see `git log`).
