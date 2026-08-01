from __future__ import annotations

import logging

from game_downloader.catalog.base import CatalogProvider
from game_downloader.models import GameEntry, GameRelease

logger = logging.getLogger(__name__)


class FallbackCatalogProvider:
    """Search the local catalog first and use the existing web search if empty."""

    def __init__(
        self,
        primary: CatalogProvider,
        fallback: CatalogProvider | None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self._selected_provider: CatalogProvider | None = None
        self.used_fallback = False

    async def search(self, query: str) -> list[GameEntry]:
        self._selected_provider = None
        self.used_fallback = False
        try:
            results = await self.primary.search(query)
        except ValueError as exc:
            logger.warning("Local catalog search failed; using fallback: %s", exc)
            results = []
        if results:
            self._selected_provider = self.primary
            return results
        if self.fallback is None:
            return []
        self.used_fallback = True
        results = await self.fallback.search(query)
        if results:
            self._selected_provider = self.fallback
        return results

    async def get_release(self, game_id: str) -> GameRelease:
        if self._selected_provider is None:
            raise LookupError("Önce katalogda bir oyun arayın.")
        return await self._selected_provider.get_release(game_id)
