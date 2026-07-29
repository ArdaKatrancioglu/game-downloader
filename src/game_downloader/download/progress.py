from __future__ import annotations

from collections import deque
from time import monotonic

from game_downloader.models import DownloadProgress


class ProgressTracker:
    def __init__(self, initial_bytes: int = 0, window_seconds: float = 5.0) -> None:
        self.initial_bytes = initial_bytes
        self.window_seconds = window_seconds
        self.started = monotonic()
        self.samples: deque[tuple[float, int]] = deque([(self.started, initial_bytes)])

    def update(self, downloaded: int, total: int | None) -> DownloadProgress:
        now = monotonic()
        self.samples.append((now, downloaded))
        cutoff = now - self.window_seconds
        while len(self.samples) > 2 and self.samples[0][0] < cutoff:
            self.samples.popleft()
        first_time, first_bytes = self.samples[0]
        elapsed = max(now - first_time, 0.001)
        speed = max(0.0, (downloaded - first_bytes) / elapsed)
        percent = None if not total else min(100.0, downloaded * 100 / total)
        remaining = None if total is None else max(0, total - downloaded)
        eta = None if remaining is None or speed <= 0 else remaining / speed
        return DownloadProgress(
            downloaded=downloaded,
            total=total,
            percent=percent,
            bytes_per_second=speed,
            eta_seconds=eta,
        )

