from __future__ import annotations

import logging
from hashlib import sha256
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

from game_downloader.http_diagnostics import HttpTrace
from game_downloader.models import (
    FuckingFastPart,
    FuckingFastSource,
    GameEntry,
    GameRelease,
)
from game_downloader.security import (
    SecurityError,
    validate_fuckingfast_part_url,
    validate_https_url,
)

logger = logging.getLogger(__name__)
WEB_REQUEST_HEADERS = {
    "User-Agent": "AuthorizedGameDownloader/0.1 (web search client)",
    "Accept": "text/html,application/xhtml+xml",
}


class InternetSearchProvider:
    """Search a configured website and resolve its FuckingFast part links."""

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
        return f"{self.base_url}?{urlencode({'s': query.strip()})}"

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
        parsed = self._parse_entry_headers(soup)
        if not parsed:
            logger.warning(
                "Web search response contains no usable "
                "header.entry-header h1.entry-title > a results."
            )
        self._results = {entry.id: entry for entry in parsed}
        return parsed

    def _parse_entry_headers(self, soup: BeautifulSoup) -> list[GameEntry]:
        results: list[GameEntry] = []
        seen_urls: set[str] = set()
        for header in soup.select("header.entry-header"):
            if not isinstance(header, Tag) or header.select_one(".entry-meta") is None:
                continue
            link = header.select_one("h1.entry-title > a[href]")
            if not isinstance(link, Tag):
                continue
            title = str(link.get_text(" ", strip=True) or link.get("title", "")).strip()
            if not title:
                continue
            detail_url = urljoin(self.base_url, str(link["href"]))
            try:
                validate_https_url(
                    detail_url,
                    self.allowed_hosts,
                    allow_local_http=self.allow_local_http,
                )
            except SecurityError as exc:
                logger.warning("Skipping external web search result: %s", exc)
                continue
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)
            result_id = sha256(detail_url.encode("utf-8")).hexdigest()[:32]
            results.append(
                GameEntry(
                    id=result_id,
                    title=title,
                    detail_url=detail_url,
                    source_name="FuckingFast",
                )
            )
        return results

    async def get_release(self, game_id: str) -> GameRelease:
        try:
            entry = self._results[game_id]
        except KeyError as exc:
            raise LookupError("Son web aramasındaki sonuçlardan birini seçin.") from exc
        if entry.detail_url is None:
            raise ValueError("Seçilen arama sonucunda detay bağlantısı yok.")
        response = await self._get(str(entry.detail_url))
        parts = self._find_fuckingfast_parts(response.text)
        return GameRelease(
            **entry.model_dump(exclude={"source"}),
            source=FuckingFastSource(parts=parts),
        )

    @staticmethod
    def _find_fuckingfast_parts(html: str) -> list[FuckingFastPart]:
        soup = BeautifulSoup(html, "html.parser")
        parts: list[FuckingFastPart] = []
        seen_filenames: set[str] = set()
        for link in soup.find_all("a", href=True):
            href = str(link["href"]).strip()
            try:
                filename, part_number = validate_fuckingfast_part_url(href)
            except (SecurityError, ValueError):
                continue
            if filename in seen_filenames:
                continue
            seen_filenames.add(filename)
            parts.append(
                FuckingFastPart(
                    page_url=href,
                    filename=filename,
                    part_number=part_number,
                )
            )
        if not parts:
            raise ValueError("Seçilen sayfada geçerli bir FuckingFast part bağlantısı yok.")
        return sorted(parts, key=lambda part: (part.part_number, part.filename.casefold()))
