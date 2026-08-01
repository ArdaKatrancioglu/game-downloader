from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from game_downloader.download.manager import DownloadCancelled, DownloadManager
from game_downloader.models import DownloadProgress, FuckingFastSource, ResolvedDownload
from game_downloader.storage.fuckingfast_download import FuckingFastDownloader


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
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


class FuckingFastWorker(QThread):
    progress = Signal(object)
    notice = Signal(str)
    succeeded = Signal(object)
    cancelled = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        downloader: FuckingFastDownloader,
        source: FuckingFastSource,
        destination: Path,
    ) -> None:
        super().__init__()
        self.downloader = downloader
        self.source = source
        self.destination = destination
        self.loop: asyncio.AbstractEventLoop | None = None

    def run(self) -> None:
        async def execute() -> list[Path]:
            self.loop = asyncio.get_running_loop()
            return await self.downloader.download(
                self.source,
                self.destination,
                progress=self.progress.emit,
                notice=self.notice.emit,
            )

        try:
            result = asyncio.run(execute())
        except DownloadCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        finally:
            self.loop = None

    def pause_download(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self.downloader.pause)

    def resume_download(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self.downloader.resume)

    def cancel_download(self, *, delete_completed: bool) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(
                lambda: self.downloader.cancel(delete_completed=delete_completed),
            )

    def set_speed_limit(self, max_bytes_per_second: int | None) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(
                lambda: self.downloader.set_speed_limit(max_bytes_per_second),
            )


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
