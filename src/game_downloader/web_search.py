from __future__ import annotations

import json
import logging
import re
from html import unescape
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

from game_downloader.http_diagnostics import HttpTrace
from game_downloader.models import (
    BrowserDirectSource,
    BrowserDownloadRecord,
    GameEntry,
    GameRelease,
)
from game_downloader.security import validate_https_url

logger = logging.getLogger(__name__)
WEB_REQUEST_HEADERS = {
    "User-Agent": "AuthorizedGameDownloader/0.1 (web search client)",
    "Accept": "text/html,application/xhtml+xml",
}


class InternetSearchProvider:
    """Search listing metadata for games handled by the browser-direct provider."""

    def __init__(
        self,
        base_url: str,
        allowed_hosts: list[str],
        *,
        client: httpx.AsyncClient | None = None,
        allow_local_http: bool = False,
    ) -> None:
        parsed = urlsplit(base_url.strip())
        root_path = parsed.path.rstrip("/") + "/"
        normalized = urlunsplit((parsed.scheme, parsed.netloc, root_path, "", ""))
        self.base_url = validate_https_url(
            normalized,
            allowed_hosts,
            allow_local_http=allow_local_http,
        )
        self.allowed_hosts = allowed_hosts
        self.allow_local_http = allow_local_http
        self._client = client
        self._results: dict[str, GameEntry] = {}

    def search_url(self, query: str) -> str:
        return urljoin(self.base_url, f"search/{quote(query.strip(), safe='')}")

    async def _get(self, url: str) -> httpx.Response:
        validate_https_url(
            url,
            self.allowed_hosts,
            allow_local_http=self.allow_local_http,
        )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            headers=WEB_REQUEST_HEADERS,
            follow_redirects=False,
            timeout=httpx.Timeout(20.0),
        )
        current_url = url
        try:
            for redirect_count in range(4):
                request = client.build_request(
                    "GET",
                    current_url,
                    headers=WEB_REQUEST_HEADERS,
                )
                trace = HttpTrace(
                    logger,
                    f"web-search-hop-{redirect_count + 1}",
                    request.method,
                    request.url,
                    request.headers,
                )
                try:
                    response = await client.send(request)
                except (httpx.HTTPError, OSError) as exc:
                    trace.exception(exc)
                    raise
                trace.response(response)
                if not response.is_redirect:
                    response.raise_for_status()
                    return response
                location = response.headers.get("location")
                if not location or redirect_count == 3:
                    raise ValueError("Web araması geçersiz veya çok fazla yönlendirme döndürdü.")
                current_url = urljoin(str(response.url), location)
                validate_https_url(
                    current_url,
                    self.allowed_hosts,
                    allow_local_http=self.allow_local_http,
                )
            raise AssertionError("unreachable")
        finally:
            if owns_client:
                await client.aclose()

    async def search(self, query: str) -> list[GameEntry]:
        if not query.strip():
            return []
        response = await self._get(self.search_url(query))
        soup = BeautifulSoup(response.text, "html.parser")
        parsed = self._parse_listing_items(soup)
        if not parsed:
            logger.warning(
                "Web search response contains no usable "
                "elements with valid listing JSON results."
            )
        self._results = {entry.id: entry for entry in parsed}
        return parsed

    def _parse_listing_items(self, soup: BeautifulSoup) -> list[GameEntry]:
        results: list[GameEntry] = []
        seen: set[str] = set()
        for node in soup.select("[listing]"):
            if not isinstance(node, Tag):
                continue
            try:
                raw_listing = unescape(str(node.get("listing", "")))
                payload = json.loads(raw_listing)
                if not isinstance(payload, dict):
                    continue
                entry = self._entry_from_listing(payload)
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("Skipping invalid listing metadata", exc_info=True)
                continue
            if entry.id not in seen:
                seen.add(entry.id)
                results.append(entry)
        return results

    def _entry_from_listing(self, data: dict[str, object]) -> GameEntry:
        def first(*keys: str) -> object | None:
            for key in keys:
                if data.get(key) not in (None, ""):
                    return data[key]
            return None

        slug = str(first("slug") or "").strip().strip("/")
        title = str(first("title", "name", "game_title") or "").strip()
        raw_id = first("id")
        if raw_id is None or not title or not slug:
            raise ValueError("listing requires id, title and slug")
        detail_url = urljoin(self.base_url, f"game/{quote(slug, safe='')}")
        validate_https_url(
            detail_url, self.allowed_hosts, allow_local_http=self.allow_local_http
        )
        game_id = str(raw_id)
        archive_size = _size_in_bytes(
            first("size_gb", "runtime", "size_bytes", "archive_size", "size"),
            is_gb=first("size_gb") is not None,
        )
        raw_downloads = first("downloads", "download_records", "downloadRecords") or []
        downloads: list[BrowserDownloadRecord] = []
        if isinstance(raw_downloads, list):
            for record in raw_downloads:
                if not isinstance(record, dict):
                    continue
                record_id = record.get("id") or record.get("download_id")
                if record_id is not None:
                    downloads.append(BrowserDownloadRecord(
                        id=str(record_id),
                        name=str(
                            record.get("name")
                            or record.get("title")
                            or f"Download {record_id}"
                        ),
                        size=record.get("size") if isinstance(record.get("size"), int) else None,
                    ))
        source = BrowserDirectSource(page_url=detail_url, downloads=downloads)
        raw_genres = first("genres")
        genres = raw_genres if isinstance(raw_genres, list) else []
        return GameEntry(
            id=game_id,
            title=title,
            version=str(first("vote_average", "version") or "Unknown"),
            description=str(first("description", "excerpt") or ""),
            archive_size=archive_size,
            image_url=first("imageurl", "image_url", "image", "thumbnail"),
            cover_url=first("coverurl", "cover_url"),
            release_date=str(first("release_date", "releaseDate", "date") or "") or None,
            genres=genres,
            detail_url=detail_url,
            source_name="Browser Direct",
            source=source,
        )

    async def get_release(self, game_id: str) -> GameRelease:
        try:
            entry = self._results[game_id]
        except KeyError as exc:
            raise LookupError("Son web aramasındaki sonuçlardan birini seçin.") from exc
        if entry.detail_url is None:
            raise ValueError("Seçilen arama sonucunda detay bağlantısı yok.")
        if entry.source is None:
            raise ValueError("Seçilen sonuçta browser-direct kaynağı yok.")
        return GameRelease(**entry.model_dump())


def _size_in_bytes(value: object, *, is_gb: bool) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if value < 0:
            return None
        return round(value * 1024**3) if is_gb else round(value)
    if isinstance(value, str):
        match = re.search(r"(?i)(\d+(?:[.,]\d+)?)\s*(TB|GB|MB|KB|B)?", value)
        if match is None:
            return None
        amount = float(match.group(1).replace(",", "."))
        unit = (match.group(2) or ("GB" if is_gb else "B")).upper()
        multiplier = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return round(amount * multiplier[unit])
    return None
