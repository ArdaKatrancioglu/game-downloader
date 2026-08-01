from pathlib import Path
from uuid import UUID

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
async def test_parts_use_one_cookie_session_and_download_strictly_in_order(
    tmp_path: Path,
):
    events: list[str] = []
    request_ids: list[str] = []
    payloads = {1: b"first-part", 2: b"second-part"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fuckingfast.co" and request.method == "GET":
            request_ids.append(request.headers["x-request-id"])
            UUID(request.headers["x-request-id"])
            number = int(request.url.path.removeprefix("/file"))
            events.append(f"page-{number}")
            assert request.headers["user-agent"] == "AuthorizedGameDownloader/0.1"
            assert request.headers["accept"] == "*/*"
            assert request.headers["cache-control"] == "no-cache"
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
            request_ids.append(request.headers["x-request-id"])
            UUID(request.headers["x-request-id"])
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
    progress_updates = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        paths = await FuckingFastDownloader(
            client=client,
            resolve_hosts=False,
            part_delay_min_seconds=0,
            part_delay_max_seconds=0,
        ).download(
            FuckingFastSource(parts=[part(1), part(2)]),
            tmp_path,
            notice=notices.append,
            progress=progress_updates.append,
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
    assert len(request_ids) == len(set(request_ids)) == 4
    assert any("Part 1/2 tamamlandı" in notice for notice in notices)
    assert not any("bekleniyor" in notice for notice in notices)
    assert [item.part_index for item in progress_updates] == [1, 2]
    assert progress_updates[0].part.percent == 100
    assert progress_updates[0].total_percent == 50
    assert progress_updates[1].total_percent == 100


def test_invalid_part_delay_range_is_rejected():
    with pytest.raises(ValueError, match="negatif"):
        FuckingFastDownloader(part_delay_min_seconds=-1)
    with pytest.raises(ValueError, match="minimum"):
        FuckingFastDownloader(part_delay_min_seconds=30, part_delay_max_seconds=15)


def test_speed_limit_update_is_forwarded_to_active_manager():
    downloader = FuckingFastDownloader(max_bytes_per_second=None)

    class ManagerStub:
        def set_speed_limit(self, value):
            self.value = value

    manager = ManagerStub()
    downloader._manager = manager

    downloader.set_speed_limit(250_000)
    assert downloader.max_bytes_per_second == 250_000
    assert manager.value == 250_000

    downloader.set_speed_limit(None)
    assert manager.value is None


@pytest.mark.asyncio
async def test_cancellation_can_remove_completed_parts(tmp_path: Path):
    downloader: FuckingFastDownloader

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
            content=b"first-part",
            headers={
                "Content-Disposition": 'attachment; filename="Demo--_.part001.rar"',
                "Content-Length": "10",
            },
            request=request,
        )

    def cancel_after_first_progress(_value) -> None:
        downloader.cancel(delete_completed=True)

    downloader = FuckingFastDownloader(
        resolve_hosts=False,
        part_delay_min_seconds=0,
        part_delay_max_seconds=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader._client = client
        with pytest.raises(Exception, match="1 tamamlanmış part silindi"):
            await downloader.download(
                FuckingFastSource(parts=[part(1), part(2)]),
                tmp_path,
                progress=cancel_after_first_progress,
            )

    assert not (tmp_path / "Demo--_.part001.rar").exists()


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
