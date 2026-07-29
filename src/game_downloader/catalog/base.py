from typing import Protocol

from game_downloader.models import GameEntry, GameRelease


class CatalogProvider(Protocol):
    async def search(self, query: str) -> list[GameEntry]: ...

    async def get_release(self, game_id: str) -> GameRelease: ...

