from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from game_downloader.download.manager import DownloadManager, NoticeCallback, ProgressCallback
from game_downloader.models import (
    BrowserDirectSource,
    BrowserDownloadRecord,
    ResolvedDownload,
)
from game_downloader.security import safe_filename

logger = logging.getLogger(__name__)

DOWNLOAD_DIALOG_SELECTOR = '[role="dialog"][aria-labelledby="downloadOpen-title"]'
DOWNLOAD_CALL_RE = re.compile(r"\bgenerateDownloadUrl\(\s*['\"]?(?P<id>[^'\")\s]+)")
CAPTCHA_SELECTOR = (
    "iframe[src*='recaptcha'], iframe[src*='turnstile'], "
    "[data-sitekey], .g-recaptcha, .cf-turnstile"
)


class BrowserDirectError(RuntimeError):
    pass


class UpgradeRequiredError(BrowserDirectError):
    pass


class GeoBlockedError(BrowserDirectError):
    pass


@dataclass(frozen=True)
class BrowserOptions:
    executable_path: Path | None = None
    headless: bool = False
    timeout_seconds: float = 30.0


def download_id_from_click(value: str | None) -> str | None:
    match = DOWNLOAD_CALL_RE.search(value or "")
    return match.group("id") if match else None


def resolved_from_response(
    payload: dict[str, object],
    record: BrowserDownloadRecord,
    page_url: str,
) -> ResolvedDownload:
    if payload.get("show_upgrade"):
        raise UpgradeRequiredError("Üyelik kotası doldu veya bu indirme üyelik gerektiriyor.")
    if payload.get("geo_block") or payload.get("geo-block"):
        raise GeoBlockedError("Bu indirme bulunduğunuz bölgede kullanılamıyor.")
    url = payload.get("download_url")
    if payload.get("success") and isinstance(url, str) and url:
        filename = record.name.strip() or f"download-{record.id}"
        return ResolvedDownload(
            source_id=record.id,
            filename=safe_filename(filename),
            size=record.size,
            url=url,
            referer=page_url,
        )
    message = payload.get("message") or payload.get("error")
    raise BrowserDirectError(str(message or "Sunucu geçici indirme adresi döndürmedi."))


class BrowserDirectDownloader:
    """Keep Chrome alive while its first visible modal download is saved."""

    def __init__(
        self,
        options: BrowserOptions,
        *,
        manager: DownloadManager | None = None,
        playwright_factory: Callable[[], object] | None = None,
    ) -> None:
        self.options = options
        self.manager = manager or DownloadManager()
        self._playwright_factory = playwright_factory
        self._active_browser_download: object | None = None

    def pause(self) -> None:
        self.manager.pause()

    def resume(self) -> None:
        self.manager.resume()

    def cancel(self) -> None:
        self.manager.cancel()
        active = self._active_browser_download
        if active is not None:
            with suppress(Exception):
                __import__("asyncio").create_task(active.cancel())

    def set_speed_limit(self, value: int | None) -> None:
        self.manager.set_speed_limit(value)

    async def download(
        self,
        source: BrowserDirectSource,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
        notice: NoticeCallback | None = None,
    ) -> Path:
        await _notice(notice, "Tarayıcı başlatılıyor…")
        factory = self._playwright_factory
        if factory is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise BrowserDirectError(
                    "Playwright kurulu değil. `playwright install chromium` komutunu çalıştırın."
                ) from exc
            factory = async_playwright
        context = None
        browser = None
        playwright = factory()
        try:
            runtime = await playwright.start()
            kwargs: dict[str, object] = {"headless": self.options.headless}
            if self.options.executable_path:
                kwargs["executable_path"] = str(self.options.executable_path)
            elif bundled := _bundled_browser(self.options.headless):
                kwargs["executable_path"] = str(bundled)
            browser = await runtime.chromium.launch(**kwargs)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            page.set_default_timeout(self.options.timeout_seconds * 1000)
            await _notice(notice, "Oyun sayfası açılıyor…")
            expected_host = urlsplit(str(source.page_url)).hostname
            await page.goto(str(source.page_url), wait_until="domcontentloaded")
            await _notice(notice, "Sayfa hazır")
            if urlsplit(page.url).hostname != expected_host:
                raise BrowserDirectError("Oyun sayfası beklenmeyen bir domaine yönlendirildi.")
            if await self._captcha_is_visible(page):
                raise BrowserDirectError(
                    "Sayfa CAPTCHA veya insan doğrulaması istiyor. "
                    "Lütfen tarayıcıda doğrulamayı tamamlayıp yeniden deneyin."
                )
            dialog = await self._open_download_dialog(page, notice)
            button, download_id = await self._first_download_button(dialog)
            await button.wait_for(state="visible")
            logger.info("İlk görünen Download ID seçildi: %s", download_id)
            if await self._captcha_is_visible(page):
                raise BrowserDirectError(
                    "İndirme penceresi CAPTCHA veya insan doğrulaması istiyor."
                )
            payload, suggested_filename = await self._request_download_url(
                page, button, download_id, notice
            )
            record = next(
                (item for item in source.downloads if item.id == download_id),
                BrowserDownloadRecord(id=download_id),
            )
            if suggested_filename:
                record = record.model_copy(update={"name": suggested_filename})
            resolved = resolved_from_response(payload, record, str(source.page_url))
            await _notice(notice, "Geçici indirme adresi alındı")
            await _notice(notice, f"İndirme başladı: {resolved.filename}")
            result = await self.manager.download(
                resolved,
                destination,
                progress=progress,
                notice=notice,
            )
            await _notice(notice, f"İndirme tamamlandı: {result}")
            return result
        except TimeoutError as exc:
            raise BrowserDirectError("Tarayıcı işlemi zaman aşımına uğradı.") from exc
        finally:
            self._active_browser_download = None
            if context is not None:
                with suppress(Exception):
                    await context.close()
            if browser is not None:
                with suppress(Exception):
                    await browser.close()
            with suppress(Exception):
                await playwright.stop()

    async def _generate_url(self, page: object, download_id: str, notice: NoticeCallback | None):
        dialog = await self._open_download_dialog(page, notice)
        button = await self._find_download_button(dialog, download_id)
        await _notice(notice, f"İndirme kimliği bulundu: {download_id}")
        if await self._captcha_is_visible(page):
            raise BrowserDirectError(
                "İndirme penceresi CAPTCHA veya insan doğrulaması istiyor. "
                "Otomatik doğrulama yapılmadı."
            )
        response = await self._click_for_response(page, button, download_id)
        await _notice(notice, "Geçici adres isteği gönderildi")
        await _notice(notice, f"Sunucu HTTP {response.status}")
        if response.status == 419:
            await _notice(notice, "CSRF/oturum yenileniyor…")
            await self._refresh_csrf(page)
            if await self._captcha_is_visible(page):
                raise BrowserDirectError(
                    "CSRF yenilemesinden sonra insan doğrulaması gerekiyor. "
                    "Otomatik doğrulama yapılmadı."
                )
            response = await self._click_for_response(page, button, download_id)
            await _notice(notice, "CSRF yenilemesinden sonra istek yeniden gönderildi")
            await _notice(notice, f"Sunucu HTTP {response.status}")
            if response.status == 419:
                raise BrowserDirectError("CSRF oturumu yenilenemedi; lütfen yeniden deneyin.")
        if response.status in {401, 403}:
            raise UpgradeRequiredError("İndirme için yetkilendirme veya üyelik gerekiyor.")
        if response.status >= 400:
            raise BrowserDirectError(f"İndirme servisi HTTP {response.status} döndürdü.")
        return await response.json()

    async def _request_download_url(
        self,
        page: object,
        button: object,
        download_id: str,
        notice: NoticeCallback | None,
    ) -> tuple[dict[str, object], str | None]:
        loop = asyncio.get_running_loop()
        suggested: asyncio.Future[str] = loop.create_future()

        async def cancel_native(download: object) -> None:
            self._active_browser_download = download
            if not suggested.done():
                suggested.set_result(safe_filename(download.suggested_filename))
            await download.cancel()

        def on_download(download: object) -> None:
            asyncio.create_task(cancel_native(download))

        page.on("download", on_download)
        try:
            response = await self._click_for_response(page, button, download_id)
            await _notice(notice, "Geçici adres isteği gönderildi")
            await _notice(notice, f"Sunucu HTTP {response.status}")
            if response.status == 419:
                await _notice(notice, "CSRF/oturum yenileniyor…")
                await self._refresh_csrf(page)
                response = await self._click_for_response(page, button, download_id)
                await _notice(notice, f"Sunucu HTTP {response.status}")
                if response.status == 419:
                    raise BrowserDirectError("CSRF oturumu yenilenemedi; yeniden deneyin.")
            if response.status in {401, 403}:
                raise UpgradeRequiredError("İndirme için yetkilendirme veya üyelik gerekiyor.")
            if response.status >= 400:
                raise BrowserDirectError(f"İndirme servisi HTTP {response.status} döndürdü.")
            payload = await response.json()
            try:
                filename = await asyncio.wait_for(asyncio.shield(suggested), timeout=1.0)
            except TimeoutError:
                filename = None
            return payload, filename
        finally:
            page.remove_listener("download", on_download)

    @staticmethod
    async def _open_download_dialog(page: object, notice: NoticeCallback | None):
        dialog = page.locator(DOWNLOAD_DIALOG_SELECTOR)
        main = page.locator(
            "xpath=//button[contains(@click, 'open-download-modal') and "
            ".//span[normalize-space()='Download']]"
        )
        if await main.count() == 1:
            await main.click()
        else:
            await page.evaluate("window.dispatchEvent(new CustomEvent('open-download-modal'))")
        await dialog.wait_for(state="visible")
        await _notice(notice, "İndirme penceresi açıldı")
        return dialog

    @staticmethod
    async def _click_for_response(page: object, button: object, download_id: str):
        endpoint = f"/generate-download-url/{download_id}"
        async with page.expect_response(lambda response: endpoint in response.url) as pending:
            await button.click()
        return await pending.value

    @staticmethod
    async def _captcha_is_visible(page: object) -> bool:
        locator = page.locator(CAPTCHA_SELECTOR)
        for index in range(await locator.count()):
            if await locator.nth(index).is_visible():
                return True
        return False

    @staticmethod
    async def _refresh_csrf(page: object) -> None:
        token = await page.locator('meta[name="csrf-token"]').get_attribute("content")
        if not token:
            cookies = await page.context.cookies()
            token = next(
                (
                    cookie["value"]
                    for cookie in cookies
                    if cookie["name"] in {"XSRF-TOKEN", "csrf_token"}
                ),
                "",
            )
        # Keep the browser's own cookie jar and JavaScript flow; never copy this token
        # to the streaming HTTP client or log it.
        del token
        response = await page.request.get("/csrf-token")
        if response.status >= 400:
            raise BrowserDirectError("CSRF oturumu yenilenemedi.")

    @staticmethod
    async def _find_download_button(dialog: object, download_id: str):
        if not re.fullmatch(r"\d+", download_id):
            raise BrowserDirectError("Download ID yalnızca rakamlardan oluşmalıdır.")
        button = dialog.locator(
            "xpath=.//a[contains(concat(' ', normalize-space(@class), ' '), "
            "' download-button ') and contains(@*[name() = '@click.prevent'], "
            f"'generateDownloadUrl({download_id})')]"
        )
        if await button.count() != 1:
            raise BrowserDirectError(
                f"İndirme penceresinde tek bir görünür ID bulunamadı: {download_id}"
            )
        return button

    @staticmethod
    async def _first_download_button(dialog: object) -> tuple[object, str]:
        buttons = dialog.locator(
            "xpath=.//a[contains(concat(' ', normalize-space(@class), ' '), "
            "' download-button ') and @*[name() = '@click.prevent']]"
        )
        for index in range(await buttons.count()):
            button = buttons.nth(index)
            if not await button.is_visible():
                continue
            download_id = download_id_from_click(
                await button.get_attribute("@click.prevent")
            )
            if download_id:
                return button, download_id
        raise BrowserDirectError("İndirme penceresinde görünür Download kaydı bulunamadı.")



async def _notice(callback: NoticeCallback | None, message: str) -> None:
    logger.info(message)
    if callback:
        result = callback(message)
        if isinstance(result, Awaitable):
            await result


def _bundled_browser(headless: bool) -> Path | None:
    root = Path.cwd() / ".playwright-browsers"
    patterns = (
        ("chromium-*/**/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",)
        if not headless
        else ()
    )
    patterns += (
        "chromium-*/**/chrome",
        "chromium-*/**/chrome.exe",
        "chromium_headless_shell-*/**/chrome-headless-shell",
        "chromium_headless_shell-*/**/chrome-headless-shell.exe",
    )
    for pattern in patterns:
        candidates = sorted(path for path in root.glob(pattern) if path.is_file())
        if candidates:
            return candidates[-1]
    return None
