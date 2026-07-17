"""Minimal qBittorrent Web API client.

Used by the `grab-url` path, which hands a magnet link or .torrent URL straight
to the download client — bypassing Prowlarr entirely. That's the manual/direct
route: the same thing Prowlarr does when it grabs a search result, but for a
torrent you (or the LLM) already have.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class QBittorrentError(Exception):
    """Raised when qBittorrent is unreachable, rejects auth, or refuses a torrent."""


@dataclass
class Torrent:
    hash: str
    name: str
    size: int
    progress: float  # 0..1
    dlspeed: int  # bytes/s
    upspeed: int  # bytes/s
    num_seeds: int
    num_leechs: int
    state: str
    eta: int  # seconds (8640000 == unknown/infinite)
    ratio: float


@dataclass
class TorrentFile:
    name: str
    size: int
    progress: float  # 0..1


class QBittorrentClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 45.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._authed = False
        # qBittorrent's WebUI requires a matching Referer/Origin or it returns 403.
        self._client = httpx.Client(timeout=timeout, headers={"Referer": self.base_url})

    def close(self) -> None:
        self._client.close()

    def _login(self) -> None:
        if self._authed:
            return
        try:
            resp = self._client.post(
                f"{self.base_url}/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
            )
        except httpx.HTTPError as exc:
            raise QBittorrentError(f"Could not reach qBittorrent at {self.base_url}: {exc}") from exc
        # Success is any 2xx (this varies by version: 200 "Ok." or 204 empty),
        # both with a SID cookie. Bad credentials come back as 200 "Fails.".
        if resp.status_code == 403:
            raise QBittorrentError("qBittorrent refused login (banned IP or bad Referer).")
        if resp.text.strip() == "Fails.":
            raise QBittorrentError("qBittorrent login failed — check the username and password.")
        if resp.status_code >= 400:
            raise QBittorrentError(f"qBittorrent login error (HTTP {resp.status_code}).")
        self._authed = True

    def add(self, source: str, savepath: str | None = None) -> str:
        """Add a torrent by magnet link or http(s) .torrent URL. Returns a label
        describing what was added."""
        source = source.strip()
        self._login()

        if source.startswith("magnet:"):
            return self._add(data={"urls": source}, savepath=savepath, label="magnet link")

        if source.startswith(("http://", "https://")):
            # Fetch the .torrent ourselves and upload the bytes — more reliable
            # than letting qBittorrent fetch the URL (some hosts redirect to
            # nodes that 401, e.g. archive.org).
            try:
                resp = self._client.get(source, follow_redirects=True)
            except httpx.HTTPError as exc:
                raise QBittorrentError(f"Could not fetch {source}: {exc}") from exc
            if resp.status_code >= 400:
                raise QBittorrentError(f"Fetching the torrent failed (HTTP {resp.status_code}).")
            if not resp.content[:1] == b"d":  # bencoded torrents start with 'd'
                raise QBittorrentError("That URL didn't return a .torrent file.")
            return self._add(
                files={"torrents": ("download.torrent", resp.content, "application/x-bittorrent")},
                savepath=savepath,
                label="torrent file",
            )

        raise QBittorrentError("Expected a magnet: link or an http(s) .torrent URL.")

    def _add(self, *, data: dict | None = None, files: dict | None = None,
             savepath: str | None = None, label: str) -> str:
        form = dict(data or {})
        if savepath:
            form["savepath"] = savepath
        try:
            resp = self._client.post(f"{self.base_url}/api/v2/torrents/add", data=form, files=files)
        except httpx.HTTPError as exc:
            raise QBittorrentError(f"Could not reach qBittorrent at {self.base_url}: {exc}") from exc
        if resp.status_code == 409:  # duplicate — torrent already present
            return f"{label} (already in qBittorrent)"
        if resp.status_code >= 400 or resp.text.strip() == "Fails.":
            raise QBittorrentError(f"qBittorrent rejected the {label} (HTTP {resp.status_code}).")
        return label

    # ---- monitoring -------------------------------------------------------
    def _get_json(self, path: str, params: dict | None = None):
        self._login()
        try:
            resp = self._client.get(f"{self.base_url}{path}", params=params)
        except httpx.HTTPError as exc:
            raise QBittorrentError(f"Could not reach qBittorrent at {self.base_url}: {exc}") from exc
        if resp.status_code >= 400:
            raise QBittorrentError(f"qBittorrent error (HTTP {resp.status_code}) on {path}.")
        return resp.json()

    def list_torrents(self) -> list[Torrent]:
        rows = self._get_json("/api/v2/torrents/info")
        return [
            Torrent(
                hash=t.get("hash", ""),
                name=t.get("name", ""),
                size=t.get("size", 0),
                progress=t.get("progress", 0.0),
                dlspeed=t.get("dlspeed", 0),
                upspeed=t.get("upspeed", 0),
                num_seeds=t.get("num_seeds", 0),
                num_leechs=t.get("num_leechs", 0),
                state=t.get("state", ""),
                eta=t.get("eta", 8640000),
                ratio=t.get("ratio", 0.0),
            )
            for t in rows
        ]

    def transfer_info(self) -> tuple[int, int]:
        """Global (download, upload) speed in bytes/s."""
        d = self._get_json("/api/v2/transfer/info")
        return d.get("dl_info_speed", 0), d.get("up_info_speed", 0)

    def torrent_files(self, torrent_hash: str) -> list[TorrentFile]:
        rows = self._get_json("/api/v2/torrents/files", params={"hash": torrent_hash})
        return [
            TorrentFile(name=f.get("name", ""), size=f.get("size", 0), progress=f.get("progress", 0.0))
            for f in rows
        ]
