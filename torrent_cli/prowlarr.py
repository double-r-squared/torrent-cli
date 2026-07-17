"""Minimal Prowlarr API client: search indexers and grab releases.

Prowlarr exposes a single search endpoint that fans out across every indexer
you have configured, plus a grab that hands a chosen release off to your
download client (qBittorrent, etc.). We only need those two calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx


class ProwlarrError(Exception):
    """Raised when Prowlarr is unreachable or returns an error."""


# Words that rarely appear in release titles and cause zero-result queries when
# a user (or model) tacks them on, e.g. "alpine linux" or "ubuntu iso download".
_FILLER_WORDS = {"linux", "iso", "download", "torrent", "distro", "the", "a", "an"}


def _query_variants(query: str) -> list[str]:
    """Progressively looser variants of a query, tried until one returns hits."""
    q = " ".join(query.split())
    variants = [q]
    tokens = q.split()
    if len(tokens) > 1:
        stripped = [t for t in tokens if t.lower() not in _FILLER_WORDS]
        if stripped and stripped != tokens:
            variants.append(" ".join(stripped))
        variants.append(max(tokens, key=len))  # single most distinctive token

    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = v.lower()
        if v and key not in seen:
            seen.add(key)
            out.append(v)
    return out


def human_size(num_bytes: int | None) -> str:
    if not num_bytes or num_bytes < 0:
        return "?"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if size < 1024 or unit == "PB":
            return f"{size:.0f}{unit}" if unit in ("B", "KB") else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


@dataclass
class Release:
    """One search result, with a short numeric id we assign for the LLM/user."""

    id: int
    title: str
    size_bytes: int | None
    seeders: int | None
    leechers: int | None
    indexer: str
    indexer_id: int | None
    guid: str
    protocol: str
    categories: list[str] = field(default_factory=list)

    @property
    def size(self) -> str:
        return human_size(self.size_bytes)

    def to_summary(self) -> dict:
        """Compact dict handed to the LLM as a tool result."""
        return {
            "id": self.id,
            "title": self.title,
            "size": self.size,
            "seeders": self.seeders if self.seeders is not None else 0,
            "indexer": self.indexer,
            "protocol": self.protocol,
        }


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 45.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(
            timeout=timeout,
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def ping(self) -> bool:
        try:
            resp = self._client.get(f"{self.base_url}/ping")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def search(self, query: str, limit: int = 15, categories: list[int] | None = None) -> list[Release]:
        """Search all configured indexers, best-seeded first.

        Many public indexers match query tokens literally against release
        titles, so a natural phrase like "alpine linux" can miss titles like
        "alpine standard 3.23". If the exact query finds nothing, we retry with
        filler words removed, then with just the most distinctive token.
        """
        if not self.api_key:
            raise ProwlarrError(
                "No Prowlarr API key configured. Set it in config.toml, the "
                "PROWLARR_API_KEY env var, or with --prowlarr-api-key."
            )
        for variant in _query_variants(query):
            releases = self._search_once(variant, limit, categories)
            if releases:
                return releases
        return []

    def _search_once(self, query: str, limit: int, categories: list[int] | None) -> list[Release]:
        params: dict = {"query": query, "type": "search", "limit": max(limit * 3, 50)}
        if categories:
            params["categories"] = categories
        try:
            resp = self._client.get(f"{self.base_url}/api/v1/search", params=params)
        except httpx.HTTPError as exc:
            raise ProwlarrError(f"Could not reach Prowlarr at {self.base_url}: {exc}") from exc

        if resp.status_code == 401:
            raise ProwlarrError("Prowlarr rejected the API key (401). Check your key.")
        if resp.status_code >= 400:
            raise ProwlarrError(f"Prowlarr search failed ({resp.status_code}): {resp.text[:200]}")

        raw = resp.json()
        raw.sort(key=lambda r: r.get("seeders") or 0, reverse=True)

        releases: list[Release] = []
        for idx, item in enumerate(raw[:limit], start=1):
            releases.append(
                Release(
                    id=idx,
                    title=item.get("title", "<untitled>"),
                    size_bytes=item.get("size"),
                    seeders=item.get("seeders"),
                    leechers=item.get("leechers"),
                    indexer=item.get("indexer", "?"),
                    indexer_id=item.get("indexerId"),
                    guid=item.get("guid", ""),
                    protocol=item.get("protocol", "torrent"),
                    categories=[c.get("name", "") for c in item.get("categories", []) if isinstance(c, dict)],
                )
            )
        return releases

    def grab(self, release: Release) -> None:
        """Tell Prowlarr to send this release to the configured download client."""
        if not release.guid or release.indexer_id is None:
            raise ProwlarrError("Release is missing a guid/indexerId; cannot grab it.")
        payload = {"guid": release.guid, "indexerId": release.indexer_id}
        try:
            resp = self._client.post(f"{self.base_url}/api/v1/search", json=payload)
        except httpx.HTTPError as exc:
            raise ProwlarrError(f"Could not reach Prowlarr at {self.base_url}: {exc}") from exc

        if resp.status_code >= 400:
            raise ProwlarrError(f"Grab failed ({resp.status_code}): {resp.text[:200]}")
