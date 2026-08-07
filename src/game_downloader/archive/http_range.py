from __future__ import annotations

import io
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import httpx

from game_downloader.security import SecurityError, normalized_host

logger = logging.getLogger(__name__)


class RangeDownloadError(RuntimeError):
    pass


class RangeNotSupported(RangeDownloadError):
    pass


class RangeDownloadCancelled(RangeDownloadError):
    pass


RangeProgressCallback = Callable[[int, int], None]
_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$", re.IGNORECASE)


class RangeTransferControl:
    """Thread-safe pause, cancellation, and speed controls for synchronous Range I/O."""

    def __init__(self, max_bytes_per_second: int | None = None) -> None:
        self._pause = threading.Event()
        self._pause.set()
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._max_bytes_per_second = max_bytes_per_second

    def reset(self) -> None:
        self._cancel.clear()
        self._pause.set()

    def pause(self) -> None:
        self._pause.clear()

    def resume(self) -> None:
        self._pause.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.set()

    def set_speed_limit(self, value: int | None) -> None:
        with self._lock:
            self._max_bytes_per_second = value

    def checkpoint(self) -> None:
        if self._cancel.is_set():
            raise RangeDownloadCancelled("İndirme ve arşiv çıkarma iptal edildi.")
        while not self._pause.wait(0.1):
            if self._cancel.is_set():
                raise RangeDownloadCancelled("İndirme ve arşiv çıkarma iptal edildi.")

    def throttle(self, byte_count: int, started_at: float) -> None:
        with self._lock:
            limit = self._max_bytes_per_second
        if not limit:
            return
        target_elapsed = byte_count / limit
        while (remaining := target_elapsed - (time.monotonic() - started_at)) > 0:
            self.checkpoint()
            time.sleep(min(remaining, 0.1))


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only file backed by aligned HTTPS Range requests."""

    def __init__(
        self,
        url: str,
        *,
        referer: str | None = None,
        require_attachment: bool = False,
        block_size: int = 8 * 1024 * 1024,
        cache_blocks: int = 4,
        prefetch: bool = True,
        client: httpx.Client | None = None,
        control: RangeTransferControl | None = None,
        progress: RangeProgressCallback | None = None,
        max_redirects: int = 3,
    ) -> None:
        super().__init__()
        if block_size <= 0 or cache_blocks <= 0:
            raise ValueError("block_size and cache_blocks must be positive")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise SecurityError("The provider-issued download URL must use HTTPS.")
        self.url = url
        self._initial_host = normalized_host(parsed.hostname)
        self._referer = referer
        self._require_attachment = require_attachment
        self.block_size = block_size
        self.cache_blocks = cache_blocks
        self.prefetch = prefetch
        self._client = client or httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=15.0),
        )
        self._owns_client = client is None
        self._control = control or RangeTransferControl()
        self._progress = progress
        self._max_redirects = max_redirects
        self._position = 0
        self._size = 0
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._fetched_blocks: set[int] = set()
        self._downloaded = 0
        self._cache_lock = threading.RLock()
        self._closing = threading.Event()
        self._prefetch_executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="zip-range-prefetch")
            if prefetch
            else None
        )
        self._prefetch_future: Future[bytes] | None = None
        self._prefetch_index: int | None = None
        try:
            self._probe()
        except RangeNotSupported as exc:
            logger.warning(
                "HTTP Range probe completed result=unsupported host=%s detail=%s",
                self._initial_host,
                exc,
            )
            if self._owns_client:
                self._client.close()
            super().close()
            raise
        except RangeDownloadError as exc:
            logger.warning(
                "HTTP Range probe completed result=failed host=%s error_type=%s detail=%s",
                self._initial_host,
                type(exc).__name__,
                exc,
            )
            if self._owns_client:
                self._client.close()
            super().close()
            raise
        logger.info(
            "HTTP Range probe completed result=supported host=%s archive_size=%d "
            "block_size=%d cache_blocks=%d prefetch=%s",
            self._initial_host,
            self._size,
            self.block_size,
            self.cache_blocks,
            self.prefetch,
        )

    @property
    def size(self) -> int:
        return self._size

    @property
    def downloaded(self) -> int:
        return self._downloaded

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        self._check_closed()
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self._check_closed()
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._position + offset
        elif whence == io.SEEK_END:
            position = self._size + offset
        else:
            raise ValueError("invalid whence")
        if position < 0:
            raise ValueError("negative seek position")
        self._position = position
        return position

    def read(self, size: int = -1) -> bytes:
        self._check_closed()
        self._control.checkpoint()
        if self._position >= self._size:
            return b""
        if size is None or size < 0:
            size = self._size - self._position
        remaining = min(size, self._size - self._position)
        output = bytearray()
        while remaining:
            self._control.checkpoint()
            block_index = self._position // self.block_size
            offset = self._position % self.block_size
            block = self._get_block(block_index)
            count = min(remaining, len(block) - offset)
            if count <= 0:
                raise RangeDownloadError("The Range response ended unexpectedly.")
            output.extend(block[offset : offset + count])
            self._position += count
            remaining -= count
        return bytes(output)

    def readinto(self, buffer: bytearray | memoryview) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def close(self) -> None:
        if not self.closed:
            self._closing.set()
            if self._prefetch_executor:
                self._prefetch_executor.shutdown(wait=True, cancel_futures=True)
            if self._owns_client:
                self._client.close()
        super().close()

    def _probe(self) -> None:
        response = self._open_range(0, 0)
        try:
            if response.status_code == 200:
                raise RangeNotSupported("Sunucu HTTP Range isteklerini desteklemiyor.")
            if response.status_code != 206:
                raise RangeDownloadError(
                    f"Range denetimi HTTP {response.status_code} döndürdü."
                )
            if self._require_attachment:
                disposition = response.headers.get("content-disposition", "")
                if not disposition.casefold().startswith("attachment"):
                    raise RangeDownloadError(
                        "The download server did not return an attachment response."
                    )
            start, end, total = self._parse_content_range(response)
            if (start, end) != (0, 0) or total <= 0:
                raise RangeDownloadError("Sunucu geçersiz bir Content-Range döndürdü.")
            encoding = response.headers.get("content-encoding", "").casefold()
            if encoding and encoding != "identity":
                raise RangeNotSupported(
                    "Range yanıtı byte konumlarını değiştiren biçimde kodlandı."
                )
            self._size = total
        finally:
            response.close()

    def _get_block(self, block_index: int) -> bytes:
        cached = self._cached_block(block_index)
        if cached is not None:
            self._schedule_prefetch(block_index + 1)
            return cached
        value = self._take_prefetched(block_index)
        if value is None:
            value = self._fetch_block(block_index)
        self._store_block(block_index, value)
        self._schedule_prefetch(block_index + 1)
        return value

    def _fetch_block(self, block_index: int) -> bytes:
        start = block_index * self.block_size
        end = min(start + self.block_size, self._size) - 1
        response = self._open_range(start, end)
        try:
            if response.status_code != 206:
                raise RangeNotSupported(
                    f"Sunucu byte aralığını döndürmedi (HTTP {response.status_code})."
                )
            actual_start, actual_end, total = self._parse_content_range(response)
            if (actual_start, actual_end, total) != (start, end, self._size):
                raise RangeDownloadError("Sunucu istenenden farklı bir byte aralığı döndürdü.")
            started_at = time.monotonic()
            data = bytearray()
            chunks = (
                [response.content]
                if response.is_stream_consumed
                else response.iter_raw(64 * 1024)
            )
            for chunk in chunks:
                self._network_checkpoint()
                data.extend(chunk)
                self._control.throttle(len(data), started_at)
            expected = end - start + 1
            if len(data) != expected:
                raise RangeDownloadError(
                    f"Eksik Range yanıtı: {expected} byte beklendi, {len(data)} alındı."
                )
        except httpx.HTTPError as exc:
            raise RangeDownloadError("Range yanıtı okunurken bağlantı kesildi.") from exc
        finally:
            response.close()
        return bytes(data)

    def _cached_block(self, block_index: int) -> bytes | None:
        with self._cache_lock:
            value = self._cache.get(block_index)
            if value is not None:
                self._cache.move_to_end(block_index)
            return value

    def _store_block(self, block_index: int, value: bytes) -> None:
        report: tuple[int, int] | None = None
        with self._cache_lock:
            self._cache[block_index] = value
            self._cache.move_to_end(block_index)
            while len(self._cache) > self.cache_blocks:
                self._cache.popitem(last=False)
            if block_index not in self._fetched_blocks:
                self._fetched_blocks.add(block_index)
                self._downloaded += len(value)
                report = min(self._downloaded, self._size), self._size
        if report and self._progress:
            self._progress(*report)

    def _take_prefetched(self, block_index: int) -> bytes | None:
        future = self._prefetch_future
        if future is None or self._prefetch_index != block_index:
            return None
        try:
            return future.result()
        finally:
            self._prefetch_future = None
            self._prefetch_index = None

    def _schedule_prefetch(self, block_index: int) -> None:
        executor = self._prefetch_executor
        if executor is None or block_index * self.block_size >= self._size:
            return
        if self._cached_block(block_index) is not None:
            return
        future = self._prefetch_future
        if future is not None:
            if not future.done():
                return
            completed_index = self._prefetch_index
            try:
                completed = future.result()
            except RangeDownloadCancelled:
                completed = None
            if completed_index is not None and completed is not None:
                self._store_block(completed_index, completed)
            self._prefetch_future = None
            self._prefetch_index = None
        if not self._closing.is_set():
            self._prefetch_index = block_index
            self._prefetch_future = executor.submit(self._fetch_block, block_index)

    def _network_checkpoint(self) -> None:
        if self._closing.is_set():
            raise RangeDownloadCancelled("Range prefetch kapatıldı.")
        self._control.checkpoint()

    def _open_range(self, start: int, end: int) -> httpx.Response:
        current = self.url
        headers = {
            "Range": f"bytes={start}-{end}",
            "Accept-Encoding": "identity",
        }
        if self._referer:
            headers["Referer"] = self._referer
        for redirect_count in range(self._max_redirects + 1):
            self._control.checkpoint()
            request = self._client.build_request("GET", current, headers=headers)
            try:
                response = self._client.send(request, stream=True)
            except httpx.HTTPError as exc:
                raise RangeDownloadError("Range isteği gönderilemedi.") from exc
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            response.close()
            if not location:
                raise RangeDownloadError("Sunucu geçersiz bir yönlendirme döndürdü.")
            if redirect_count >= self._max_redirects:
                raise RangeDownloadError("İndirme yönlendirme sınırını aştı.")
            current = urljoin(current, location)
            parsed = urlsplit(current)
            if parsed.scheme != "https" or normalized_host(parsed.hostname) != self._initial_host:
                raise SecurityError("Range isteği beklenmeyen bir domaine yönlendirildi.")
        raise AssertionError("unreachable")

    @staticmethod
    def _parse_content_range(response: httpx.Response) -> tuple[int, int, int]:
        match = _CONTENT_RANGE_RE.match(response.headers.get("content-range", ""))
        if not match:
            raise RangeDownloadError("Sunucu geçerli bir Content-Range döndürmedi.")
        return tuple(int(value) for value in match.groups())  # type: ignore[return-value]

    def _check_closed(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file")
