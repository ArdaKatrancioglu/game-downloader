from types import SimpleNamespace

import pytest

from game_downloader.models import BrowserDirectSource, BrowserDownloadRecord, DownloadProgress
from game_downloader.storage.browser_direct import (
    CAPTCHA_SELECTOR,
    DOWNLOAD_DIALOG_SELECTOR,
    BrowserDirectDownloader,
    BrowserDirectError,
    UpgradeRequiredError,
    download_id_from_click,
    resolved_from_response,
)


def test_download_modal_selector_is_specific():
    assert DOWNLOAD_DIALOG_SELECTOR == '[role="dialog"][aria-labelledby="downloadOpen-title"]'


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("generateDownloadUrl(4584)", "4584"), ("generateDownloadUrl('abc')", "abc")],
)
def test_extracts_download_id_from_alpine_click(expression, expected):
    assert download_id_from_click(expression) == expected


@pytest.mark.asyncio
async def test_download_button_uses_working_alpine_xpath():
    captured = []

    class Button:
        async def count(self):
            return 1

    class Dialog:
        def locator(self, selector):
            captured.append(selector)
            return Button()

    button = await BrowserDirectDownloader._find_download_button(Dialog(), "4584")

    assert isinstance(button, Button)
    assert "download-button" in captured[0]
    assert "@click.prevent" in captured[0]
    assert "generateDownloadUrl(4584)" in captured[0]


@pytest.mark.asyncio
async def test_first_visible_download_record_is_selected_automatically():
    class Button:
        def __init__(self, visible, click_value):
            self.visible = visible
            self.click_value = click_value

        async def is_visible(self):
            return self.visible

        async def get_attribute(self, name):
            assert name == "@click.prevent"
            return self.click_value

    buttons = [
        Button(False, "generateDownloadUrl(1)"),
        Button(True, "generateDownloadUrl(4584)"),
        Button(True, "generateDownloadUrl(4698)"),
    ]

    class Buttons:
        async def count(self):
            return len(buttons)

        def nth(self, index):
            return buttons[index]

    dialog = SimpleNamespace(locator=lambda selector: Buttons())
    button, download_id = await BrowserDirectDownloader._first_download_button(dialog)

    assert button is buttons[1]
    assert download_id == "4584"


def test_successful_download_url_response():
    resolved = resolved_from_response(
        {"success": True, "download_url": "https://cdn.example/demo.zip"},
        BrowserDownloadRecord(id="4584", name="demo.zip", size=10),
        "https://catalog.example/game",
    )
    assert str(resolved.url) == "https://cdn.example/demo.zip"
    assert resolved.size == 10


def test_show_upgrade_response_is_explained():
    with pytest.raises(UpgradeRequiredError, match="üyelik"):
        resolved_from_response(
            {"show_upgrade": True},
            BrowserDownloadRecord(id="1"),
            "https://catalog.example/game",
        )


def test_browser_flow_controls_delegate_to_download_manager():
    calls = []

    class Manager:
        def pause(self): calls.append("pause")
        def resume(self): calls.append("resume")
        def cancel(self): calls.append("cancel")
        def set_speed_limit(self, value): calls.append(("limit", value))

    downloader = BrowserDirectDownloader(SimpleNamespace(), manager=Manager())
    downloader.pause()
    downloader.resume()
    downloader.set_speed_limit(125_000)
    downloader.cancel()

    assert calls == ["pause", "resume", ("limit", 125_000), "cancel"]


@pytest.mark.asyncio
async def test_http_419_refreshes_csrf_and_retries(monkeypatch):
    notices = []
    downloader = BrowserDirectDownloader(SimpleNamespace())
    page = SimpleNamespace(
        locator=lambda value: SimpleNamespace(wait_for=lambda **kwargs: _done()),
        get_by_role=lambda *args, **kwargs: SimpleNamespace(first=SimpleNamespace(click=_done)),
    )
    monkeypatch.setattr(downloader, "_find_download_button", _button)
    monkeypatch.setattr(downloader, "_open_download_dialog", _dialog)
    responses = [
        SimpleNamespace(status=419),
        _json_response({"success": True, "download_url": "https://cdn.example/a"}),
    ]
    refreshes = []

    async def click(*args):
        return responses.pop(0)

    async def refresh(*args):
        refreshes.append(True)

    monkeypatch.setattr(downloader, "_click_for_response", click)
    monkeypatch.setattr(downloader, "_refresh_csrf", refresh)
    monkeypatch.setattr(downloader, "_captcha_is_visible", _false)
    result = await downloader._generate_url(page, "7", notices.append)
    assert result["success"] is True
    assert refreshes == [True]
    assert any("yenileniyor" in item for item in notices)


async def _done(*args, **kwargs):
    return None


async def _button(*args, **kwargs):
    return SimpleNamespace(click=_done)


async def _dialog(*args, **kwargs):
    return SimpleNamespace()


async def _false(*args, **kwargs):
    return False


def _json_response(payload):
    async def json():
        return payload

    return SimpleNamespace(status=200, json=json)


@pytest.mark.asyncio
async def test_normal_click_response_cancels_native_copy_and_returns_url(monkeypatch):
    handlers = {}
    cancelled = []

    class NativeDownload:
        suggested_filename = "demo.zip"

        async def cancel(self):
            cancelled.append(True)

    class Page:
        def on(self, event, callback):
            handlers[event] = callback

        def remove_listener(self, event, callback):
            assert handlers[event] is callback

    downloader = BrowserDirectDownloader(SimpleNamespace())

    async def click(*args):
        handlers["download"](NativeDownload())
        return _json_response(
            {"success": True, "download_url": "https://cdn.example/demo.zip"}
        )

    monkeypatch.setattr(downloader, "_click_for_response", click)
    payload, filename = await downloader._request_download_url(
        Page(), SimpleNamespace(), "4584", None
    )

    assert payload["success"] is True
    assert filename == "demo.zip"
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_captcha_stops_before_download_url_request(monkeypatch):
    downloader = BrowserDirectDownloader(SimpleNamespace())
    page = SimpleNamespace(
        locator=lambda value: SimpleNamespace(wait_for=lambda **kwargs: _done()),
        get_by_role=lambda *args, **kwargs: SimpleNamespace(first=SimpleNamespace(click=_done)),
    )
    monkeypatch.setattr(downloader, "_find_download_button", _button)
    monkeypatch.setattr(downloader, "_open_download_dialog", _dialog)
    monkeypatch.setattr(downloader, "_captcha_is_visible", lambda page: _true())

    with pytest.raises(BrowserDirectError, match="CAPTCHA"):
        await downloader._generate_url(page, "7", None)


async def _true():
    return True


def test_captcha_selector_covers_supported_challenges():
    assert "recaptcha" in CAPTCHA_SELECTOR
    assert "turnstile" in CAPTCHA_SELECTOR


@pytest.mark.asyncio
async def test_captcha_check_iterates_async_locators_without_async_generator_error():
    class Candidate:
        def __init__(self, visible):
            self.visible = visible

        async def is_visible(self):
            return self.visible

    class Locator:
        async def count(self):
            return 2

        def nth(self, index):
            return Candidate(index == 1)

    page = SimpleNamespace(locator=lambda selector: Locator())
    assert await BrowserDirectDownloader._captcha_is_visible(page) is True


@pytest.mark.asyncio
async def test_browser_context_closes_cleanly_on_download_error(tmp_path):
    events = []

    class Page:
        url = "https://catalog.example/game"

        def set_default_timeout(self, value): pass
        async def goto(self, *args, **kwargs): pass

    class Context:
        async def new_page(self): return Page()
        async def close(self): events.append("context-closed")

    class Browser:
        async def new_context(self, **kwargs): return Context()
        async def close(self): events.append("browser-closed")

    class Chromium:
        async def launch(self, **kwargs): return Browser()

    class Runtime:
        chromium = Chromium()

    class Playwright:
        async def start(self): return Runtime()
        async def stop(self): events.append("playwright-stopped")

    downloader = BrowserDirectDownloader(
        SimpleNamespace(headless=False, executable_path=None, timeout_seconds=1),
        playwright_factory=Playwright,
    )

    async def fail(*args, **kwargs):
        raise RuntimeError("boom")

    downloader._open_download_dialog = fail
    downloader._captcha_is_visible = _false
    source = BrowserDirectSource(
        page_url="https://catalog.example/game",
        downloads=[BrowserDownloadRecord(id="7", name="demo.zip")],
    )
    with pytest.raises(RuntimeError, match="boom"):
        await downloader.download(source, tmp_path)
    assert events == ["context-closed", "browser-closed", "playwright-stopped"]


def test_browser_worker_transfers_progress_signal(tmp_path):
    from game_downloader.ui.workers import BrowserDirectWorker

    received = []

    class Downloader:
        async def download(self, source, destination, *, progress, notice):
            progress(DownloadProgress(
                downloaded=5, total=10, percent=50, bytes_per_second=2, eta_seconds=2.5
            ))
            return destination / "demo.zip"

    source = BrowserDirectSource(
        page_url="https://catalog.example/game",
        downloads=[BrowserDownloadRecord(id="7", name="demo.zip")],
    )
    worker = BrowserDirectWorker(Downloader(), source, tmp_path)
    worker.progress.connect(received.append)
    worker.run()
    assert received[0].downloaded == 5
