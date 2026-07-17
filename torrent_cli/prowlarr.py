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


def _format_validation(resp: httpx.Response) -> str:
    """Turn Prowlarr's validation-error array into a readable one-liner."""
    try:
        data = resp.json()
        if isinstance(data, list):
            msgs = [d.get("errorMessage", "") for d in data if isinstance(d, dict)]
            msgs = [m for m in msgs if m]
            if msgs:
                return "; ".join(msgs)
    except Exception:  # noqa: BLE001 - fall back to raw text
        pass
    return f"HTTP {resp.status_code}: {resp.text[:200]}"


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


@dataclass
class ConfiguredIndexer:
    """An indexer currently set up in Prowlarr (a source searches run against)."""

    id: int
    name: str
    enabled: bool
    privacy: str
    protocol: str


@dataclass
class IndexerDefinition:
    """An entry from Prowlarr's catalog of indexers that *could* be added."""

    name: str
    privacy: str
    protocol: str
    implementation: str


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 45.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._schema_cache: list | None = None
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

    # ---- indexer management ----------------------------------------------
    def list_indexers(self) -> list[ConfiguredIndexer]:
        """The indexers currently configured in Prowlarr."""
        resp = self._get("/api/v1/indexer", "list indexers")
        return [
            ConfiguredIndexer(
                id=i["id"],
                name=i.get("name", ""),
                enabled=i.get("enable", False),
                privacy=i.get("privacy", "?"),
                protocol=i.get("protocol", "torrent"),
            )
            for i in resp.json()
        ]

    def find_indexer_definitions(self, query: str, limit: int = 25) -> list[IndexerDefinition]:
        """Search Prowlarr's catalog of ~hundreds of indexer definitions by name."""
        q = query.lower().strip()
        matches = [d for d in self._indexer_schema() if q in d.get("name", "").lower()]
        matches.sort(key=lambda d: d.get("name", "").lower())
        return [
            IndexerDefinition(
                name=d.get("name", ""),
                privacy=d.get("privacy", "?"),
                protocol=d.get("protocol", "torrent"),
                implementation=d.get("implementation", ""),
            )
            for d in matches[:limit]
        ]

    def _find_schema(self, name: str) -> dict:
        """Resolve an indexer definition by exact or unambiguous partial name."""
        key = name.lower().strip()
        schema = next((d for d in self._indexer_schema() if d.get("name", "").lower() == key), None)
        if schema is not None:
            return schema
        partial = [d for d in self._indexer_schema() if key in d.get("name", "").lower()]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            names = ", ".join(d["name"] for d in partial[:8])
            raise ProwlarrError(f"'{name}' is ambiguous. Matches: {names}")
        raise ProwlarrError(
            f"No indexer definition named '{name}'. Use find_indexer_definitions to search."
        )

    def indexer_credential_fields(self, name: str) -> list[dict]:
        """Fields a human must fill to add this indexer (empty, non-advanced
        credential-type fields — logins, API keys, cookies). Empty for public
        indexers or ones already configured."""
        schema = self._find_schema(name)
        for existing in self.list_indexers():
            if existing.name.lower() == schema.get("name", "").lower():
                return []
        fields = []
        for f in schema.get("fields", []):
            if f.get("type") in ("textbox", "password") and not f.get("value") and not f.get("advanced"):
                fields.append(
                    {"name": f.get("name"), "label": f.get("label", f.get("name")),
                     "type": f.get("type", "textbox")}
                )
        return fields

    def add_indexer(self, name: str, field_values: dict | None = None) -> ConfiguredIndexer:
        """Add an indexer by its exact (or unambiguous) definition name.

        Public indexers need no `field_values`. Private ones take a dict of
        field name -> value (username/password/apikey/etc.); Prowlarr's add-time
        connectivity test validates the credentials. Idempotent: returns the
        existing indexer if it's already configured.
        """
        schema = self._find_schema(name)
        for existing in self.list_indexers():
            if existing.name.lower() == schema.get("name", "").lower():
                return existing  # already configured

        body = dict(schema)
        body["enable"] = True
        body["appProfileId"] = self._app_profile_id()
        if field_values:
            body["fields"] = [dict(f) for f in body.get("fields", [])]
            for f in body["fields"]:
                if f.get("name") in field_values:
                    f["value"] = field_values[f["name"]]

        try:
            created = self._post_indexer(body)
        except ProwlarrError as exc:
            if schema.get("privacy") != "public" and not field_values:
                raise ProwlarrError(
                    f"'{schema['name']}' is {schema.get('privacy')} and needs credentials — "
                    f"add it manually (e.g. `/indexers add {schema['name']}` in the app, or "
                    f"`add-indexer --field ...`). ({exc})"
                ) from exc
            raise

        return ConfiguredIndexer(
            id=created["id"],
            name=created.get("name", ""),
            enabled=created.get("enable", False),
            privacy=created.get("privacy", "?"),
            protocol=created.get("protocol", "torrent"),
        )

    def remove_indexer(self, indexer_id: int) -> None:
        try:
            resp = self._client.delete(f"{self.base_url}/api/v1/indexer/{indexer_id}")
        except httpx.HTTPError as exc:
            raise ProwlarrError(f"Could not reach Prowlarr at {self.base_url}: {exc}") from exc
        if resp.status_code >= 400 and resp.status_code != 404:
            raise ProwlarrError(f"Remove failed ({resp.status_code}): {resp.text[:200]}")

    # ---- internal helpers -------------------------------------------------
    def _get(self, path: str, action: str) -> httpx.Response:
        try:
            resp = self._client.get(f"{self.base_url}{path}")
        except httpx.HTTPError as exc:
            raise ProwlarrError(f"Could not reach Prowlarr at {self.base_url}: {exc}") from exc
        if resp.status_code == 401:
            raise ProwlarrError("Prowlarr rejected the API key (401). Check your key.")
        if resp.status_code >= 400:
            raise ProwlarrError(f"Failed to {action} ({resp.status_code}): {resp.text[:200]}")
        return resp

    def _indexer_schema(self) -> list:
        if self._schema_cache is None:
            self._schema_cache = self._get("/api/v1/indexer/schema", "load indexer catalog").json()
        return self._schema_cache

    def _app_profile_id(self) -> int:
        try:
            profiles = self._client.get(f"{self.base_url}/api/v1/appprofile").json()
            return profiles[0]["id"] if profiles else 1
        except (httpx.HTTPError, KeyError, IndexError):
            return 1

    def _post_indexer(self, body: dict) -> dict:
        """Add an indexer. Prowlarr runs a connectivity test as part of the add;
        we let it, so we only ever configure sources that actually work rather
        than force-adding broken ones."""
        url = f"{self.base_url}/api/v1/indexer"
        try:
            resp = self._client.post(url, json=body)
        except httpx.TimeoutException as exc:
            raise ProwlarrError(
                "Timed out adding the indexer — Prowlarr's connectivity test didn't "
                "finish (the site may be slow or unreachable). Try another indexer."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProwlarrError(f"Could not reach Prowlarr at {self.base_url}: {exc}") from exc
        if resp.status_code >= 400:
            raise ProwlarrError(_format_validation(resp))
        return resp.json()
