from __future__ import annotations

from game_downloader.catalog.fallback_provider import FallbackCatalogProvider
from game_downloader.models import GameEntry, GameRelease


class FakeProvider:
    def __init__(self, title: str | None) -> None:
        self.title = title
        self.search_calls = 0

    async def search(self, query: str) -> list[GameEntry]:
        self.search_calls += 1
        if self.title is None:
            return []
        return [GameEntry(id=self.title.lower(), title=self.title)]

    async def get_release(self, game_id: str) -> GameRelease:
        return GameRelease(
            id=game_id,
            title=self.title or "Fallback",
            source={"type": "gofile", "content_id": "demo123"},
        )


async def test_fallback_is_not_searched_when_local_catalog_matches():
    local = FakeProvider("Local")
    web = FakeProvider("Web")
    provider = FallbackCatalogProvider(local, web)

    results = await provider.search("game")

    assert [item.title for item in results] == ["Local"]
    assert web.search_calls == 0
    assert not provider.used_fallback
    assert (await provider.get_release(results[0].id)).title == "Local"


async def test_web_is_searched_when_local_catalog_has_no_match():
    local = FakeProvider(None)
    web = FakeProvider("Web")
    provider = FallbackCatalogProvider(local, web)

    results = await provider.search("game")

    assert [item.title for item in results] == ["Web"]
    assert provider.used_fallback
    assert (await provider.get_release(results[0].id)).title == "Web"
