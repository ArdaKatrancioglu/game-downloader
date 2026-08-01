import httpx
import pytest

from game_downloader.web_search import InternetSearchProvider, _parse_release_heading


@pytest.mark.asyncio
async def test_web_search_uses_s_query_and_entry_header_contract():
    requests: list[httpx.Request] = []
    search_html = """
    <html><body>
      <header class="entry-header">
        <div class="entry-meta">Published</div>
        <h1 class="entry-title">
          <a href="/demo-game/" rel="bookmark">Demo Game</a>
        </h1>
      </header>
      <header class="entry-header">
        <h1 class="entry-title"><a href="/missing-meta/">Ignored</a></h1>
      </header>
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

    assert requests[0].url.path == "/"
    assert requests[0].url.query == b"s=Demo+Game"
    assert [(result.title, str(result.detail_url)) for result in results] == [
        ("Demo Game", "https://catalog.example/demo-game/")
    ]


@pytest.mark.asyncio
async def test_selected_result_extracts_deduplicates_and_sorts_fuckingfast_parts():
    search_html = """
    <header class="entry-header">
      <div class="entry-meta">Published</div>
      <h1 class="entry-title"><a href="/demo/">Demo</a></h1>
    </header>
    """
    detail_html = """
    <a href="https://example.invalid/not-a-part">Ignore</a>
    <a href="https://fuckingfast.co/file002#Demo--_.part002.rar">Part 2</a>
    <a href="https://fuckingfast.co/file001#Demo--_.part001.rar">Part 1</a>
    <a href="https://fuckingfast.co/file001#Demo--_.part001.rar">Duplicate</a>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        html = search_html if request.url.path == "/" else detail_html
        return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = InternetSearchProvider(
            "https://catalog.example/",
            ["catalog.example"],
            client=client,
        )
        results = await provider.search("Demo")
        release = await provider.get_release(results[0].id)

    assert release.source.type == "fuckingfast"
    assert [part.part_number for part in release.source.parts] == [1, 2]
    assert [part.filename for part in release.source.parts] == [
        "Demo--_.part001.rar",
        "Demo--_.part002.rar",
    ]


@pytest.mark.asyncio
async def test_web_search_skips_external_result_links():
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


def test_detail_parser_rejects_links_without_required_filename_fragment():
    with pytest.raises(ValueError, match="FuckingFast part"):
        InternetSearchProvider._find_fuckingfast_parts(
            '<a href="https://fuckingfast.co/file001">Missing fragment</a>'
        )


def test_detail_parser_keeps_all_archive_sets_and_only_turkish_optional_file():
    names = [
        "content.part01.rar",
        "fg-tll.part01.rar",
        "fg-tll.part02.rar",
        "fg-u4.part01.rar",
        "fg-u4.part02.rar",
        "fg-optional-french.bin",
        "fg-optional-turkish.bin",
        "fg-u4-optional-russian.bin",
        "fg-u4-optional-turkish.bin",
    ]
    html = "".join(
        f'<a href="https://fuckingfast.co/file{index:03d}#{name}">{name}</a>'
        for index, name in enumerate(names, start=1)
    )

    parts = InternetSearchProvider._find_fuckingfast_parts(html)

    assert [part.filename for part in parts] == [
        "content.part01.rar",
        "fg-tll.part01.rar",
        "fg-tll.part02.rar",
        "fg-u4.part01.rar",
        "fg-u4.part02.rar",
        "fg-optional-turkish.bin",
        "fg-u4-optional-turkish.bin",
    ]


def test_release_heading_uses_en_dash_as_title_version_separator():
    assert _parse_release_heading("The Last Light – V1.2.3 + Bonus") == (
        "The Last Light",
        "v1.2.3",
    )
    assert _parse_release_heading("Title without version") == (
        "Title without version",
        "Unknown",
    )
    assert _parse_release_heading(
        "Demo Game – Deluxe Edition, v1.0.21.23831 + 4 DLCs/Bonuses"
    ) == ("Demo Game", "v1.0.21.23831")


def test_release_heading_keeps_only_first_version_before_slash():
    assert _parse_release_heading(
        "Marvel’s Spider-Man 2: Digital Deluxe Edition, "
        "v1.130.1.0/v1.131.0.0 + 2 DLCs + Unlocker + Bonus Soundtrack"
    ) == (
        "Marvel’s Spider-Man 2: Digital Deluxe Edition",
        "v1.130.1.0",
    )
    assert _parse_release_heading(
        "ELDEN RING: Shadow of the Erdtree Deluxe Edition, "
        "v1.12/v1.12.1 + 9 DLCs/Bonuses + Windows 7 Fix"
    ) == (
        "ELDEN RING: Shadow of the Erdtree Deluxe Edition",
        "v1.12",
    )
