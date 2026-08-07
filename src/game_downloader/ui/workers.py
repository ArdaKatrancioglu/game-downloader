from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from PySide6.QtCore import QThread, Signal

from game_downloader.archive.extractor import ArchiveExtractor
from game_downloader.download.manager import DownloadCancelled, DownloadManager
from game_downloader.download.progress import ProgressTracker
from game_downloader.error_diagnostics import log_exception
from game_downloader.models import BrowserDirectSource, DownloadProgress, ResolvedDownload
from game_downloader.storage.browser_direct import BrowserDirectDownloader

logger = logging.getLogger(__name__)


class CoroutineWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], Awaitable[object]]) -> None:
        super().__init__()
        self.operation = operation

    def run(self) -> None:
        try:
            result = asyncio.run(self.operation())
        except Exception as exc:  # The UI boundary intentionally converts errors to short text.
            log_exception(logger, "coroutine-worker", exc)
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


class ExtractionWorker(QThread):
    progress = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        extractor: ArchiveExtractor,
        archive: Path,
        destination: Path,
    ) -> None:
        super().__init__()
        self.extractor = extractor
        self.archive = archive
        self.destination = destination

    def run(self) -> None:
        tracker = ProgressTracker()

        def report(extracted: int, total: int) -> None:
            self.progress.emit(tracker.update(extracted, total))

        try:
            result = self.extractor.extract(
                self.archive,
                self.destination,
                progress=report,
            )
        except Exception as exc:
            log_exception(logger, "archive-extraction", exc)
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


async def fetch_image(url: str) -> bytes:
    """Fetch a small preview image without blocking the Qt event loop."""
    images = await fetch_images([url])
    try:
        return images[url]
    except KeyError as exc:
        raise ValueError("Görsel yüklenemedi.") from exc


async def fetch_images(urls: list[str]) -> dict[str, bytes]:
    """Fetch preview images concurrently while keeping network use bounded."""
    unique_urls = list(dict.fromkeys(urls))
    if not unique_urls:
        return {}
    semaphore = asyncio.Semaphore(4)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(10.0),
        headers={"User-Agent": "AuthorizedGameDownloader/0.1"},
    ) as client:
        async def fetch_one(url: str) -> tuple[str, bytes | None]:
            try:
                async with semaphore:
                    response = await client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").casefold()
                if content_type and not content_type.startswith("image/"):
                    return url, None
                if len(response.content) > 10 * 1024 * 1024:
                    return url, None
                return url, response.content
            except (httpx.HTTPError, OSError):
                return url, None

        fetched = await asyncio.gather(*(fetch_one(url) for url in unique_urls))
    return {url: data for url, data in fetched if data is not None}


class DownloadWorker(QThread):
    progress = Signal(object)
    notice = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, resolved: ResolvedDownload, destination: Path) -> None:
        super().__init__()
        self.resolved = resolved
        self.destination = destination
        self.manager = DownloadManager()
        self.loop: asyncio.AbstractEventLoop | None = None

    def run(self) -> None:
        async def execute() -> Path:
            self.loop = asyncio.get_running_loop()

            def progress_callback(value: DownloadProgress) -> None:
                self.progress.emit(value)

            return await self.manager.download(
                self.resolved,
                self.destination,
                progress=progress_callback,
                notice=self.notice.emit,
            )

        try:
            result = asyncio.run(execute())
        except Exception as exc:
            log_exception(logger, "download-worker", exc)
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.loop = None

    def pause_download(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self.manager.pause)

    def resume_download(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self.manager.resume)

    def cancel_download(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self.manager.cancel)


class BrowserDirectWorker(QThread):
    progress = Signal(object)
    notice = Signal(str)
    succeeded = Signal(object)
    cancelled = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        downloader: BrowserDirectDownloader,
        source: BrowserDirectSource,
        destination: Path,
    ) -> None:
        super().__init__()
        self.downloader = downloader
        self.source = source
        self.destination = destination
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[Path] | None = None

    def run(self) -> None:
        async def execute() -> Path:
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.create_task(
                self.downloader.download(
                    self.source, self.destination,
                    progress=self.progress.emit, notice=self.notice.emit,
                )
            )
            return await self.task
        try:
            result = asyncio.run(execute())
        except asyncio.CancelledError:
            self.cancelled.emit("İndirme hemen iptal edildi; yarım dosya korundu.")
        except DownloadCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            log_exception(logger, "browser-direct-download", exc)
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.task = None
            self.loop = None

    def pause_download(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self.downloader.pause)

    def resume_download(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self.downloader.resume)

    def cancel_download(self) -> None:
        if self.loop:
            def cancel_now() -> None:
                self.downloader.cancel()
                if self.task is not None and not self.task.done():
                    self.task.cancel()

            self.loop.call_soon_threadsafe(cancel_now)

    def set_speed_limit(self, max_bytes_per_second: int | None) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(
                lambda: self.downloader.set_speed_limit(max_bytes_per_second)
            )
