"""Entry point: parse args, wire everything together, run the REPL."""

from __future__ import annotations

import argparse
import getpass
import sys

from . import __version__
from .agent import Agent
from .config import Config, load_config
from .prowlarr import ProwlarrClient, ProwlarrError
from .providers import Provider, build_provider
from .qbittorrent import QBittorrentClient, QBittorrentError
from .ui import UI

HELP_TEXT = """commands:
  /help              show this help
  /settings          show provider, model, Prowlarr URL, and indexers
  /indexers          manage sources: /indexers [find <q> | add <name> | remove <id>]
  /grab <url>        download a magnet link or .torrent URL directly
  /provider <name>   switch backend: ollama | anthropic
  /model <name>      switch the model (for the current provider)
  /clear             clear the conversation history
  /quit, /exit       leave
anything else is treated as a request, e.g. "download big buck bunny"."""


def _add_global_opts(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=["ollama", "anthropic"], help="LLM backend to use.")
    parser.add_argument("--model", help="Model name for the chosen provider.")
    parser.add_argument("--prowlarr-url", dest="prowlarr_url", help="Prowlarr base URL.")
    parser.add_argument("--prowlarr-api-key", dest="prowlarr_api_key", help="Prowlarr API key.")
    parser.add_argument("--ollama-host", dest="ollama_host", help="Ollama host URL.")
    parser.add_argument("--no-color", action="store_true", help="Disable coloured output.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    # Global options live on a shared parent so they're accepted either before
    # or after a subcommand (or with no subcommand, which launches the REPL).
    common = argparse.ArgumentParser(add_help=False)
    _add_global_opts(common)

    parser = argparse.ArgumentParser(
        prog="torrent-cli",
        parents=[common],
        description="Natural-language torrent search over Prowlarr. "
        "Run with no command for the interactive assistant, or use a subcommand directly.",
    )
    parser.add_argument("--version", action="version", version=f"torrent-cli {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="[command]")

    p = sub.add_parser("search", parents=[common], help="Search indexers for a query.")
    p.add_argument("query", nargs="+", help="Search terms, e.g. ubuntu 24.04")
    p.add_argument("--limit", type=int, help="Max results.")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")

    p = sub.add_parser("grab", parents=[common], help="Grab a release id from the last search.")
    p.add_argument("id", type=int, help="Release id from `search`.")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")

    p = sub.add_parser("grab-url", parents=[common],
                       help="Send a magnet link or .torrent URL straight to qBittorrent.")
    p.add_argument("url", help="A magnet: link or an http(s) .torrent URL.")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")

    p = sub.add_parser("list-indexers", parents=[common], help="List configured indexers.")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")

    p = sub.add_parser("find-indexers", parents=[common], help="Search the indexer catalogue.")
    p.add_argument("query", nargs="+", help="Part of an indexer name.")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")

    p = sub.add_parser("add-indexer", parents=[common], help="Add an indexer by name.")
    p.add_argument("name", nargs="+", help="Exact indexer name, e.g. LinuxTracker.")
    p.add_argument("--field", action="append", metavar="KEY=VALUE",
                   help="Credential/config field for private trackers (repeatable).")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")

    sub.add_parser("mcp", parents=[common], help="Run the MCP server (stdio) for LLM agents.")

    sub.add_parser("up", parents=[common],
                   help="Bring up the VPN-tunnelled container stack (gluetun + qBittorrent + Prowlarr).")
    sub.add_parser("down", parents=[common], help="Tear the VPN stack down; host back to normal.")
    sub.add_parser("vpn-status", parents=[common], help="Show whether the VPN tunnel is up.")

    sub.add_parser("monitor", parents=[common],
                   help="Full-screen TUI: search/picker + live qBittorrent monitor.")

    return parser.parse_args(argv)


def _preflight(config: Config, ui: UI) -> bool:
    """Warn about likely-misconfigured setups. Returns False to abort startup."""
    if config.provider == "anthropic" and not config.anthropic_api_key:
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            ui.error("Provider is 'anthropic' but no API key is set (ANTHROPIC_API_KEY).")
            return False
    if not config.prowlarr_api_key:
        ui.info("No Prowlarr API key set yet — searches will fail until you add one.")
        ui.info("Set it in config.toml, PROWLARR_API_KEY, or --prowlarr-api-key.")
    return True


def _rebuild_provider(config: Config, ui: UI) -> Provider | None:
    try:
        return build_provider(config)
    except Exception as exc:  # noqa: BLE001
        ui.error(f"Could not initialise provider: {exc}")
        return None


def run_repl(config: Config, ui: UI) -> int:
    provider = _rebuild_provider(config, ui)
    if provider is None:
        return 1

    prowlarr = ProwlarrClient(config.prowlarr_url, config.prowlarr_api_key)
    qbittorrent = QBittorrentClient(
        config.qbittorrent_url, config.qbittorrent_username, config.qbittorrent_password
    )
    agent = Agent(provider, prowlarr, ui, qbittorrent=qbittorrent, max_results=config.max_results)

    ui.header()

    while True:
        try:
            raw = ui.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            ui.newline()
            break

        if not raw:
            continue

        if raw.startswith("/"):
            if _handle_command(raw, config, agent, ui):
                continue
            break  # /quit

        agent.handle(raw)

    prowlarr.close()
    qbittorrent.close()
    return 0


def _handle_command(raw: str, config: Config, agent: Agent, ui: UI) -> bool:
    """Handle a /command. Returns True to keep looping, False to quit."""
    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit"):
        return False
    if cmd == "/help":
        ui.help(HELP_TEXT)
        return True
    if cmd == "/settings":
        from . import stack

        ui.settings(config.provider, config.resolved_model(), config.prowlarr_url,
                    config.max_results, vpn_provider=config.vpn_provider,
                    vpn_configured=config.vpn_configured(), vpn_tunnel_ip=stack.tunnel_ip())
        try:
            ui.indexers(agent.prowlarr.list_indexers())
        except ProwlarrError as exc:
            ui.info(f"(indexers unavailable: {exc})")
        return True
    if cmd == "/indexers":
        _handle_indexers(arg, agent, ui)
        return True
    if cmd == "/grab":
        if not arg:
            ui.error("Usage: /grab <magnet-link-or-.torrent-URL>")
            return True
        if agent.qbittorrent is None:
            ui.error("No download client configured (qbittorrent_url/username/password).")
            return True
        try:
            label = agent.qbittorrent.add(arg)
            ui.success(f"Sent {label} to qBittorrent.")
        except QBittorrentError as exc:
            ui.error(str(exc))
        return True
    if cmd == "/clear":
        agent.reset()
        ui.info("Conversation cleared.")
        return True
    if cmd == "/provider":
        if arg not in ("ollama", "anthropic"):
            ui.error("Usage: /provider ollama | anthropic")
            return True
        config.provider = arg
        config.model = ""  # fall back to the new provider's default model
        provider = _rebuild_provider(config, ui)
        if provider is not None:
            agent.provider = provider
            ui.success(f"Switched to {arg} ({config.resolved_model()}).")
        return True
    if cmd == "/model":
        if not arg:
            ui.error("Usage: /model <name>")
            return True
        config.model = arg
        provider = _rebuild_provider(config, ui)
        if provider is not None:
            agent.provider = provider
            ui.success(f"Model set to {arg}.")
        return True

    ui.error(f"Unknown command {cmd}. Try /help.")
    return True


def _handle_indexers(arg: str, agent: Agent, ui: UI) -> None:
    """Human-facing indexer management: /indexers [list | find <q> | add <name> | remove <id>]."""
    parts = arg.split(maxsplit=1)
    sub = parts[0].lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""
    prowlarr = agent.prowlarr
    try:
        if sub in ("", "list"):
            ui.indexers(prowlarr.list_indexers())
        elif sub == "find":
            if not rest:
                ui.error("Usage: /indexers find <query>")
                return
            ui.indexer_matches(rest, prowlarr.find_indexer_definitions(rest))
        elif sub == "add":
            if not rest:
                ui.error("Usage: /indexers add <name>")
                return
            # Private trackers need credentials — prompt for each field.
            fields = prowlarr.indexer_credential_fields(rest)
            values = {}
            if fields:
                ui.info(f"'{rest}' needs credentials — enter each (blank to skip):")
                for f in fields:
                    label = f.get("label") or f.get("name")
                    if f.get("type") == "password":
                        val = getpass.getpass(f"    {label}: ")
                    else:
                        val = input(f"    {label}: ").strip()
                    if val:
                        values[f["name"]] = val
            indexer = prowlarr.add_indexer(rest, field_values=values or None)
            ui.success(f"Added indexer: {indexer.name}")
        elif sub == "remove":
            if not rest.isdigit():
                ui.error("Usage: /indexers remove <id>   (id shown by /indexers)")
                return
            prowlarr.remove_indexer(int(rest))
            ui.success(f"Removed indexer {rest}")
        else:
            ui.error("Usage: /indexers [list | find <query> | add <name> | remove <id>]")
    except ProwlarrError as exc:
        ui.error(str(exc))


def main() -> None:
    args = parse_args(sys.argv[1:])
    config = load_config(vars(args))

    # MCP server: stdout is the protocol channel — build and run, no UI output.
    if args.command == "mcp":
        from .mcp_server import build_server

        build_server(config).run()
        return

    ui = UI(color=False if args.no_color else None)

    # VPN stack lifecycle.
    if args.command in ("up", "down", "vpn-status"):
        from . import stack

        fn = {"up": stack.up, "down": stack.down, "vpn-status": stack.status}[args.command]
        sys.exit(fn(config, ui))

    # Full-screen monitoring TUI.
    if args.command == "monitor":
        from . import tui

        sys.exit(tui.run(config, ui))

    if args.command is None:
        # No subcommand → interactive assistant (the conversational human path).
        if not _preflight(config, ui):
            sys.exit(1)
        sys.exit(run_repl(config, ui))

    # Direct subcommand → non-interactive human/script path.
    from .commands import run_command

    sys.exit(run_command(args, config, ui))


if __name__ == "__main__":
    main()
