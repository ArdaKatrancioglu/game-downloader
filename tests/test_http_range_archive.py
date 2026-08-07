from __future__ import annotations

import io
import logging
import threading
import zipfile

import httpx
import pytest

from game_downloader.archive.extractor import ArchiveExtractor
from game_downloader.archive.http_range import HttpRangeFile, RangeNotSupported


def _zip_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("wanted.txt", b"wanted content")
        archive.writestr("unused.bin", bytes(range(256)) * 40)
    return output.getvalue()


def _zstandard_zip_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_ZSTANDARD) as archive:
        archive.writestr("file.bin", b"payload")
    return output.getvalue()


def _range_client(payload: bytes) -> tuple[httpx.Client, list[tuple[int, int]]]:
    ranges = []

    def handler(request: httpx.Request) -> httpx.Response:
        value = request.headers["range"]
        start_text, end_text = value.removeprefix("bytes=").split("-", 1)
        start, end = int(start_text), int(end_text)
        ranges.append((start, end))
        return httpx.Response(
            206,
            content=payload[start : end + 1],
            headers={
                "Content-Range": f"bytes {start}-{end}/{len(payload)}",
                "Content-Disposition": 'attachment; filename="game.zip"',
                "Content-Encoding": "identity",
            },
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handler)), ranges


def test_zipfile_reads_one_member_without_fetching_the_whole_archive(caplog) -> None:
    payload = _zip_bytes()
    client, ranges = _range_client(payload)
    try:
        with caplog.at_level(logging.INFO), HttpRangeFile(
            "https://cdn.example/game.zip?token=secret",
            client=client,
            block_size=128,
            cache_blocks=8,
        ) as remote:
            with zipfile.ZipFile(remote) as archive:
                assert archive.read("wanted.txt") == b"wanted content"
            assert remote.downloaded < len(payload)
    finally:
        client.close()

    assert ranges[0] == (0, 0)
    assert len(ranges) > 1
    assert "result=supported" in caplog.text
    assert "archive_size=" in caplog.text
    range_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "game_downloader.archive.http_range"
    )
    assert "secret" not in range_logs


def test_remote_zip_is_extracted_without_a_local_archive(tmp_path) -> None:
    payload = _zip_bytes()
    client, _ranges = _range_client(payload)
    destination = tmp_path / "game"
    try:
        with HttpRangeFile(
            "https://cdn.example/game.zip",
            client=client,
            block_size=256,
        ) as remote:
            result = ArchiveExtractor().extract_zip_stream(
                remote,
                destination,
                archive_size=remote.size,
            )
    finally:
        client.close()

    assert result.destination == destination
    assert (destination / "wanted.txt").read_bytes() == b"wanted content"
    assert (destination / "unused.bin").read_bytes() == bytes(range(256)) * 40
    assert not (tmp_path / "game.zip").exists()


def test_server_without_range_support_uses_explicit_fallback_signal(caplog) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"not ranged", request=request)
        )
    )
    try:
        with caplog.at_level(logging.WARNING), pytest.raises(
            RangeNotSupported, match="Range"
        ):
            HttpRangeFile("https://cdn.example/game.zip", client=client)
    finally:
        client.close()
    assert "result=unsupported" in caplog.text


def test_remote_zip_logs_and_extracts_zstandard_method(caplog, tmp_path) -> None:
    payload = _zstandard_zip_bytes()
    client, _ranges = _range_client(payload)
    destination = tmp_path / "game"
    try:
        with caplog.at_level(logging.INFO), HttpRangeFile(
            "https://cdn.example/game.zip",
            client=client,
            block_size=64,
        ) as remote:
            ArchiveExtractor().extract_zip_stream(
                remote,
                destination,
                archive_size=remote.size,
            )
    finally:
        client.close()

    assert "Remote ZIP compression methods methods=93:zstandard=1" in caplog.text
    assert "Remote ZIP compression unsupported" not in caplog.text
    assert (destination / "file.bin").read_bytes() == b"payload"


def test_next_range_block_is_prefetched_in_background() -> None:
    payload = bytes(range(192))
    second_block_requested = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        start_text, end_text = request.headers["range"].removeprefix("bytes=").split("-")
        start, end = int(start_text), int(end_text)
        if start == 64:
            second_block_requested.set()
        return httpx.Response(
            206,
            content=payload[start : end + 1],
            headers={"Content-Range": f"bytes {start}-{end}/{len(payload)}"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with HttpRangeFile(
            "https://cdn.example/game.zip",
            client=client,
            block_size=64,
            cache_blocks=2,
        ) as remote:
            assert remote.read(1) == payload[:1]
            assert second_block_requested.wait(1)
    finally:
        client.close()
