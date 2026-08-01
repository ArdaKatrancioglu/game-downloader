from __future__ import annotations

import asyncio
import logging
import random
import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

from game_downloader.download.manager import DownloadCancelled, DownloadError, DownloadManager
from game_downloader.http_diagnostics import HttpTrace, is_cloudflare_challenge
from game_downloader.models import (
    DownloadProgress,
    FuckingFastPart,
    FuckingFastSource,
    MultipartDownloadProgress,
    ResolvedDownload,
)
from game_downloader.security import SecurityError, ensure_public_host, normalized_host

logger = logging.getLogger(__name__)
_GO_PATH = re.compile(r"^/f/(?P<public_id>[A-Za-z0-9_-]{3,128})/go$")
_DOWNLOAD_PATH = re.compile(r"^/dl/[^/]+$")
FUCKINGFAST_HEADERS = {
    "User-Agent": "AuthorizedGameDownloader/0.1",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
}

ProgressCallback = Callable[[MultipartDownloadProgress], Awaitable[None] | None]
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
        part_delay_min_seconds: float = 15.0,
        part_delay_max_seconds: float = 30.0,
        max_bytes_per_second: int | None = None,
    ) -> None:
        if part_delay_min_seconds < 0 or part_delay_max_seconds < 0:
            raise ValueError("Part gecikmesi negatif olamaz.")
        if part_delay_min_seconds > part_delay_max_seconds:
            raise ValueError("Part gecikmesi minimumu maksimumdan büyük olamaz.")
        if max_bytes_per_second is not None and max_bytes_per_second <= 0:
            raise ValueError("İndirme hızı limiti pozitif olmalıdır.")
        self._client = client
        self.resolve_hosts = resolve_hosts
        self.part_delay_min_seconds = part_delay_min_seconds
        self.part_delay_max_seconds = part_delay_max_seconds
        self.max_bytes_per_second = max_bytes_per_second
        self._pause = asyncio.Event()
        self._pause.set()
        self._cancel = asyncio.Event()
        self._manager: DownloadManager | None = None
        self._delete_completed_on_cancel = False

    def pause(self) -> None:
        self._pause.clear()
        if self._manager:
            self._manager.pause()

    def resume(self) -> None:
        self._pause.set()
        if self._manager:
            self._manager.resume()

    def cancel(self, *, delete_completed: bool = False) -> None:
        self._delete_completed_on_cancel = delete_completed
        self._cancel.set()
        self._pause.set()
        if self._manager:
            self._manager.cancel()

    async def download(
        self,
        source: FuckingFastSource,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
        notice: NoticeCallback | None = None,
    ) -> list[Path]:
        destination.mkdir(parents=True, exist_ok=True)
        self._cancel.clear()
        self._pause.set()
        self._delete_completed_on_cancel = False
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
            self._manager = DownloadManager(
                client=client,
                resolve_hosts=self.resolve_hosts,
                max_bytes_per_second=self.max_bytes_per_second,
            )
            total_parts = len(source.parts)
            completed_sizes: list[int] = []
            for index, part in enumerate(source.parts, start=1):
                self._raise_if_cancelled()
                if index > 1:
                    await self._wait_between_parts(notice)
                if notice:
                    await _call(notice, f"Part {index}/{total_parts} hazırlanıyor…")
                resolved = await self._resolve_part(client, part)
                self._raise_if_cancelled()
                if notice:
                    await _call(
                        notice,
                        f"Part {index}/{total_parts} indiriliyor: {part.filename}",
                    )
                async def part_progress(
                    value: DownloadProgress,
                    current_part: FuckingFastPart = part,
                    current_index: int = index,
                ) -> None:
                    if progress:
                        await _call(
                            progress,
                            _make_batch_progress(
                                part=current_part,
                                part_index=current_index,
                                part_count=total_parts,
                                progress=value,
                                completed_sizes=completed_sizes,
                            ),
                        )

                path = await self._manager.download(
                    resolved,
                    destination,
                    progress=part_progress,
                    notice=notice,
                )
                downloaded.append(path)
                completed_sizes.append(path.stat().st_size)
                if notice:
                    await _call(notice, f"Part {index}/{total_parts} tamamlandı ve doğrulandı.")
            return downloaded
        except DownloadCancelled as exc:
            if self._delete_completed_on_cancel:
                removed = _remove_completed_parts(downloaded)
                raise DownloadCancelled(
                    f"İndirme iptal edildi; {removed} tamamlanmış part silindi."
                ) from exc
            raise DownloadCancelled(
                "İndirme iptal edildi; tamamlanmış partlar korundu."
            ) from exc
        finally:
            self._manager = None
            if owns_client:
                await client.aclose()

    def _raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise DownloadCancelled("İndirme iptal edildi.")

    async def _wait_between_parts(self, notice: NoticeCallback | None) -> None:
        delay = random.uniform(
            self.part_delay_min_seconds,
            self.part_delay_max_seconds,
        )
        if delay <= 0:
            return
        if notice:
            await _call(notice, f"Sonraki part için {delay:.0f} saniye bekleniyor…")
        remaining = delay
        while remaining > 0:
            self._raise_if_cancelled()
            await self._pause.wait()
            interval = min(remaining, 0.25)
            started = asyncio.get_running_loop().time()
            await asyncio.sleep(interval)
            remaining -= asyncio.get_running_loop().time() - started

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
        request_headers = {**headers, "X-Request-ID": str(uuid.uuid4())}
        request = client.build_request(
            method,
            url,
            headers=request_headers,
            content=content,
        )
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


def _make_batch_progress(
    *,
    part: FuckingFastPart,
    part_index: int,
    part_count: int,
    progress: DownloadProgress,
    completed_sizes: list[int],
) -> MultipartDownloadProgress:
    completed_bytes = sum(completed_sizes)
    estimated_total: int | None = None
    total_percent: float | None = None
    total_eta: float | None = None
    if progress.total is not None:
        observed_sizes = [*completed_sizes, progress.total]
        average_size = sum(observed_sizes) / len(observed_sizes)
        estimated_total = round(
            completed_bytes + progress.total + average_size * (part_count - part_index)
        )
        downloaded_total = completed_bytes + progress.downloaded
        total_percent = min(100.0, downloaded_total * 100 / estimated_total)
        remaining = max(0, estimated_total - downloaded_total)
        if progress.bytes_per_second > 0:
            total_eta = remaining / progress.bytes_per_second
    return MultipartDownloadProgress(
        part_index=part_index,
        part_count=part_count,
        part_filename=part.filename,
        part=progress,
        completed_bytes=completed_bytes,
        estimated_total_bytes=estimated_total,
        total_percent=total_percent,
        total_eta_seconds=total_eta,
        total_is_estimate=part_index < part_count,
    )


def _remove_completed_parts(paths: list[Path]) -> int:
    removed = 0
    for path in paths:
        try:
            path.unlink()
        except OSError:
            logger.warning("Completed part could not be removed during cancellation: %s", path)
        else:
            removed += 1
    return removed


async def _call(callback: Callable, value: object) -> None:
    result = callback(value)
    if hasattr(result, "__await__"):
        await result
