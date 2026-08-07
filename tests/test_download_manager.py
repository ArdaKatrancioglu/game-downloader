import errno
import gzip
import hashlib
import socket
import ssl

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


def test_speed_limit_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        DownloadManager(max_bytes_per_second=0)


def test_speed_limit_can_be_changed_while_manager_is_alive():
    manager = DownloadManager(max_bytes_per_second=None)

    manager.set_speed_limit(125_000)
    assert manager.max_bytes_per_second == 125_000
    assert manager._rate_limiter.max_bytes_per_second == 125_000

    manager.set_speed_limit(None)
    assert manager.max_bytes_per_second is None
    assert manager._rate_limiter.max_bytes_per_second is None


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
                referer="https://files.example/download/owned123",
            ),
            tmp_path,
        )
    assert result.read_bytes() == data
    assert requests[0].headers["referer"] == "https://files.example/download/owned123"
    assert not (tmp_path / "demo.zip.part").exists()


@pytest.mark.asyncio
async def test_compressed_transfer_length_is_not_used_as_decoded_file_size(tmp_path):
    data = b"browser transition response" * 100
    compressed = gzip.compress(data)

    def handler(request):
        return httpx.Response(
            200,
            content=compressed,
            headers={
                "Content-Encoding": "gzip",
                "Content-Length": str(len(compressed)),
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DownloadManager(client=client, resolve_hosts=False).download(
            resolved(size=None),
            tmp_path,
        )

    assert result.read_bytes() == data


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
    assert delays == [3]


@pytest.mark.asyncio
async def test_tls_failure_retries_three_times_then_succeeds(tmp_path, monkeypatch):
    calls = 0
    delays = []
    notices = []

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 3:
            try:
                raise ssl.SSLError("TLS connection closed")
            except ssl.SSLError as cause:
                raise httpx.ConnectError("handshake failed", request=request) from cause
        return httpx.Response(200, content=b"abcdef", request=request)

    async def fake_sleep(value):
        delays.append(value)

    monkeypatch.setattr("game_downloader.download.manager.asyncio.sleep", fake_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DownloadManager(
            client=client,
            resolve_hosts=False,
            max_attempts=3,
        ).download(resolved(), tmp_path, notice=notices.append)

    assert result.read_bytes() == b"abcdef"
    assert calls == 3
    assert delays == [3, 5]
    assert any("(2/3)" in notice for notice in notices)
    assert any("(3/3)" in notice for notice in notices)


@pytest.mark.asyncio
async def test_tls_failure_is_shown_after_retry_threshold(tmp_path, monkeypatch):
    calls = 0
    delays = []

    def handler(request):
        nonlocal calls
        calls += 1
        raise ssl.SSLError("TLS connection closed")

    async def fake_sleep(value):
        delays.append(value)

    monkeypatch.setattr("game_downloader.download.manager.asyncio.sleep", fake_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadError, match="download was interrupted"):
            await DownloadManager(
                client=client,
                resolve_hosts=False,
                max_attempts=3,
            ).download(resolved(), tmp_path)

    assert calls == 3
    assert delays == [3, 5]


@pytest.mark.asyncio
async def test_untyped_connection_closed_by_peer_retries(tmp_path, monkeypatch):
    calls = 0
    delays = []

    class UnstableStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise RuntimeError("connection closed by peer")
            yield b""  # pragma: no cover

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(200, stream=UnstableStream(), request=request)
        return httpx.Response(200, content=b"abcdef", request=request)

    async def fake_sleep(value):
        delays.append(value)

    monkeypatch.setattr("game_downloader.download.manager.asyncio.sleep", fake_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DownloadManager(
            client=client,
            resolve_hosts=False,
            max_attempts=3,
        ).download(resolved(), tmp_path)

    assert result.read_bytes() == b"abcdef"
    assert calls == 3
    assert delays == [3, 5]


@pytest.mark.asyncio
async def test_clean_early_eof_retries_from_partial_file(tmp_path, monkeypatch):
    calls = 0
    delays = []

    class ShortStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"abc"

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=ShortStream(), request=request)
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
            max_attempts=3,
        ).download(resolved(), tmp_path)

    assert result.read_bytes() == b"abcdef"
    assert calls == 2
    assert delays == [3]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [403, 500, 502, 503])
async def test_http_error_responses_are_not_retried(tmp_path, status_code):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadError, match=f"HTTP {status_code}"):
            await DownloadManager(
                client=client,
                resolve_hosts=False,
                max_attempts=3,
            ).download(resolved(), tmp_path)

    assert calls == 1


@pytest.mark.asyncio
async def test_temporary_dns_failure_retries(tmp_path, monkeypatch):
    dns_calls = 0
    delays = []

    async def flaky_dns(_host):
        nonlocal dns_calls
        dns_calls += 1
        if dns_calls < 3:
            try:
                raise socket.gaierror("temporary DNS failure")
            except socket.gaierror as cause:
                raise SecurityError("host could not be resolved") from cause

    async def fake_sleep(value):
        delays.append(value)

    monkeypatch.setattr("game_downloader.download.manager.ensure_public_host", flaky_dns)
    monkeypatch.setattr("game_downloader.download.manager.asyncio.sleep", fake_sleep)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"abcdef", request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await DownloadManager(client=client, max_attempts=3).download(
            resolved(), tmp_path
        )

    assert result.read_bytes() == b"abcdef"
    assert dns_calls == 3
    assert delays == [3, 5]


@pytest.mark.asyncio
async def test_local_disk_error_is_not_retried(tmp_path, monkeypatch):
    calls = 0
    manager = DownloadManager(
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None)),
        resolve_hosts=False,
        max_attempts=3,
    )

    async def disk_failure(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(manager, "_single_attempt", disk_failure)
    try:
        with pytest.raises(OSError, match="disk full"):
            await manager.download(resolved(), tmp_path)
    finally:
        await manager._client.aclose()

    assert calls == 1


@pytest.mark.asyncio
async def test_programming_error_at_request_boundary_is_not_retried(tmp_path):
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        raise TypeError("bad request integration")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TypeError, match="bad request integration"):
            await DownloadManager(
                client=client,
                resolve_hosts=False,
                max_attempts=3,
            ).download(resolved(), tmp_path)

    assert calls == 1


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
                        referer="https://files.example/download/owned123",
                    ),
                    tmp_path,
                )

    assert "classification=cloudflare_challenge" in caplog.text
    assert "referer_present=True" in caplog.text
    assert "download-ray-IST" in caplog.text
