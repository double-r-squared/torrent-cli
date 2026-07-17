# torrent-cli

A natural-language torrent search assistant for your terminal. Describe what you
want — *"download ubuntu 24.04"*, *"find big buck bunny"* — and an LLM turns it
into a [Prowlarr](https://prowlarr.com) search, ranks the results, recommends
one, and starts the download **only after you approve it**.

Works with a **local model via [Ollama](https://ollama.com)** or **Anthropic's
Claude** — same interface, pick per run.

The interface is plain text with no third-party UI dependencies, so it's
portable: colour auto-enables in a terminal and falls away cleanly when piped,
logged, run over SSH, or with `--no-color` / `NO_COLOR`.

```
╭─────────────────────────────────────────────────────────╮
│ torrent-cli                                             │
│ provider ollama · model llama3.2:3b · prowlarr :9696    │
╰─────────────────────────────────────────────────────────╯
› download ubuntu 24.04

⠋ searching prowlarr for "ubuntu 24.04"…

  12 results for "ubuntu 24.04"
  #   Title                          Size    Seeds  Indexer
  1   Ubuntu 24.04.2 Desktop amd64   5.9GB    1240  LinuxTracker
  2   Ubuntu 24.04 Server            2.1GB     430  LinuxTracker

  #1 looks best — official desktop image with by far the most seeders.
  Want me to grab it?
```

## How it works

```
you ──▶ LLM (Ollama or Claude) ──▶ search_prowlarr ──▶ Prowlarr ──▶ every indexer
                    │
                    └── grab_release ──▶ [you approve] ──▶ Prowlarr ──▶ download client
```

The LLM only has two tools: `search_prowlarr` and `grab_release`. Prowlarr does
the actual work of querying indexers and handing grabs to your download client
(qBittorrent, etc.). `grab_release` is always gated behind a yes/no confirmation,
so nothing downloads without you saying so.

## Requirements

- **Python 3.11+**
- **A running Prowlarr** with at least one indexer configured, and its API key
  (Prowlarr → Settings → General → Security → API Key).
- **One of:**
  - [Ollama](https://ollama.com) running locally with a tool-capable model
    (`llama3.2`, `qwen2.5`, `llama3.1`, …), or
  - An **Anthropic API key** (`ANTHROPIC_API_KEY`).

## Install

```bash
git clone https://github.com/<you>/torrent-cli.git
cd torrent-cli
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Don't have Prowlarr yet? Run the stack with Docker

This repo ships a `docker-compose.yml` that brings up Prowlarr **and** qBittorrent
on a shared network (so Prowlarr can hand grabs to qBittorrent by name):

```bash
PUID=$(id -u) PGID=$(id -g) docker compose up -d
```

Then, one-time setup:

1. Open Prowlarr at <http://localhost:9696>.
2. **Add an indexer**: Indexers → Add Indexer → e.g. `LinuxTracker` (public, Linux
   ISOs) → Save.
3. **Add the download client**: Settings → Download Clients → `+` → qBittorrent →
   Host `qbittorrent`, Port `8080`, Username `admin`, Password from
   `docker logs qbittorrent` (a temporary password is printed on first start;
   set your own in qBittorrent → Options → Web UI).
4. **Copy the API key**: Settings → General → Security → API Key, into your
   `config.toml`.

Downloads land in `./downloads`.

## Configure

Copy the example config and fill it in (it's gitignored — it holds your keys):

```bash
cp config.example.toml config.toml
```

Every setting can also come from an environment variable or a CLI flag.
Precedence: **CLI flag > env var > config.toml > default**.

| Setting            | config.toml key     | Env var             | CLI flag              |
|--------------------|---------------------|---------------------|-----------------------|
| Backend            | `provider`          | `TORRENT_CLI_PROVIDER` | `--provider`       |
| Model              | `model`             | `TORRENT_CLI_MODEL` | `--model`             |
| Prowlarr URL       | `prowlarr_url`      | `PROWLARR_URL`      | `--prowlarr-url`      |
| Prowlarr API key   | `prowlarr_api_key`  | `PROWLARR_API_KEY`  | `--prowlarr-api-key`  |
| Ollama host        | `ollama_host`       | `OLLAMA_HOST`       | `--ollama-host`       |
| Anthropic API key  | `anthropic_api_key` | `ANTHROPIC_API_KEY` | —                     |
| qBittorrent URL    | `qbittorrent_url`   | `QBITTORRENT_URL`   | —                     |
| qBittorrent user   | `qbittorrent_username` | `QBITTORRENT_USERNAME` | —               |
| qBittorrent pass   | `qbittorrent_password` | `QBITTORRENT_PASSWORD` | —               |

(qBittorrent settings are only needed for `grab-url`, which talks to the
download client directly.)

## Run

```bash
torrent-cli                          # uses your config.toml
torrent-cli --provider ollama --model llama3.2:3b
torrent-cli --provider anthropic --model claude-opus-4-8
torrent-cli --no-color               # force plain text (also honours NO_COLOR)
```

In the REPL:

| Command             | What it does                          |
|---------------------|---------------------------------------|
| *(plain text)*      | a request, e.g. `find debian 12`      |
| `/settings`         | show provider, model, URL, and indexers |
| `/indexers`         | list/add/remove indexers (see below)  |
| `/grab <url>`       | download a magnet link or .torrent URL directly |
| `/provider <name>`  | switch between `ollama` and `anthropic` |
| `/model <name>`     | switch model for the current provider |
| `/clear`            | clear the conversation                |
| `/help`             | show commands                         |
| `/quit`             | exit                                  |

## Three ways to drive it

One capability layer (Prowlarr operations) behind three front doors:

**1. Interactive assistant (humans).** `torrent-cli` with no arguments — the
conversational REPL above, where an LLM turns your words into searches and grabs.

**2. Direct commands (humans and scripts).** Run one operation and exit — no LLM
involved. Add `--json` for machine-readable output.

```bash
torrent-cli search ubuntu 24.04
torrent-cli grab 1                 # grabs result #1 from your last search
torrent-cli list-indexers
torrent-cli find-indexers linux
torrent-cli add-indexer LinuxTracker
torrent-cli add-indexer MyTracker --field username=me --field password=secret
torrent-cli grab-url "magnet:?xt=urn:btih:..."   # hand a magnet/.torrent to qBittorrent
torrent-cli search sintel --json | jq .results
```

`search` remembers its results, so a later `grab <id>` — even in a separate
shell — knows which release you mean.

**3. MCP server (LLM agents).** Exposes `search`, `grab`, `grab_url`,
`add_indexer`, `list_indexers`, and `find_indexers` over the
[Model Context Protocol](https://modelcontextprotocol.io), so Claude Desktop,
Claude Code, or any MCP client can drive Prowlarr.

```bash
pip install 'torrent-cli[mcp]'
torrent-cli mcp        # serves over stdio
```

Add it to Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "torrent-cli": {
      "command": "torrent-cli-mcp",
      "env": {
        "PROWLARR_URL": "http://localhost:9696",
        "PROWLARR_API_KEY": "your-prowlarr-api-key"
      }
    }
  }
}
```

Or with Claude Code:

```bash
claude mcp add torrent-cli \
  --env PROWLARR_URL=http://localhost:9696 \
  --env PROWLARR_API_KEY=your-prowlarr-api-key \
  -- torrent-cli-mcp
```

Tool-call approval happens in the MCP client (you approve each grab in Claude),
so the server runs tools directly.

## Managing indexers

Indexers are the sources Prowlarr searches. torrent-cli exposes indexer
management through **two parallel interfaces over the same Prowlarr API** — the
terminal is for humans, the tool calls are for the LLM.

**In the terminal (for you):**

| Command                    | What it does                             |
|----------------------------|------------------------------------------|
| `/indexers`                | list configured indexers                 |
| `/indexers find <query>`   | search the catalog of available indexers |
| `/indexers add <name>`     | add an indexer (prompts for a login if it's private) |
| `/indexers remove <id>`    | remove an indexer                        |

Adding a **public** tracker just works. Adding a **private** one that needs a
login: `/indexers add <name>` detects the required fields and prompts you for
them (passwords are hidden). Non-interactively, use the CLI:

```bash
torrent-cli add-indexer MyTracker --field username=me --field password=secret
```

That's the manual, human path — you enter the credentials, in keeping with the
settings-vs-main-UI split (credentials live in the config/settings surface, not
the request flow).

**Via the LLM (headless):** the model has matching tools it can call on its own
— `list_indexers`, `find_indexers(query)`, and `add_indexer(name)` — so you can
just say *"what sources do I have?"* or *"add the linuxtracker indexer"* and it
drives the same operations. The AI adds **public** trackers on its own; anything
needing a **login** is left to you to add manually (the AI never handles your
credentials).

## Good things to search for

Prowlarr searches whatever indexers you've added. For legal, well-seeded test
content:

- **Linux ISOs** — `ubuntu 24.04`, `debian 12`, `linux mint`, `fedora`, `arch linux`
- **Blender open movies** (public domain / CC) — `big buck bunny`, `sintel`,
  `tears of steel`, `cosmos laundromat`
- **Public-domain media** — LibriVox audiobooks, Internet Archive collections

`big buck bunny` is a good tiny end-to-end test — a small, heavily-seeded,
public-domain file.

## Notes on models

- Small local models (like `llama3.2:3b`) can drive the tools but are less
  reliable at multi-step tool use. For better local results try `qwen2.5:7b` or
  `llama3.1:8b`.
- The Anthropic default is `claude-opus-4-8`; pass `--model claude-sonnet-5` or
  `--model claude-haiku-4-5` for cheaper/faster runs.

## Responsible use

This is a search-and-download convenience layer over software you already run.
Only download content you have the right to — Linux distributions, public-domain
and Creative-Commons media, and anything else you're authorized to obtain.

## License

MIT — see [LICENSE](LICENSE).
