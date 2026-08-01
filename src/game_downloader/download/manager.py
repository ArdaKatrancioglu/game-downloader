from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from game_downloader.download.progress import ProgressTracker
from game_downloader.http_diagnostics import (
    HttpTrace,
    is_cloudflare_challenge,
    response_preview,
    safe_url,
)
from game_downloader.models import DownloadProgress, ResolvedDownload
from game_downloader.security import (
    SecurityError,
    ensure_public_host,
    normalized_host,
    safe_filename,
)

logger = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(DownloadError):
    pass


ProgressCallback = Callable[[DownloadProgress], Awaitable[None] | None]
NoticeCallback = Callable[[str], Awaitable[None] | None]


class DownloadManager:
    _destination_locks: dict[Path, asyncio.Lock] = {}
    _locks_guard = asyncio.Lock()

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        max_redirects: int = 3,
        max_attempts: int = 3,
        chunk_size: int = 256 * 1024,
        resolve_hosts: bool = True,
    ) -> None:
        self._client = client
        self.max_redirects = max_redirects
        self.max_attempts = max_attempts
        self.chunk_size = chunk_size
        self.resolve_hosts = resolve_hosts
        self._pause = asyncio.Event()
        self._pause.set()
        self._cancel = asyncio.Event()

    def pause(self) -> None:
        self._pause.clear()

    def resume(self) -> None:
        self._pause.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.set()

    @staticmethod
    def has_recommended_space(folder: Path, expected_size: int | None) -> bool:
        if expected_size is None:
            return True
        return shutil.disk_usage(folder).free >= int(expected_size * 1.5)

    async def download(
        self,
        resolved: ResolvedDownload,
        destination_folder: Path,
        *,
        progress: ProgressCallback | None = None,
        notice: NoticeCallback | None = None,
    ) -> Path:
        destination_folder.mkdir(parents=True, exist_ok=True)
        filename = safe_filename(resolved.filename)
        destination = (destination_folder / filename).resolve()
        if destination.parent != destination_folder.resolve():
            raise DownloadError("The destination filename is unsafe.")
        if destination.exists():
            raise DownloadError("A file with this name already exists.")
        lock = await self._get_lock(destination)
        if lock.locked():
            raise DownloadError("A download to this destination is already running.")
        async with lock:
            self._cancel.clear()
            return await self._download_locked(
                resolved,
                destination,
                progress=progress,
                notice=notice,
            )

    @classmethod
    async def _get_lock(cls, destination: Path) -> asyncio.Lock:
        async with cls._locks_guard:
            return cls._destination_locks.setdefault(destination, asyncio.Lock())

    async def _download_locked(
        self,
        resolved: ResolvedDownload,
        destination: Path,
        *,
        progress: ProgressCallback | None,
        notice: NoticeCallback | None,
    ) -> Path:
        part = destination.with_name(destination.name + ".part")
        url = str(resolved.url)
        initial_host = self._validate_download_url(url)
        if self.resolve_hosts:
            await ensure_public_host(initial_host)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=15.0),
        )
        try:
            for attempt in range(self.max_attempts):
                try:
                    await self._single_attempt(
                        client,
                        url,
                        initial_host,
                        part,
                        resolved.size,
                        str(resolved.referer) if resolved.referer else None,
                        resolved.require_attachment,
                        progress,
                        notice,
                    )
                    break
                except DownloadCancelled:
                    raise
                except (httpx.NetworkError, httpx.TimeoutException) as exc:
                    if attempt + 1 >= self.max_attempts:
                        raise DownloadError(
                            "The download was interrupted. The partial file was kept for resume."
                        ) from exc
                    await asyncio.sleep(2**attempt)
            self._validate_size(part, resolved.size)
            if resolved.checksum_sha256:
                self._validate_checksum(part, resolved.checksum_sha256)
            os.replace(part, destination)
            return destination
        finally:
            if owns_client:
                await client.aclose()

    async def _single_attempt(
        self,
        client: httpx.AsyncClient,
        url: str,
        initial_host: str,
        part: Path,
        expected_size: int | None,
        referer: str | None,
        require_attachment: bool,
        progress: ProgressCallback | None,
        notice: NoticeCallback | None,
    ) -> None:
        existing = part.stat().st_size if part.exists() else 0
        headers = {"Referer": referer} if referer else {}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        response = await self._open_response(client, url, headers, initial_host)
        try:
            if response.status_code == 416 and expected_size == existing:
                return
            if response.status_code not in {200, 206}:
                raise DownloadError(f"The download server returned HTTP {response.status_code}.")
            disposition = response.headers.get("content-disposition", "")
            if require_attachment and not disposition.casefold().startswith("attachment"):
                raise DownloadError(
                    "The download server did not return an attachment response."
                )
            if existing and response.status_code != 206:
                existing = 0
                if notice:
                    await _call(
                        notice,
                        "The server does not support resume; restarting the download from zero.",
                    )
            mode = "ab" if existing and response.status_code == 206 else "wb"
            total = _response_total(response, existing, expected_size)
            tracker = ProgressTracker(existing)
            downloaded = existing
            with part.open(mode) as output:
                async for chunk in response.aiter_bytes(self.chunk_size):
                    if self._cancel.is_set():
                        raise DownloadCancelled(
                            "Download cancelled. The partial file was kept for resume."
                        )
                    await self._pause.wait()
                    if self._cancel.is_set():
                        raise DownloadCancelled(
                            "Download cancelled. The partial file was kept for resume."
                        )
                    output.write(chunk)
                    downloaded += len(chunk)
                    if expected_size is not None and downloaded > expected_size:
                        raise DownloadError("The server sent more data than expected.")
                    if progress:
                        await _call(progress, tracker.update(downloaded, total))
                output.flush()
                os.fsync(output.fileno())
            if total is not None and downloaded != total:
                raise DownloadError(
                    f"Size validation failed: expected {total} bytes, "
                    f"received {downloaded}."
                )
        finally:
            await response.aclose()

    async def _open_response(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        initial_host: str,
    ) -> httpx.Response:
        current = url
        for redirect_count in range(self.max_redirects + 1):
            request = client.build_request("GET", current, headers=headers)
            trace = HttpTrace(
                logger,
                f"download-hop-{redirect_count + 1}",
                request.method,
                request.url,
                request.headers,
            )
            try:
                response = await client.send(request, stream=True)
            except (httpx.HTTPError, OSError) as exc:
                trace.exception(exc)
                raise
            possible_cloudflare = (
                response.status_code in {403, 503}
                and (
                    response.headers.get("server", "").lower() == "cloudflare"
                    or bool(response.headers.get("cf-ray"))
                )
            )
            if possible_cloudflare:
                content_length = response.headers.get("content-length", "")
                if content_length.isdigit() and int(content_length) <= 1024 * 1024:
                    await response.aread()
            trace.response(response, include_error_body=possible_cloudflare)
            if is_cloudflare_challenge(response):
                logger.warning(
                    "Download access diagnosis classification=cloudflare_challenge "
                    "operation=%s status=%d url=%s referer_present=%s referer=%s "
                    "server=%s cf_ray=%s content_type=%s body_preview=%r",
                    f"download-hop-{redirect_count + 1}",
                    response.status_code,
                    safe_url(response.url),
                    bool(headers.get("Referer")),
                    safe_url(headers["Referer"])
                    if headers.get("Referer")
                    else "<none>",
                    response.headers.get("server", "<missing>"),
                    response.headers.get("cf-ray", "<missing>"),
                    response.headers.get("content-type", "<missing>"),
                    response_preview(response),
                )
                await response.aclose()
                raise DownloadError(
                    "The download server returned a Cloudflare verification page. "
                    "The Referer was sent, but this HTTP client cannot complete browser "
                    "JavaScript or human verification."
                )
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            await response.aclose()
            if not location:
                raise DownloadError("The download server returned an invalid redirect.")
            if redirect_count >= self.max_redirects:
                raise DownloadError("The download exceeded the redirect limit.")
            current = urljoin(current, location)
            host = self._validate_download_url(current)
            if host != initial_host:
                raise SecurityError(f"Download redirected to an unexpected domain: {host}")
            if self.resolve_hosts:
                await ensure_public_host(host)
        raise AssertionError("unreachable")

    @staticmethod
    def _validate_download_url(url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise SecurityError("The provider-issued download URL must use HTTPS.")
        return normalized_host(parsed.hostname)

    @staticmethod
    def _validate_size(path: Path, expected: int | None) -> None:
        if expected is not None and path.stat().st_size != expected:
            raise DownloadError(
                f"Size validation failed: expected {expected} bytes, "
                f"received {path.stat().st_size}."
            )

    @staticmethod
    def _validate_checksum(path: Path, expected: str) -> None:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != expected.lower():
            raise DownloadError("SHA-256 validation failed. The partial file was kept.")


def _response_total(
    response: httpx.Response,
    existing: int,
    expected: int | None,
) -> int | None:
    if expected is not None:
        return expected
    content_range = response.headers.get("content-range")
    if content_range and "/" in content_range:
        total = content_range.rsplit("/", 1)[1]
        if total.isdigit():
            return int(total)
    content_length = response.headers.get("content-length")
    return existing + int(content_length) if content_length and content_length.isdigit() else None


async def _call(callback: Callable, value: object) -> None:
    result = callback(value)
    if asyncio.iscoroutine(result):
        await result


async def cleanup_lock_registry() -> None:
    """Drop completed lock entries; useful for long-running sessions and tests."""
    async with DownloadManager._locks_guard:
        completed = [
            destination
            for destination, lock in DownloadManager._destination_locks.items()
            if not lock.locked()
        ]
        for destination in completed:
            with suppress(KeyError):
                del DownloadManager._destination_locks[destination]
