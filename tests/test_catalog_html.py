from pathlib import Path

import httpx
import pytest

from game_downloader.catalog.owned_html_provider import OwnedHtmlCatalogProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_owned_catalog_searches_then_follows_selected_page():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/":
            return httpx.Response(
                200,
                text=(FIXTURES / "catalog_search.html").read_text(),
                request=request,
            )
        return httpx.Response(
            200,
            text=(FIXTURES / "catalog_detail.html").read_text(),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OwnedHtmlCatalogProvider(
            "https://catalog.example/",
            ["catalog.example"],
            client=client,
        )
        results = await provider.search("Demo Game")
        release = await provider.get_release(results[0].id)

    assert requests[0].url.query == b"s=Demo+Game"
    assert requests[0].headers["user-agent"].startswith("AuthorizedGameDownloader/")
    assert "text/html" in requests[0].headers["accept"]
    assert requests[1].url.path == "/demo-game"
    assert release.source.content_id == "example123"


@pytest.mark.asyncio
async def test_owned_catalog_rejects_non_gofile_download_link():
    detail = '<a href="https://bzzhr.to/example123">DOWNLOAD HERE</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        fixture = (FIXTURES / "catalog_search.html").read_text()
        return httpx.Response(
            200,
            text=fixture if request.url.path == "/" else detail,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OwnedHtmlCatalogProvider(
            "https://catalog.example/",
            ["catalog.example"],
            client=client,
        )
        results = await provider.search("Demo")
        with pytest.raises(ValueError, match="GoFile"):
            await provider.get_release(results[0].id)


def test_download_parser_skips_other_hosts_and_accepts_protocol_relative_gofile():
    html = """
    <a href="https://example.invalid/not-gofile">DOWNLOAD HERE</a>
    <a href="//gofile.io/d/ud2omH"
       target="_blank"
       rel="nofollow"
       class="shortc-button medium purple ">
      DOWNLOAD HERE
    </a>
    """

    assert OwnedHtmlCatalogProvider._find_download_content_id(html) == "ud2omH"


@pytest.mark.asyncio
async def test_owned_catalog_skips_external_results():
    html = (
        '<div class="slide lazyload">'
        '<a href="https://evil.example/game">Bad</a></div>'
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OwnedHtmlCatalogProvider(
            "https://catalog.example/",
            ["catalog.example"],
            client=client,
        )
        assert await provider.search("bad") == []


@pytest.mark.asyncio
async def test_owned_catalog_uses_first_link_from_each_slide_lazyload_card():
    html = """
    <html><body>
      <a href="/outside">Outside Link</a>
      <div id="masonry">
        <div class="slide lazyload">
          <a href="wanted-game/" class="all-over-thumb-link">
            <span class="screen-reader-text">Wanted Game</span>
          </a>
          <div class="thumb-overlay">
            <a href="must-not-be-used/">Different nested link</a>
            <span class="tagmetafield">Build 20951841</span>
          </div>
        </div>
      </div>
    </body></html>
    """
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=html, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OwnedHtmlCatalogProvider(
            "https://catalog.example/",
            ["catalog.example"],
            client=client,
        )
        results = await provider.search("wanted")

    assert [result.title for result in results] == ["Wanted Game"]
    assert results[0].version == "Build 20951841"
    assert str(results[0].detail_url) == "https://catalog.example/wanted-game/"


@pytest.mark.asyncio
async def test_owned_catalog_returns_no_results_without_slide_cards(caplog):
    html = '<div class="search-results"><a href="/outside">Outside</a></div>'
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=html, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OwnedHtmlCatalogProvider(
            "https://catalog.example/",
            ["catalog.example"],
            client=client,
        )
        assert await provider.search("outside") == []

    assert "div.slide.lazyload" in caplog.text
