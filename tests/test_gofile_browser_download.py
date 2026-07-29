from pathlib import Path

import pytest

from game_downloader.storage.gofile_browser_download import (
    GoFileBrowserDownload,
    GoFileBrowserDownloadError,
    _available_download_path,
    _is_gofile_host,
    _validate_download_url,
    _validate_share_page,
    _wait_for_single_download_control,
)


@pytest.mark.asyncio
async def test_browser_download_is_disabled_by_default(tmp_path: Path):
    with pytest.raises(GoFileBrowserDownloadError, match="kapalı"):
        await GoFileBrowserDownload().download("owned123", tmp_path)


def test_navigation_and_download_hosts_are_restricted_to_gofile():
    _validate_share_page("https://gofile.io/d/owned123", "owned123")
    _validate_download_url("https://store1.gofile.io/download/owned/file.zip")
    assert _is_gofile_host("gofile.io")
    assert _is_gofile_host("store1.gofile.io")
    assert not _is_gofile_host("gofile.io.evil.example")
    with pytest.raises(GoFileBrowserDownloadError, match="Beklenmeyen GoFile"):
        _validate_share_page("https://evil.example/d/owned123", "owned123")
    with pytest.raises(GoFileBrowserDownloadError, match="beklenmeyen bir adresten"):
        _validate_download_url("https://evil.example/file.zip")


def test_existing_downloads_get_a_non_overwriting_name(tmp_path):
    (tmp_path / "owned.rar").write_bytes(b"existing")
    (tmp_path / "owned (2).rar.part").write_bytes(b"partial")

    result = _available_download_path(tmp_path, "owned.rar")

    assert result == tmp_path / "owned (3).rar"


@pytest.mark.asyncio
async def test_single_visible_download_control_is_selected():
    class FakeControl:
        async def is_visible(self):
            return True

        async def is_enabled(self):
            return True

    class FakeMatches:
        def __init__(self, count):
            self._count = count

        async def count(self):
            return self._count

        def nth(self, index):
            return FakeControl()

    class FakePage:
        url = "https://gofile.io/d/owned123"

        def is_closed(self):
            return False

        def locator(self, selector):
            assert selector == "button.item_download"
            return FakeMatches(1)

        def get_by_role(self, role, name):
            return FakeMatches(0)

    control = await _wait_for_single_download_control(
        FakePage(),
        "owned123",
        timeout_seconds=1,
    )
    assert isinstance(control, FakeControl)


@pytest.mark.asyncio
async def test_multiple_download_controls_stop_automatic_selection():
    class FakeControl:
        async def is_visible(self):
            return True

        async def is_enabled(self):
            return True

    class FakeMatches:
        def __init__(self, count):
            self._count = count

        async def count(self):
            return self._count

        def nth(self, index):
            return FakeControl()

    class FakePage:
        url = "https://gofile.io/d/owned123"

        def is_closed(self):
            return False

        def locator(self, selector):
            assert selector == "button.item_download"
            return FakeMatches(2)

        def get_by_role(self, role, name):
            return FakeMatches(0)

    with pytest.raises(GoFileBrowserDownloadError, match="birden fazla"):
        await _wait_for_single_download_control(
            FakePage(),
            "owned123",
            timeout_seconds=1,
        )
