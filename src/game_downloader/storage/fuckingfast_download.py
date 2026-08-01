from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

from game_downloader.download.manager import DownloadError, DownloadManager
from game_downloader.http_diagnostics import HttpTrace, is_cloudflare_challenge
from game_downloader.models import (
    DownloadProgress,
    FuckingFastPart,
    FuckingFastSource,
    ResolvedDownload,
)
from game_downloader.security import SecurityError, ensure_public_host, normalized_host

logger = logging.getLogger(__name__)
_GO_PATH = re.compile(r"^/f/(?P<public_id>[A-Za-z0-9_-]{3,128})/go$")
_DOWNLOAD_PATH = re.compile(r"^/dl/[^/]+$")
FUCKINGFAST_HEADERS = {
    "User-Agent": "AuthorizedGameDownloader/0.1 (FuckingFast download client)",
    "Accept": "text/html,application/xhtml+xml",
}

ProgressCallback = Callable[[DownloadProgress], Awaitable[None] | None]
NoticeCallback = Callable[[str], Awaitable[None] | None]


class FuckingFastDownloadError(DownloadError):
    pass


class FuckingFastDownloader:
    """Resolve and download FuckingFast parts serially in one cookie session."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        resolve_hosts: bool = True,
    ) -> None:
        self._client = client
        self.resolve_hosts = resolve_hosts

    async def download(
        self,
        source: FuckingFastSource,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
        notice: NoticeCallback | None = None,
    ) -> list[Path]:
        destination.mkdir(parents=True, exist_ok=True)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            headers=FUCKINGFAST_HEADERS,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=20.0, read=60.0, write=30.0, pool=20.0),
        )
        if self.resolve_hosts:
            await ensure_public_host("fuckingfast.co")
            await ensure_public_host("dl.fuckingfast.co")
        downloaded: list[Path] = []
        try:
            manager = DownloadManager(client=client, resolve_hosts=self.resolve_hosts)
            total_parts = len(source.parts)
            for index, part in enumerate(source.parts, start=1):
                if notice:
                    await _call(notice, f"Part {index}/{total_parts} hazırlanıyor…")
                resolved = await self._resolve_part(client, part)
                if notice:
                    await _call(
                        notice,
                        f"Part {index}/{total_parts} indiriliyor: {part.filename}",
                    )
                path = await manager.download(
                    resolved,
                    destination,
                    progress=progress,
                    notice=notice,
                )
                downloaded.append(path)
                if notice:
                    await _call(notice, f"Part {index}/{total_parts} tamamlandı ve doğrulandı.")
            return downloaded
        finally:
            if owns_client:
                await client.aclose()

    async def _resolve_part(
        self,
        client: httpx.AsyncClient,
        part: FuckingFastPart,
    ) -> ResolvedDownload:
        parsed = urlsplit(str(part.page_url))
        page_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
        page_response = await self._send(
            client,
            "GET",
            page_url,
            headers=FUCKINGFAST_HEADERS,
            operation="fuckingfast-page",
        )
        _raise_for_download_page(page_response, "FuckingFast dosya sayfası")
        go_url, public_id = self._find_go_endpoint(page_response.text, page_url)
        post_headers = {
            **FUCKINGFAST_HEADERS,
            "HX-Request": "true",
            "HX-Current-URL": page_url,
            "Referer": page_url,
        }
        go_response = await self._send(
            client,
            "POST",
            go_url,
            headers=post_headers,
            content=b"",
            operation="fuckingfast-go",
        )
        if go_response.status_code != 200:
            raise FuckingFastDownloadError(
                f"FuckingFast /go isteği HTTP {go_response.status_code} döndürdü."
            )
        redirect_url = go_response.headers.get("hx-redirect")
        if not redirect_url:
            raise FuckingFastDownloadError(
                "FuckingFast /go yanıtında HX-Redirect başlığı yok."
            )
        self._validate_download_url(redirect_url)
        return ResolvedDownload(
            source_id=public_id,
            filename=part.filename,
            url=redirect_url,
            referer=page_url,
            require_attachment=True,
        )

    @staticmethod
    def _find_go_endpoint(html: str, page_url: str) -> tuple[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        endpoints: list[tuple[str, str]] = []
        for node in soup.select("[hx-post]"):
            if not isinstance(node, Tag):
                continue
            value = str(node.get("hx-post", "")).strip()
            endpoint = urljoin(page_url, value)
            parsed = urlsplit(endpoint)
            try:
                host = normalized_host(parsed.hostname)
            except SecurityError:
                continue
            match = _GO_PATH.fullmatch(parsed.path)
            if (
                parsed.scheme == "https"
                and host in {"fuckingfast.co", "www.fuckingfast.co"}
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and match
            ):
                candidate = (endpoint, match.group("public_id"))
                if candidate not in endpoints:
                    endpoints.append(candidate)
        if len(endpoints) != 1:
            raise FuckingFastDownloadError(
                "FuckingFast sayfasında tek bir geçerli HTMX indirme düğmesi bulunamadı."
            )
        return endpoints[0]

    @staticmethod
    def _validate_download_url(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or normalized_host(parsed.hostname) != "dl.fuckingfast.co"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not _DOWNLOAD_PATH.fullmatch(parsed.path)
        ):
            raise SecurityError("FuckingFast beklenmeyen bir indirme adresi döndürdü.")

    @staticmethod
    async def _send(
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        operation: str,
        content: bytes | None = None,
    ) -> httpx.Response:
        request = client.build_request(method, url, headers=headers, content=content)
        trace = HttpTrace(logger, operation, request.method, request.url, request.headers)
        try:
            response = await client.send(request)
        except (httpx.HTTPError, OSError) as exc:
            trace.exception(exc)
            raise
        trace.response(response, include_error_body=response.status_code >= 400)
        return response


def _raise_for_download_page(response: httpx.Response, label: str) -> None:
    if is_cloudflare_challenge(response):
        raise FuckingFastDownloadError(
            f"{label} Cloudflare doğrulaması istedi; HTTP oturumu doğrulanamadı."
        )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise FuckingFastDownloadError(
            f"{label} HTTP {response.status_code} döndürdü."
        ) from exc


async def _call(callback: Callable, value: object) -> None:
    result = callback(value)
    if hasattr(result, "__await__"):
        await result
