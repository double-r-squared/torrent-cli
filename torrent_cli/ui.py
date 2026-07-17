"""Rich-powered terminal UI: header, spinner, results table, prompts."""

from __future__ import annotations

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from .prowlarr import Release

ACCENT = "#7dd3fc"  # sky blue
MUTED = "grey62"


class UI:
    def __init__(self) -> None:
        self.console = Console()

    # ---- chrome -----------------------------------------------------------
    def header(self, provider: str, model: str, prowlarr_url: str) -> None:
        title = Text("torrent-cli", style=f"bold {ACCENT}")
        subtitle = Text.assemble(
            ("provider ", MUTED), (f"{provider}", "bold"),
            ("  ·  model ", MUTED), (f"{model}", "bold"),
            ("  ·  prowlarr ", MUTED), (f"{prowlarr_url}", "bold"),
        )
        body = Text.assemble(
            title, "\n", subtitle, "\n\n",
            ("Describe what you want to download. ", "default"),
            ("Type ", MUTED), ("/help", ACCENT), (" for commands, ", MUTED),
            ("/quit", ACCENT), (" to exit.", MUTED),
        )
        self.console.print(Panel(body, box=ROUNDED, border_style=ACCENT, padding=(1, 2)))

    def prompt(self) -> str:
        return Prompt.ask(Text("›", style=f"bold {ACCENT}"), console=self.console)

    # ---- model / status output -------------------------------------------
    def assistant(self, text: str) -> None:
        self.console.print(Text(text, style="default"), highlight=False)

    def info(self, text: str) -> None:
        self.console.print(Text(f"  {text}", style=MUTED))

    def success(self, text: str) -> None:
        self.console.print(Text.assemble(("  ✓ ", "bold green"), (text, "green")))

    def error(self, text: str) -> None:
        self.console.print(Text.assemble(("  ✗ ", "bold red"), (text, "red")))

    def searching(self, query: str):
        """Context manager showing a spinner while a search runs."""
        return self.console.status(
            Text.assemble(("searching prowlarr for ", MUTED), (f"“{query}”", ACCENT), ("…", MUTED)),
            spinner="dots",
        )

    # ---- results ----------------------------------------------------------
    def render_results(self, query: str, releases: list[Release]) -> None:
        table = Table(
            box=SIMPLE_HEAVY,
            border_style=MUTED,
            header_style=f"bold {ACCENT}",
            expand=False,
            pad_edge=False,
        )
        table.add_column("#", justify="right", style="bold", width=3)
        table.add_column("Title", overflow="ellipsis", max_width=52, no_wrap=True)
        table.add_column("Size", justify="right", width=8)
        table.add_column("Seeds", justify="right", width=6)
        table.add_column("Indexer", overflow="ellipsis", max_width=18, no_wrap=True, style=MUTED)

        for r in releases:
            seeders = r.seeders if r.seeders is not None else 0
            seed_style = "green" if seeders >= 20 else ("yellow" if seeders >= 3 else "red")
            table.add_row(
                str(r.id),
                r.title,
                r.size,
                Text(str(seeders), style=seed_style),
                r.indexer,
            )

        count = Text.assemble(
            ("  ", ""), (f"{len(releases)}", f"bold {ACCENT}"),
            (f" result{'s' if len(releases) != 1 else ''} for ", MUTED), (f"“{query}”", "default"),
        )
        self.console.print()
        self.console.print(count)
        self.console.print(table)

    # ---- confirmation gate -----------------------------------------------
    def confirm_grab(self, release: Release) -> bool:
        seeders = release.seeders if release.seeders is not None else 0
        detail = Text.assemble(
            ("Grab this release?\n\n", "bold"),
            (f"  {release.title}\n", ACCENT),
            (f"  {release.size}  ·  {seeders} seeders  ·  {release.indexer}", MUTED),
        )
        self.console.print(Panel(detail, box=ROUNDED, border_style="yellow", padding=(1, 2)))
        return Confirm.ask("  Start download", default=False, console=self.console)
