import hashlib

import httpx
import pytest

from game_downloader.download.manager import DownloadError, DownloadManager
from game_downloader.models import ResolvedDownload
from game_downloader.security import SecurityError


def resolved(size=6, checksum=None, referer=None):
    return ResolvedDownload(
        source_id="file123",
        filename="demo.zip",
        size=size,
        url="https://cdn.example/demo.zip",
        referer=referer,
        checksum_sha256=checksum,
    )


@pytest.mark.asyncio
async def test_streams_to_part_then_atomically_finishes(tmp_path):
    data = b"abcdef"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=data,
            headers={"Content-Length": str(len(data))},
            request=request,
        )

    transport = httpx.MockTransport(
        handler
    )
    async with httpx.AsyncClient(transport=transport) as client:
        manager = DownloadManager(client=client, resolve_hosts=False, chunk_size=2)
        result = await manager.download(
            resolved(
                checksum=hashlib.sha256(data).hexdigest(),
                referer="https://gofile.io/d/owned123",
            ),
            tmp_path,
        )
    assert result.read_bytes() == data
    assert requests[0].headers["referer"] == "https://gofile.io/d/owned123"
    assert not (tmp_path / "demo.zip.part").exists()


@pytest.mark.asyncio
async def test_resumes_with_range(tmp_path):
    (tmp_path / "demo.zip.part").write_bytes(b"abc")

    def handler(request):
        assert request.headers["range"] == "bytes=3-"
        return httpx.Response(
            206,
            content=b"def",
            headers={"Content-Range": "bytes 3-5/6"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DownloadManager(client=client, resolve_hosts=False).download(
            resolved(),
            tmp_path,
        )
    assert result.read_bytes() == b"abcdef"


@pytest.mark.asyncio
async def test_restarts_when_range_is_unsupported(tmp_path):
    (tmp_path / "demo.zip.part").write_bytes(b"abc")
    notices = []
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"abcdef", request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await DownloadManager(client=client, resolve_hosts=False).download(
            resolved(),
            tmp_path,
            notice=notices.append,
        )
    assert result.read_bytes() == b"abcdef"
    assert "restarting" in notices[0]


@pytest.mark.asyncio
async def test_rejects_cross_domain_redirect(tmp_path):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302,
            headers={"Location": "https://evil.example/file"},
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SecurityError, match="unexpected domain"):
            await DownloadManager(client=client, resolve_hosts=False).download(
                resolved(),
                tmp_path,
            )


@pytest.mark.asyncio
async def test_exact_size_and_checksum_failures_keep_part(tmp_path):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"wrong", request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DownloadError, match="Size validation"):
            await DownloadManager(client=client, resolve_hosts=False).download(
                resolved(size=6),
                tmp_path,
            )
    assert (tmp_path / "demo.zip.part").read_bytes() == b"wrong"


@pytest.mark.asyncio
async def test_interrupted_stream_retries_with_range(tmp_path, monkeypatch):
    calls = 0
    delays = []

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"abc"
            raise httpx.ReadError("connection interrupted")

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=InterruptedStream(), request=request)
        assert request.headers["range"] == "bytes=3-"
        return httpx.Response(
            206,
            content=b"def",
            headers={"Content-Range": "bytes 3-5/6"},
            request=request,
        )

    async def fake_sleep(value):
        delays.append(value)

    monkeypatch.setattr("game_downloader.download.manager.asyncio.sleep", fake_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DownloadManager(
            client=client,
            resolve_hosts=False,
            chunk_size=1,
        ).download(resolved(), tmp_path)
    assert result.read_bytes() == b"abcdef"
    assert delays == [1]


@pytest.mark.asyncio
async def test_cloudflare_download_challenge_is_logged_and_explained(tmp_path, caplog):
    html = b"<html><title>Just a moment...</title></html>"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            403,
            content=html,
            headers={
                "Content-Type": "text/html; charset=UTF-8",
                "Content-Length": str(len(html)),
                "Server": "cloudflare",
                "CF-Ray": "download-ray-IST",
            },
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with caplog.at_level("INFO"):
            with pytest.raises(DownloadError, match="Cloudflare verification"):
                await DownloadManager(client=client, resolve_hosts=False).download(
                    resolved(
                        referer="https://gofile.io/d/owned123",
                    ),
                    tmp_path,
                )

    assert "classification=cloudflare_challenge" in caplog.text
    assert "referer_present=True" in caplog.text
    assert "download-ray-IST" in caplog.text
