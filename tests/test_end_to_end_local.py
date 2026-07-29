import io
import zipfile
from pathlib import Path

import httpx
import pytest

from game_downloader.archive.extractor import ArchiveExtractor
from game_downloader.catalog.owned_html_provider import OwnedHtmlCatalogProvider
from game_downloader.download.manager import DownloadManager
from game_downloader.models import ResolvedDownload

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_catalog_to_mocked_gofile_browser_capture_and_safe_extract(tmp_path):
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("demo/readme.txt", b"authorized local fixture")
    archive_bytes = archive_buffer.getvalue()

    def catalog_handler(request):
        fixture = "catalog_search.html" if request.url.path == "/" else "catalog_detail.html"
        return httpx.Response(
            200,
            text=(FIXTURES / fixture).read_text(),
            request=request,
        )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(catalog_handler)) as catalog_client,
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=archive_bytes, request=request)
            )
        ) as download_client,
    ):
        catalog = OwnedHtmlCatalogProvider(
            "https://catalog.example/",
            ["catalog.example"],
            client=catalog_client,
        )
        results = await catalog.search("Demo")
        release = await catalog.get_release(results[0].id)
        resolved = ResolvedDownload(
            source_id=release.source.content_id,
            filename="demo.zip",
            size=len(archive_bytes),
            url="https://store.gofile.io/download/demo.zip",
            referer=f"https://gofile.io/d/{release.source.content_id}",
        )
        downloaded = await DownloadManager(
            client=download_client,
            resolve_hosts=False,
        ).download(resolved, tmp_path)

    destination = tmp_path / "extracted"
    ArchiveExtractor().extract(downloaded, destination)
    assert (destination / "demo/readme.txt").read_bytes() == b"authorized local fixture"
