from typing import Protocol

from game_downloader.models import ResolvedDownload


class StorageProvider(Protocol):
    async def resolve(self, content_id: str) -> ResolvedDownload: ...
