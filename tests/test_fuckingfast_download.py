from pathlib import Path

import httpx
import pytest

from game_downloader.models import FuckingFastPart, FuckingFastSource
from game_downloader.storage.fuckingfast_download import (
    FuckingFastDownloader,
    FuckingFastDownloadError,
)


def part(number: int) -> FuckingFastPart:
    filename = f"Demo--_.part{number:03d}.rar"
    return FuckingFastPart(
        page_url=f"https://fuckingfast.co/file{number:03d}#{filename}",
        filename=filename,
        part_number=number,
    )


@pytest.mark.asyncio
async def test_parts_use_one_cookie_session_and_download_strictly_in_order(tmp_path: Path):
    events: list[str] = []
    payloads = {1: b"first-part", 2: b"second-part"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fuckingfast.co" and request.method == "GET":
            number = int(request.url.path.removeprefix("/file"))
            events.append(f"page-{number}")
            if number == 2:
                assert request.headers["cookie"] == "session=verified"
            return httpx.Response(
                200,
                text=f'<button hx-post="/f/public{number:03d}/go">Download</button>',
                headers={
                    "Set-Cookie": "session=verified; Domain=.fuckingfast.co; Path=/"
                },
                request=request,
            )
        if request.url.host == "fuckingfast.co" and request.method == "POST":
            number = int(request.url.path.split("/")[2].removeprefix("public"))
            events.append(f"go-{number}")
            assert request.content == b""
            assert request.headers["hx-request"] == "true"
            assert request.headers["cookie"] == "session=verified"
            return httpx.Response(
                200,
                headers={"HX-Redirect": f"https://dl.fuckingfast.co/dl/token{number}"},
                request=request,
            )
        number = int(request.url.path.removeprefix("/dl/token"))
        events.append(f"download-{number}")
        assert request.headers["cookie"] == "session=verified"
        assert request.headers["referer"] == f"https://fuckingfast.co/file{number:03d}"
        payload = payloads[number]
        return httpx.Response(
            200,
            content=payload,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="Demo--_.part{number:03d}.rar"'
                ),
                "Content-Length": str(len(payload)),
                "ETag": f'"part-{number}"',
            },
            request=request,
        )

    notices: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        paths = await FuckingFastDownloader(
            client=client,
            resolve_hosts=False,
        ).download(
            FuckingFastSource(parts=[part(1), part(2)]),
            tmp_path,
            notice=notices.append,
        )

    assert events == [
        "page-1",
        "go-1",
        "download-1",
        "page-2",
        "go-2",
        "download-2",
    ]
    assert [path.read_bytes() for path in paths] == [b"first-part", b"second-part"]
    assert any("Part 1/2 tamamlandı" in notice for notice in notices)


@pytest.mark.asyncio
async def test_missing_hx_redirect_stops_before_file_download(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                text='<button hx-post="/f/public001/go">Download</button>',
                request=request,
            )
        return httpx.Response(200, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FuckingFastDownloadError, match="HX-Redirect"):
            await FuckingFastDownloader(
                client=client,
                resolve_hosts=False,
            ).download(FuckingFastSource(parts=[part(1)]), tmp_path)


@pytest.mark.asyncio
async def test_non_attachment_download_is_rejected_and_part_file_is_kept(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fuckingfast.co" and request.method == "GET":
            return httpx.Response(
                200,
                text='<button hx-post="/f/public001/go">Download</button>',
                request=request,
            )
        if request.url.host == "fuckingfast.co":
            return httpx.Response(
                200,
                headers={"HX-Redirect": "https://dl.fuckingfast.co/dl/token1"},
                request=request,
            )
        return httpx.Response(
            200,
            content=b"not-an-attachment",
            headers={"Content-Type": "text/html"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception, match="attachment"):
            await FuckingFastDownloader(
                client=client,
                resolve_hosts=False,
            ).download(FuckingFastSource(parts=[part(1)]), tmp_path)

    assert not (tmp_path / "Demo--_.part001.rar").exists()


@pytest.mark.asyncio
async def test_content_length_mismatch_is_rejected(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fuckingfast.co" and request.method == "GET":
            return httpx.Response(
                200,
                text='<button hx-post="/f/public001/go">Download</button>',
                request=request,
            )
        if request.url.host == "fuckingfast.co":
            return httpx.Response(
                200,
                headers={"HX-Redirect": "https://dl.fuckingfast.co/dl/token1"},
                request=request,
            )
        return httpx.Response(
            200,
            content=b"short",
            headers={
                "Content-Disposition": "attachment",
                "Content-Length": "6",
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception, match="Size validation"):
            await FuckingFastDownloader(
                client=client,
                resolve_hosts=False,
            ).download(FuckingFastSource(parts=[part(1)]), tmp_path)

    assert (tmp_path / "Demo--_.part001.rar.part").read_bytes() == b"short"
