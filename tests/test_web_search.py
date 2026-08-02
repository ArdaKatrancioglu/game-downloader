import httpx
import pytest

from game_downloader.web_search import InternetSearchProvider


@pytest.mark.asyncio
async def test_web_search_uses_path_and_listing_json_contract():
    requests: list[httpx.Request] = []
    search_html = """
    <html><body>
      <article listing="{&quot;id&quot;:&quot;42&quot;,&quot;title&quot;:&quot;Demo Game&quot;,
        &quot;slug&quot;:&quot;demo-game&quot;,
        &quot;imageurl&quot;:&quot;https://catalog.example/demo.jpg&quot;,
        &quot;coverurl&quot;:&quot;https://catalog.example/cover.jpg&quot;,
        &quot;size_gb&quot;:1.5,&quot;release_date&quot;:&quot;2026-01-02&quot;,
        &quot;vote_average&quot;:&quot;v1.2.3&quot;,
        &quot;genres&quot;:[{&quot;id&quot;:1,&quot;name&quot;:&quot;Action&quot;}],
        &quot;downloads&quot;:[{&quot;id&quot;:4584,&quot;name&quot;:&quot;demo.zip&quot;}]}"></article>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=search_html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = InternetSearchProvider(
            "https://catalog.example",
            ["catalog.example"],
            client=client,
        )
        results = await provider.search("Demo Game")

    assert requests[0].url.path == "/search/Demo Game"
    assert requests[0].url.raw_path == b"/search/Demo%20Game"
    assert requests[0].url.query == b""
    assert [(result.title, str(result.detail_url)) for result in results] == [
        ("Demo Game", "https://catalog.example/game/demo-game")
    ]
    assert results[0].id == "42"
    assert results[0].archive_size == round(1.5 * 1024**3)
    assert results[0].version == "v1.2.3"
    assert str(results[0].image_url) == "https://catalog.example/demo.jpg"
    assert str(results[0].cover_url) == "https://catalog.example/cover.jpg"
    assert results[0].genres == [{"id": 1, "name": "Action"}]
    assert results[0].source.downloads[0].id == "4584"


@pytest.mark.asyncio
async def test_search_returns_all_valid_listings_and_skips_invalid(caplog):
    search_html = """
    <div listing='{"id":1,"title":"One","slug":"one","runtime":"20 GB"}'></div>
    <div listing='not-json'></div>
    <div></div>
    <div listing='{"id":2,"title":"Two","slug":"two","runtime":"512 MB"}'></div>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=search_html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = InternetSearchProvider(
            "https://catalog.example/",
            ["catalog.example"],
            client=client,
        )
        with caplog.at_level("WARNING"):
            results = await provider.search("Demo")

    assert [result.title for result in results] == ["One", "Two"]
    assert [result.archive_size for result in results] == [20 * 1024**3, 512 * 1024**2]
    assert "Skipping invalid listing metadata" in caplog.text


@pytest.mark.asyncio
async def test_release_without_embedded_downloads_stays_browser_direct():
    requests = []
    html = '<div listing=\'{"id":1,"title":"One","slug":"one"}\'></div>'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = InternetSearchProvider(
            "https://catalog.example/", ["catalog.example"], client=client
        )
        results = await provider.search("One")
        release = await provider.get_release(results[0].id)

    assert release.source.type == "browser_direct"
    assert release.source.downloads == []
    assert str(release.source.page_url) == "https://catalog.example/game/one"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_old_entry_header_search_markup_is_not_parsed():
    html = """
    <header class="entry-header">
      <div class="entry-meta">Published</div>
      <h1 class="entry-title">
        <a href="https://evil.example/demo/">Demo</a>
      </h1>
    </header>
    """
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=html, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        provider = InternetSearchProvider(
            "https://catalog.example/",
            ["catalog.example"],
            client=client,
        )
        assert await provider.search("Demo") == []
