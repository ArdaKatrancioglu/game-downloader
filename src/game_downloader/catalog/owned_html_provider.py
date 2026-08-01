from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import ValidationError

from game_downloader.http_diagnostics import HttpTrace
from game_downloader.models import (
    FileCryptSource,
    GameEntry,
    GameRelease,
    GoFileSource,
)
from game_downloader.security import (
    SecurityError,
    extract_gofile_content_id,
    validate_filecrypt_container_url,
    validate_https_url,
)

logger = logging.getLogger(__name__)
CATALOG_REQUEST_HEADERS = {
    "User-Agent": "AuthorizedGameDownloader/0.1 (user-owned catalog client)",
    "Accept": "text/html,application/xhtml+xml",
}


class OwnedHtmlCatalogProvider:
    def __init__(
        self,
        base_url: str,
        allowed_hosts: list[str],
        *,
        client: httpx.AsyncClient | None = None,
        allow_local_http: bool = False,
    ) -> None:
        self.base_url = validate_https_url(
            base_url.rstrip("/") + "/",
            allowed_hosts,
            allow_local_http=allow_local_http,
        )
        self.allowed_hosts = allowed_hosts
        self.allow_local_http = allow_local_http
        self._client = client
        self._results: dict[str, GameEntry] = {}

    async def _get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        validate_https_url(
            url,
            self.allowed_hosts,
            allow_local_http=self.allow_local_http,
        )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            headers=CATALOG_REQUEST_HEADERS,
            follow_redirects=False,
            timeout=httpx.Timeout(20.0),
        )
        try:
            request = client.build_request(
                "GET",
                url,
                params=params,
                headers=CATALOG_REQUEST_HEADERS,
            )
            trace = HttpTrace(
                logger, "catalog", request.method, request.url, request.headers
            )
            try:
                response = await client.send(request)
            except (httpx.HTTPError, OSError) as exc:
                trace.exception(exc)
                raise
            trace.response(response)
            if response.is_redirect:
                location = response.headers.get("location", "")
                redirect = urljoin(str(response.url), location)
                validate_https_url(
                    redirect,
                    self.allowed_hosts,
                    allow_local_http=self.allow_local_http,
                )
                redirect_request = client.build_request(
                    "GET",
                    redirect,
                    headers=CATALOG_REQUEST_HEADERS,
                )
                redirect_trace = HttpTrace(
                    logger,
                    "catalog-redirect",
                    redirect_request.method,
                    redirect_request.url,
                    redirect_request.headers,
                )
                try:
                    response = await client.send(redirect_request)
                except (httpx.HTTPError, OSError) as exc:
                    redirect_trace.exception(exc)
                    raise
                redirect_trace.response(response)
            response.raise_for_status()
            return response
        finally:
            if owns_client:
                await client.aclose()

    async def search(self, query: str) -> list[GameEntry]:
        if not query.strip():
            return []
        search_url = urlunsplit((*urlsplit(self.base_url)[:3], "", ""))
        response = await self._get(search_url, params={"s": query.strip()})
        soup = BeautifulSoup(response.text, "html.parser")
        parsed = self._parse_slide_results(soup)
        results_container = soup.select_one("div#masonry")
        if not parsed and isinstance(results_container, Tag):
            parsed = self._parse_contract_articles(results_container)
        if not parsed:
            logger.warning(
                "Catalog search response contains no usable div.slide.lazyload result cards."
            )
        self._results = {entry.id: entry for entry in parsed}
        return parsed

    def _parse_slide_results(self, soup: BeautifulSoup) -> list[GameEntry]:
        results: list[GameEntry] = []
        seen: set[str] = set()
        for card in soup.select("div.slide.lazyload"):
            assert isinstance(card, Tag)
            link = card.find("a", href=True)
            if not isinstance(link, Tag):
                logger.warning("Skipping div.slide.lazyload card without an href.")
                continue
            title_node = link.select_one(".screen-reader-text")
            title = (
                title_node.get_text(" ", strip=True)
                if title_node
                else link.get_text(" ", strip=True)
            )
            if not title:
                logger.warning("Skipping div.slide.lazyload card without a title.")
                continue
            detail_url = urljoin(self.base_url, str(link["href"]))
            try:
                validate_https_url(
                    detail_url,
                    self.allowed_hosts,
                    allow_local_http=self.allow_local_http,
                )
            except SecurityError as exc:
                logger.warning("Skipping external catalog result: %s", exc)
                continue
            slug = urlsplit(detail_url).path.strip("/") or re.sub(r"\W+", "-", title.lower())
            if slug in seen:
                continue
            seen.add(slug)
            version_node = card.select_one(".tagmetafield")
            results.append(
                GameEntry(
                    id=slug,
                    title=title,
                    version=(
                        version_node.get_text(" ", strip=True)
                        if version_node
                        else "Unknown"
                    ),
                    detail_url=detail_url,
                )
            )
        return results

    def _parse_contract_articles(self, container: Tag) -> list[GameEntry]:
        results: list[GameEntry] = []
        for article in container.select("article[data-game-id][data-title]"):
            assert isinstance(article, Tag)
            try:
                provider = article.get("data-provider", "gofile")
                if provider != "gofile":
                    raise ValueError("unsupported provider")
                link = article.find("a", href=True)
                detail_url = urljoin(self.base_url, str(link["href"])) if link else None
                if detail_url:
                    validate_https_url(
                        detail_url,
                        self.allowed_hosts,
                        allow_local_http=self.allow_local_http,
                    )
                content_id = article.get("data-content-id")
                source = (
                    GoFileSource(content_id=str(content_id))
                    if content_id
                    else None
                )
                entry = GameEntry(
                    id=str(article["data-game-id"]),
                    title=str(article["data-title"]),
                    version=str(article.get("data-version", "Unknown")),
                    archive_size=_optional_int(article.get("data-archive-size")),
                    detail_url=detail_url,
                    source=source,
                )
                if not entry.detail_url and not entry.source:
                    raise ValueError("missing detail link and share id")
                results.append(entry)
            except (ValidationError, ValueError, SecurityError) as exc:
                logger.warning("Skipping incomplete HTML catalog entry: %s", exc)
        return results

    async def get_release(self, game_id: str) -> GameRelease:
        try:
            entry = self._results[game_id]
        except KeyError as exc:
            raise LookupError("Select a result from the latest search.") from exc
        source = entry.source
        if entry.detail_url:
            response = await self._get(str(entry.detail_url))
            source = self._find_download_source(response.text)
        if source is None:
            raise ValueError("The selected page does not contain an approved download link.")
        return GameRelease(**entry.model_dump(exclude={"source"}), source=source)

    @staticmethod
    def _find_download_source(
        html: str,
    ) -> GoFileSource | FileCryptSource:
        soup = BeautifulSoup(html, "html.parser")
        filecrypt_source = None
        for link in soup.find_all("a", href=True):
            if _is_hidden(link):
                continue
            label = " ".join(link.get_text(" ", strip=True).upper().split())
            if label not in {"DOWNLOAD HERE", "DOWNLOAD"}:
                continue
            href = str(link["href"])
            if href.startswith("//"):
                href = "https:" + href
            try:
                return GoFileSource(
                    content_id=extract_gofile_content_id(href)
                )
            except SecurityError:
                pass
            try:
                validated = validate_filecrypt_container_url(href)
            except SecurityError:
                logger.info(
                    "Skipping visible DOWNLOAD HERE link because it is not "
                    "a GoFile share or FileCrypt container."
                )
                continue
            if filecrypt_source is None:
                filecrypt_source = FileCryptSource(url=validated)
        if filecrypt_source is not None:
            return filecrypt_source
        raise ValueError(
            "No visible DOWNLOAD HERE GoFile or FileCrypt link was found."
        )

    @staticmethod
    def _find_download_content_id(html: str) -> str:
        source = OwnedHtmlCatalogProvider._find_download_source(html)
        if not isinstance(source, GoFileSource):
            raise ValueError("The visible DOWNLOAD HERE link is a FileCrypt container.")
        return source.content_id


def _is_hidden(tag: Tag) -> bool:
    if str(tag.get("aria-hidden", "")).lower() == "true" or tag.has_attr("hidden"):
        return True
    style = str(tag.get("style", "")).replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(str(value))
