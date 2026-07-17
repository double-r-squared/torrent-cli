"""Full-screen curses TUI: a picker plane and a live qBittorrent monitor plane.

Layouts: 'compact' (one plane at a time; ←/→ switch) and 'compound' (picker on
top, monitor below; ←/→ move focus). Navigation is signposted in the top
corners. The monitor updates ~1x/sec from qBittorrent. Stdlib curses only.

The rendering is split into pure helpers (below, returning plain text lines so
they're unit-testable) and a thin curses layer (`MonitorApp`) that positions
them and applies colour.
"""

from __future__ import annotations

import curses
import time
from collections import deque

from .config import Config
from .prowlarr import ProwlarrClient, ProwlarrError, Release, human_size
from .qbittorrent import QBittorrentClient, QBittorrentError, Torrent, TorrentFile

VBLOCKS = " ▁▂▃▄▅▆▇█"  # 0..8 eighths, bottom-up

_STATE_LABELS = {
    "downloading": "downloading", "forcedDL": "downloading", "metaDL": "metadata",
    "stalledDL": "stalled", "queuedDL": "queued", "checkingDL": "checking",
    "uploading": "seeding", "forcedUP": "seeding", "stalledUP": "seeding",
    "queuedUP": "queued", "checkingUP": "checking", "checkingResumeData": "checking",
    "pausedDL": "paused", "pausedUP": "paused", "error": "error",
    "missingFiles": "missing", "moving": "moving", "unknown": "?",
}


# ---- formatting -----------------------------------------------------------
def human_speed(n: int) -> str:
    if n < 1024:
        return f"{n} B/s"
    size = float(n)
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}/s"
    return f"{size:.1f} GB/s"


def human_eta(seconds: int | None) -> str:
    if seconds is None or seconds >= 8640000 or seconds < 0:
        return "∞"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def state_label(state: str) -> str:
    return _STATE_LABELS.get(state, state[:11])


def trunc(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def progress_bar(frac: float, width: int) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = round(frac * width)
    return "█" * filled + "░" * (width - filled)


def speed_graph(history, width: int, height: int) -> list[str]:
    """Block-character area chart of `history` (most recent on the right)."""
    if width <= 0 or height <= 0:
        return []
    samples = list(history)[-width:]
    samples = [0] * (width - len(samples)) + samples
    peak = max(samples) or 1
    rows = []
    for r in range(height):  # r=0 is the top row
        cell_from_bottom = height - 1 - r
        line = []
        for v in samples:
            eighths = round(v / peak * height * 8)
            full = eighths // 8
            rem = eighths % 8
            if cell_from_bottom < full:
                line.append("█")
            elif cell_from_bottom == full and rem:
                line.append(VBLOCKS[rem])
            else:
                line.append(" ")
        rows.append("".join(line))
    return rows


def sparkline(history, width: int) -> str:
    if width <= 0:
        return ""
    samples = list(history)[-width:]
    samples = [0] * (width - len(samples)) + samples
    peak = max(samples) or 1
    return "".join(VBLOCKS[round(v / peak * 8)] for v in samples)


def torrent_table(torrents: list[Torrent], width: int) -> tuple[str, list[str]]:
    w_prog, w_spd, w_sp, w_state, w_eta = 12, 10, 7, 11, 6
    fixed = 3 + w_prog + w_spd + w_spd + w_sp + w_state + w_eta + 8
    name_w = max(8, width - fixed)

    def row(idx, name, prog_cell, dl, up, sp, state, eta) -> str:
        return (f" {idx:>2} {name:<{name_w}} {prog_cell:<{w_prog}} {dl:>{w_spd}} "
                f"{up:>{w_spd}} {sp:>{w_sp}} {state:<{w_state}} {eta:>{w_eta}}")

    header = row("#", "Name", "Progress", "↓", "↑", "S/P", "State", "ETA")
    rows = []
    for i, t in enumerate(torrents, 1):
        prog = f"{progress_bar(t.progress, 6)} {t.progress * 100:>3.0f}%"
        rows.append(row(
            i, trunc(t.name, name_w), prog,
            human_speed(t.dlspeed) if t.dlspeed else "·",
            human_speed(t.upspeed) if t.upspeed else "·",
            f"{t.num_seeds}/{t.num_leechs}", state_label(t.state), human_eta(t.eta),
        ))
    return header, rows


def files_table(files: list[TorrentFile], width: int) -> list[str]:
    lines = []
    for f in files:
        name = f.name.rsplit("/", 1)[-1]
        meta = f"{human_size(f.size):>8}  {f.progress * 100:>3.0f}%"
        name_w = max(4, width - len(meta) - 4)
        lines.append(f"  {trunc(name, name_w):<{name_w}}  {meta}")
    return lines


def release_table(results: list[Release], width: int) -> tuple[str, list[str]]:
    w_size, w_seed, w_idx = 8, 6, 14
    name_w = max(8, width - (3 + w_size + w_seed + w_idx + 6))

    def row(idx, title, size, seeds, indexer) -> str:
        return (f" {idx:>2} {title:<{name_w}} {size:>{w_size}} "
                f"{seeds:>{w_seed}} {indexer:<{w_idx}}")

    header = row("#", "Title", "Size", "Seeds", "Indexer")
    rows = [
        row(i, trunc(r.title, name_w), r.size, r.seeders if r.seeders is not None else 0,
            trunc(r.indexer, w_idx))
        for i, r in enumerate(results, 1)
    ]
    return header, rows


# ---- curses app -----------------------------------------------------------
PICKER, MONITOR = 0, 1


class MonitorApp:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.qb = QBittorrentClient(
            config.qbittorrent_url, config.qbittorrent_username, config.qbittorrent_password
        )
        self.prowlarr = ProwlarrClient(config.prowlarr_url, config.prowlarr_api_key)

        self.mode = "compact"          # 'compact' | 'compound'
        self.plane = MONITOR           # focused plane
        self.torrents: list[Torrent] = []
        self.files: list[TorrentFile] = []
        self.dl_hist: deque = deque(maxlen=600)
        self.up_hist: deque = deque(maxlen=600)
        self.sel_torrent = 0
        self.query = ""
        self.last_searched = None
        self.results: list[Release] = []
        self.sel_result = 0
        self.picker_status = "type a query, Enter to search"
        self.status = ""
        self.qb_error: str | None = None
        self._last_poll = 0.0

    # -- lifecycle
    def run(self) -> int:
        try:
            curses.wrapper(self._main)
        except KeyboardInterrupt:
            pass
        finally:
            self.qb.close()
            self.prowlarr.close()
        return 0

    def _main(self, stdscr) -> None:
        curses.curs_set(0)
        stdscr.timeout(200)
        self._init_colors()
        self._poll()
        while True:
            now = time.monotonic()
            if now - self._last_poll >= 1.0:
                self._poll()
                self._last_poll = now
            self._draw(stdscr)
            ch = stdscr.getch()
            if ch != -1 and self._handle_key(ch) == "quit":
                break

    def _init_colors(self) -> None:
        self.color = False
        try:
            if not curses.has_colors():
                return
            curses.start_color()
            curses.use_default_colors()
            for pair, col in enumerate(
                (curses.COLOR_GREEN, curses.COLOR_CYAN, curses.COLOR_YELLOW, curses.COLOR_RED), 1
            ):
                curses.init_pair(pair, col, -1)
            self.color = True
        except curses.error:
            self.color = False

    def _c(self, pair: int) -> int:
        return curses.color_pair(pair) if self.color else 0

    # -- data
    def _poll(self) -> None:
        try:
            self.torrents = self.qb.list_torrents()
            dl, up = self.qb.transfer_info()
            self.dl_hist.append(dl)
            self.up_hist.append(up)
            self.qb_error = None
            if self.torrents:
                self.sel_torrent = min(self.sel_torrent, len(self.torrents) - 1)
                self.files = self.qb.torrent_files(self.torrents[self.sel_torrent].hash)
            else:
                self.files = []
        except QBittorrentError as exc:
            self.qb_error = str(exc)

    # -- drawing
    def _safe(self, win, y, x, text, attr=0) -> None:
        maxy, maxx = win.getmaxyx()
        if y < 0 or y >= maxy or x >= maxx:
            return
        try:
            win.addstr(y, x, text[: maxx - x - 1], attr)
        except curses.error:
            pass

    def _draw(self, stdscr) -> None:
        stdscr.erase()
        maxy, maxx = stdscr.getmaxyx()
        self._draw_corners(stdscr, maxx)
        body_top, body_bot = 1, maxy - 2
        if self.mode == "compound":
            split = body_top + (body_bot - body_top) // 2
            self._draw_picker(stdscr, body_top, split - 1, maxx, focused=self.plane == PICKER)
            self._safe(stdscr, split, 0, "─" * maxx, self._c(3))
            self._draw_monitor(stdscr, split + 1, body_bot, maxx, focused=self.plane == MONITOR)
        elif self.plane == PICKER:
            self._draw_picker(stdscr, body_top, body_bot, maxx, focused=True)
        else:
            self._draw_monitor(stdscr, body_top, body_bot, maxx, focused=True)
        self._draw_statusbar(stdscr, maxy - 1, maxx)
        stdscr.refresh()

    def _draw_corners(self, stdscr, width) -> None:
        left, right = " Search ", " Monitor "
        self._safe(stdscr, 0, 0, "─" * width, self._c(3))
        la = (curses.A_REVERSE if self.plane == PICKER else curses.A_BOLD) | self._c(3)
        ra = (curses.A_REVERSE if self.plane == MONITOR else curses.A_BOLD) | self._c(3)
        self._safe(stdscr, 0, 1, left, la)
        self._safe(stdscr, 0, max(0, width - len(right) - 1), right, ra)
        mid = f" {self.mode} "
        self._safe(stdscr, 0, max(0, (width - len(mid)) // 2), mid, self._c(3))

    def _draw_statusbar(self, stdscr, y, width) -> None:
        hint = "←/→ plane   ↑/↓ select   Tab compact/compound   Enter search·grab   q quit"
        msg = self.qb_error or self.status or hint
        attr = self._c(4) if self.qb_error else curses.A_DIM
        self._safe(stdscr, y, 0, msg[:width - 1], attr)

    def _draw_monitor(self, stdscr, y0, y1, width, focused) -> None:
        if y1 < y0:
            return
        dl = self.dl_hist[-1] if self.dl_hist else 0
        up = self.up_hist[-1] if self.up_hist else 0
        self._safe(stdscr, y0, 1, "↓ ", self._c(1))
        self._safe(stdscr, y0, 3, f"{human_speed(dl):<12}", self._c(1) | curses.A_BOLD)
        self._safe(stdscr, y0, 16, "↑ ", self._c(2))
        self._safe(stdscr, y0, 18, f"{human_speed(up):<12}", self._c(2) | curses.A_BOLD)
        self._safe(stdscr, y0, 32, f"peak ↓ {human_speed(max(self.dl_hist) if self.dl_hist else 0)}",
                   curses.A_DIM)

        avail = y1 - y0
        graph_h = min(8, max(3, avail // 3))
        gy = y0 + 1
        for i, line in enumerate(speed_graph(self.dl_hist, width - 2, graph_h)):
            self._safe(stdscr, gy + i, 1, line, self._c(1))
        self._safe(stdscr, gy + graph_h, 1, "↑" + sparkline(self.up_hist, width - 3), self._c(2))

        ty = gy + graph_h + 2
        if self.qb_error:
            self._safe(stdscr, ty, 1, "qBittorrent unreachable — is it running?", self._c(4))
            return
        if not self.torrents:
            self._safe(stdscr, ty, 1, "no torrents yet — grab something from the Search plane.",
                       curses.A_DIM)
            return

        header, rows = torrent_table(self.torrents, width - 1)
        self._safe(stdscr, ty, 1, header, curses.A_BOLD | self._c(3))
        list_room = max(1, (y1 - (ty + 1)) - 1 - min(6, len(self.files) + 1))
        start = max(0, self.sel_torrent - list_room + 1)
        for i in range(start, min(len(rows), start + list_room)):
            attr = curses.A_REVERSE if (i == self.sel_torrent and focused) else 0
            self._safe(stdscr, ty + 1 + (i - start), 1, rows[i].ljust(width - 2), attr)

        fy = ty + 1 + min(len(rows), list_room) + 1
        if fy < y1 and self.torrents:
            title = f"files · {trunc(self.torrents[self.sel_torrent].name, width - 12)}"
            self._safe(stdscr, fy, 1, title, curses.A_BOLD | self._c(3))
            for i, line in enumerate(files_table(self.files, width - 2)):
                if fy + 1 + i > y1:
                    break
                self._safe(stdscr, fy + 1 + i, 1, line, 0)

    def _draw_picker(self, stdscr, y0, y1, width, focused) -> None:
        if y1 < y0:
            return
        cursor = "_" if focused else ""
        self._safe(stdscr, y0, 1, "search: ", curses.A_BOLD | self._c(3))
        self._safe(stdscr, y0, 9, f"{self.query}{cursor}", 0)
        self._safe(stdscr, y0 + 1, 1, self.picker_status, curses.A_DIM)
        if not self.results:
            return
        header, rows = release_table(self.results, width - 1)
        self._safe(stdscr, y0 + 2, 1, header, curses.A_BOLD | self._c(3))
        room = max(1, y1 - (y0 + 3) + 1)
        start = max(0, self.sel_result - room + 1)
        for i in range(start, min(len(rows), start + room)):
            attr = curses.A_REVERSE if (i == self.sel_result and focused) else 0
            self._safe(stdscr, y0 + 3 + (i - start), 1, rows[i].ljust(width - 2), attr)

    # -- input
    def _handle_key(self, ch: int) -> str | None:
        if ch in (ord("q"), ord("Q")) and self.plane == MONITOR:
            return "quit"
        if ch == 3:  # Ctrl-C
            return "quit"
        if ch == curses.KEY_LEFT:
            self.plane = PICKER
        elif ch == curses.KEY_RIGHT:
            self.plane = MONITOR
        elif ch == ord("\t"):
            self.mode = "compound" if self.mode == "compact" else "compact"
        elif ch == curses.KEY_UP:
            self._move(-1)
        elif ch == curses.KEY_DOWN:
            self._move(1)
        elif ch in (curses.KEY_ENTER, 10, 13):
            self._enter()
        elif self.plane == PICKER:
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                self.query = self.query[:-1]
            elif 32 <= ch < 127:
                self.query += chr(ch)
        return None

    def _move(self, delta: int) -> None:
        if self.plane == MONITOR and self.torrents:
            self.sel_torrent = max(0, min(len(self.torrents) - 1, self.sel_torrent + delta))
            self.files = []  # refreshed on next poll for the new selection
        elif self.plane == PICKER and self.results:
            self.sel_result = max(0, min(len(self.results) - 1, self.sel_result + delta))

    def _enter(self) -> None:
        if self.plane != PICKER:
            return
        if self.query and self.query != self.last_searched:
            self._search()
        elif self.results:
            self._grab()

    def _search(self) -> None:
        self.picker_status = f"searching “{self.query}”…"
        try:
            self.results = self.prowlarr.search(self.query, limit=self.config.max_results)
            self.last_searched = self.query
            self.sel_result = 0
            self.picker_status = (f"{len(self.results)} results — ↑/↓ select, Enter to grab"
                                  if self.results else "no results")
        except ProwlarrError as exc:
            self.results = []
            self.picker_status = f"search failed: {exc}"

    def _grab(self) -> None:
        release = self.results[self.sel_result]
        try:
            self.prowlarr.grab(release)
            self.status = f"grabbed: {release.title[:60]}"
            self.plane = MONITOR
        except ProwlarrError as exc:
            self.status = f"grab failed: {exc}"


def run(config: Config, ui=None) -> int:
    return MonitorApp(config).run()
