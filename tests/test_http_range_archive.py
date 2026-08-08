from __future__ import annotations

import io
import logging
import threading
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from game_downloader.archive.extractor import ArchiveError, ArchiveExtractor
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


def test_range_blocks_are_restored_from_persistent_cache(tmp_path) -> None:
    payload = bytes(range(192))
    cache = tmp_path / "ranges"
    first_client, first_ranges = _range_client(payload)
    try:
        with HttpRangeFile(
            "https://cdn.example/game.zip",
            client=first_client,
            block_size=64,
            prefetch=False,
            disk_cache=cache,
        ) as remote:
            assert remote.read(70) == payload[:70]
    finally:
        first_client.close()

    second_client, second_ranges = _range_client(payload)
    try:
        with HttpRangeFile(
            "https://cdn.example/new-signed-url.zip",
            client=second_client,
            block_size=64,
            prefetch=False,
            disk_cache=cache,
        ) as remote:
            assert remote.downloaded == 128
            assert remote.read(70) == payload[:70]
            assert remote.downloaded == 128
    finally:
        second_client.close()

    assert first_ranges == [(0, 0), (0, 191), (0, 63), (64, 127)]
    assert second_ranges == [(0, 0), (0, 191)]


def test_persistent_range_cache_is_bounded(tmp_path) -> None:
    payload = bytes(range(192))
    cache = tmp_path / "ranges"
    client, _ranges = _range_client(payload)
    try:
        with HttpRangeFile(
            "https://cdn.example/game.zip",
            client=client,
            block_size=64,
            prefetch=False,
            disk_cache=cache,
            disk_cache_max_bytes=128,
        ) as remote:
            assert remote.read() == payload
    finally:
        client.close()

    blocks = list(cache.glob("*/block-*.bin"))
    assert len(blocks) == 2
    assert sum(path.stat().st_size for path in blocks) == 128


def test_remote_zip_resume_keeps_completed_members(caplog, tmp_path) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("first.bin", b"a" * 32)
        archive.writestr("second.bin", b"b" * 32)
    destination = tmp_path / "game"
    workspace = tmp_path / ".game.ondemand.part"

    def interrupt_after_first_file(extracted: int, _total: int) -> None:
        if extracted > 32:
            raise RuntimeError("connection lost")

    with pytest.raises(RuntimeError, match="connection lost"):
        ArchiveExtractor().extract_zip_stream(
            io.BytesIO(payload.getvalue()),
            destination,
            archive_size=len(payload.getvalue()),
            progress=interrupt_after_first_file,
            resume_workspace=workspace,
        )

    assert (workspace / "extracted" / "first.bin").read_bytes() == b"a" * 32
    assert not destination.exists()

    resumed = []
    with caplog.at_level(logging.INFO):
        result = ArchiveExtractor().extract_zip_stream(
            io.BytesIO(payload.getvalue()),
            destination,
            archive_size=len(payload.getvalue()),
            resume_workspace=workspace,
            resume_progress=lambda offset, members: resumed.append((offset, members)),
        )

    assert result.destination == destination
    assert (destination / "first.bin").read_bytes() == b"a" * 32
    assert (destination / "second.bin").read_bytes() == b"b" * 32
    assert resumed[0][0] > 0
    assert resumed[0][1] == 1
    assert "Remote ZIP member restored from checkpoint member='first.bin'" in caplog.text


def test_remote_zip_uses_metadata_for_exact_storage_preflight(
    monkeypatch, tmp_path
) -> None:
    payload = _zip_bytes()
    workspace = tmp_path / ".game.ondemand.part"
    estimates = []
    monkeypatch.setattr(
        "game_downloader.archive.extractor.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=10),
    )

    with pytest.raises(ArchiveError, match="ZIP açıldığında"):
        ArchiveExtractor().extract_zip_stream(
            io.BytesIO(payload),
            tmp_path / "game",
            archive_size=len(payload),
            resume_workspace=workspace,
            working_storage_size=128,
            storage_progress=estimates.append,
        )

    assert estimates[0].extracted_size == len(b"wanted content") + 256 * 40
    assert estimates[0].peak_size == estimates[0].extracted_size + 128
    assert estimates[0].enough is False
