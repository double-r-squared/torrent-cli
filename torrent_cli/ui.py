"""Plain-text terminal UI — stdlib only, no third-party dependencies.

Renders a header box, an aligned results table, and prompts using ordinary
print()/input(). ANSI colour is used only when writing to a real terminal and
NO_COLOR is unset (and --no-color wasn't passed); otherwise output is pure text,
so it pipes, logs, and travels over SSH cleanly.
"""

from __future__ import annotations

import contextlib
import os
import sys

from .prowlarr import ConfiguredIndexer, IndexerDefinition, Release

# ANSI SGR parameter strings (256-colour for wide terminal support).
ACCENT = "38;5;75"        # light blue
ACCENT_BOLD = "1;38;5;75"
MUTED = "38;5;245"        # grey
GREEN = "32"
YELLOW = "33"
RED = "31"
YELLOW_BOLD = "1;33"

# Table column widths (title/indexer are truncated to fit).
_W_ID, _W_TITLE, _W_SIZE, _W_SEEDS, _W_INDEXER = 3, 40, 8, 6, 12


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


class UI:
    def __init__(self, color: bool | None = None) -> None:
        if color is None:
            color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        self.color = color

    def _c(self, text: str, params: str) -> str:
        """Wrap text in an ANSI colour if colour is enabled."""
        if not self.color or not params:
            return text
        return f"\033[{params}m{text}\033[0m"

    # ---- chrome -----------------------------------------------------------
    def header(self) -> None:
        name, cmds = "torrent-cli", "/help  /settings"
        inner = max(40, len(name) + len(cmds) + 4)
        gap = inner - len(name) - len(cmds)
        line = f"{self._c(name, ACCENT_BOLD)}{' ' * gap}{self._c(cmds, MUTED)}"
        bar = "─" * (inner + 2)
        print(self._c(f"╭{bar}╮", ACCENT))
        print(f"{self._c('│ ', ACCENT)}{line}{self._c(' │', ACCENT)}")
        print(self._c(f"╰{bar}╯", ACCENT))

    def settings(self, provider: str, model: str, prowlarr_url: str, max_results: int,
                 vpn_provider: str | None = None, vpn_configured: bool = False,
                 vpn_tunnel_ip: str | None = None) -> None:
        print(self._c("  settings", ACCENT_BOLD))
        rows = [
            ("provider", provider),
            ("model", model),
            ("prowlarr url", prowlarr_url),
            ("max results", str(max_results)),
        ]
        if vpn_provider is not None:
            if vpn_tunnel_ip:
                rows.append(("vpn", f"up · {vpn_provider} · exit {vpn_tunnel_ip}"))
            elif vpn_configured:
                rows.append(("vpn", f"configured ({vpn_provider}) · not running — `torrent-cli up`"))
            else:
                rows.append(("vpn", "not configured (set wireguard_private_key in settings)"))
        for label, value in rows:
            print(self._c(f"    {label:<13} {value}", MUTED))
        print(self._c("  change with /provider <name> or /model <name>", MUTED))

    def prompt(self) -> str:
        return input(self._c("› ", ACCENT_BOLD))

    def help(self, text: str) -> None:
        print(self._c(text, MUTED))

    def newline(self) -> None:
        print()

    # ---- model / status output -------------------------------------------
    def assistant(self, text: str) -> None:
        print(text)

    def info(self, text: str) -> None:
        print(self._c(f"  {text}", MUTED))

    def success(self, text: str) -> None:
        print(f"  {self._c('✓', GREEN)} {self._c(text, GREEN)}")

    def error(self, text: str) -> None:
        print(f"  {self._c('✗', RED)} {self._c(text, RED)}")

    @contextlib.contextmanager
    def searching(self, query: str):
        """Print a one-line status before a search. No animation, so it pipes."""
        print(self._c(f"  searching prowlarr for “{query}”…", MUTED))
        yield

    # ---- results ----------------------------------------------------------
    def render_results(self, query: str, releases: list[Release]) -> None:
        count = self._c(str(len(releases)), ACCENT_BOLD)
        plural = "s" if len(releases) != 1 else ""
        print()
        print(f"  {count} result{plural} for “{query}”")

        header = (
            f"  {'#':>{_W_ID}}  {'Title':<{_W_TITLE}}  {'Size':>{_W_SIZE}}  "
            f"{'Seeds':>{_W_SEEDS}}  {'Indexer':<{_W_INDEXER}}"
        )
        print(self._c(header, ACCENT))
        rule_width = _W_ID + _W_TITLE + _W_SIZE + _W_SEEDS + _W_INDEXER + 8
        print(self._c("  " + "─" * rule_width, MUTED))

        for r in releases:
            seeders = r.seeders if r.seeders is not None else 0
            seed_code = GREEN if seeders >= 20 else (YELLOW if seeders >= 3 else RED)
            cells = (
                f"{r.id:>{_W_ID}}",
                f"{_truncate(r.title, _W_TITLE):<{_W_TITLE}}",
                f"{r.size:>{_W_SIZE}}",
                self._c(f"{seeders:>{_W_SEEDS}}", seed_code),
                self._c(f"{_truncate(r.indexer, _W_INDEXER):<{_W_INDEXER}}", MUTED),
            )
            print("  " + "  ".join(cells))

    # ---- indexers ---------------------------------------------------------
    def indexers(self, indexers: list[ConfiguredIndexer]) -> None:
        if not indexers:
            print(self._c("  no indexers configured — add one with /indexers add <name>", MUTED))
            return
        print(self._c(f"  {len(indexers)} configured indexer(s)", ACCENT_BOLD))
        for i in indexers:
            state = self._c("on", GREEN) if i.enabled else self._c("off", RED)
            print(f"    {self._c(f'[{i.id}]', MUTED)} {i.name}  {self._c(f'· {i.privacy}', MUTED)}  {state}")

    def indexer_matches(self, query: str, defs: list[IndexerDefinition]) -> None:
        if not defs:
            print(self._c(f"  no indexer definitions match “{query}”", MUTED))
            return
        print(self._c(f"  {len(defs)} match(es) for “{query}”  ·  add with /indexers add <name>", ACCENT_BOLD))
        for d in defs:
            print(f"    {d.name}  {self._c(f'· {d.privacy} · {d.protocol}', MUTED)}")

    # ---- confirmation gate -----------------------------------------------
    def confirm_grab(self, release: Release) -> bool:
        seeders = release.seeders if release.seeders is not None else 0
        print()
        print(self._c("  ┌─ grab this release? ─", YELLOW))
        print(f"  {self._c('│', YELLOW)}  {self._c(release.title, ACCENT_BOLD)}")
        print(f"  {self._c('│', YELLOW)}  {self._c(f'{release.size} · {seeders} seeders · {release.indexer}', MUTED)}")
        print(self._c("  └", YELLOW))
        try:
            answer = input(f"  {self._c('start download?', YELLOW_BOLD)} [y/N] ").strip().lower()
        except EOFError:
            return False
        return answer in ("y", "yes")
