from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from game_downloader.archive.extractor import ArchiveExtractor
from game_downloader.archive.http_range import (
    HttpRangeFile,
    RangeDownloadCancelled,
    RangeDownloadError,
    RangeNotSupported,
    RangeTransferControl,
)
from game_downloader.download.manager import (
    DownloadCancelled,
    DownloadManager,
    NoticeCallback,
    ProgressCallback,
)
from game_downloader.download.progress import ProgressTracker
from game_downloader.models import (
    BrowserDirectSource,
    BrowserDownloadRecord,
    ExtractionLimits,
    ResolvedDownload,
)
from game_downloader.security import ensure_public_host, safe_filename

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


@dataclass(frozen=True)
class RemoteExtractionResult:
    destination: Path
    file_count: int
    total_size: int
    archive_size: int
    downloaded_size: int


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
            # Listing metadata is intended for display and is not an exact byte
            # contract for the generated archive.
            size=None,
            url=url,
            referer=page_url,
            require_attachment=True,
        )
    message = payload.get("message") or payload.get("error")
    raise BrowserDirectError(str(message or "Sunucu geçici indirme adresi döndürmedi."))


def _available_destination(folder: Path, filename: str) -> Path:
    name = Path(filename).stem or "archive"
    candidate = folder / name
    index = 2
    while candidate.exists():
        candidate = folder / f"{name} ({index})"
        index += 1
    return candidate


class BrowserDirectDownloader:
    """Use Chrome to resolve a download URL, then stream it without Chrome."""

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
        self._range_control = RangeTransferControl(
            getattr(self.manager, "max_bytes_per_second", None)
        )

    def pause(self) -> None:
        self.manager.pause()
        self._range_control.pause()

    def resume(self) -> None:
        self.manager.resume()
        self._range_control.resume()

    def cancel(self) -> None:
        self.manager.cancel()
        self._range_control.cancel()
        active = self._active_browser_download
        if active is not None:
            with suppress(Exception):
                __import__("asyncio").create_task(active.cancel())

    def set_speed_limit(self, value: int | None) -> None:
        self.manager.set_speed_limit(value)
        self._range_control.set_speed_limit(value)

    def pause_range(self) -> None:
        self._range_control.pause()

    def resume_range(self) -> None:
        self._range_control.resume()

    def cancel_range(self) -> None:
        self._range_control.cancel()

    def set_range_speed_limit(self, value: int | None) -> None:
        self._range_control.set_speed_limit(value)

    async def download(
        self,
        source: BrowserDirectSource,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
        notice: NoticeCallback | None = None,
        stream_extract_zip: bool = False,
        extraction_limits: ExtractionLimits | None = None,
    ) -> Path | RemoteExtractionResult:
        self._range_control.reset()
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
            await _notice(notice, "Tarayıcı kapatılıyor…")
            await _close_browser_resources(context, browser, playwright)
            context = None
            browser = None
            playwright = None
            if stream_extract_zip and resolved.filename.casefold().endswith(".zip"):
                await _notice(notice, "ZIP Range desteği denetleniyor…")
                try:
                    result = await self._extract_remote_zip(
                        resolved,
                        destination,
                        progress=progress,
                        notice=notice,
                        limits=extraction_limits,
                    )
                except RangeDownloadCancelled as exc:
                    raise DownloadCancelled(str(exc)) from exc
                except (RangeNotSupported, RangeDownloadError, NotImplementedError) as exc:
                    logger.warning(
                        "Remote ZIP extraction completed result=fallback error_type=%s detail=%s",
                        type(exc).__name__,
                        exc,
                    )
                    await _notice(
                        notice,
                        "Sunucu doğrudan ZIP açmayı desteklemiyor; normal indirmeye geçiliyor…",
                    )
                else:
                    await _notice(notice, f"İndirme sırasında arşiv açıldı: {result.destination}")
                    return result
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
            await _close_browser_resources(context, browser, playwright)

    async def _extract_remote_zip(
        self,
        resolved: ResolvedDownload,
        destination_folder: Path,
        *,
        progress: ProgressCallback | None,
        notice: NoticeCallback | None,
        limits: ExtractionLimits | None,
    ) -> RemoteExtractionResult:
        host = self.manager._validate_download_url(str(resolved.url))
        if getattr(self.manager, "resolve_hosts", True):
            await ensure_public_host(host)
        destination = _available_destination(destination_folder, resolved.filename)
        tracker = ProgressTracker()

        def report_download(downloaded: int, total: int) -> None:
            if progress:
                progress(tracker.update(downloaded, total))

        await _notice(notice, "ZIP dizini uzaktan okunuyor…")
        with HttpRangeFile(
            str(resolved.url),
            referer=str(resolved.referer) if resolved.referer else None,
            require_attachment=resolved.require_attachment,
            control=self._range_control,
            progress=report_download,
        ) as remote:
            logger.info(
                "Remote ZIP extraction started archive_size=%d block_size=%d prefetch=%s",
                remote.size,
                remote.block_size,
                remote.prefetch,
            )
            await _notice(notice, "ZIP inerken hedef klasöre açılıyor…")
            extraction = ArchiveExtractor(limits).extract_zip_stream(
                remote,
                destination,
                archive_size=remote.size,
            )
            if progress:
                progress(tracker.update(remote.size, remote.size))
            logger.info(
                "Remote ZIP extraction completed result=success archive_size=%d "
                "downloaded_size=%d extracted_size=%d file_count=%d",
                remote.size,
                remote.downloaded,
                extraction.total_size,
                extraction.file_count,
            )
            return RemoteExtractionResult(
                destination=extraction.destination,
                file_count=extraction.file_count,
                total_size=extraction.total_size,
                archive_size=remote.size,
                downloaded_size=remote.downloaded,
            )

    async def _request_download_url(
        self,
        page: object,
        button: object,
        download_id: str,
        notice: NoticeCallback | None,
    ) -> tuple[dict[str, object], str | None]:
        loop = asyncio.get_running_loop()
        native_download: asyncio.Future[tuple[str, str]] = loop.create_future()

        async def cancel_native(download: object) -> None:
            self._active_browser_download = download
            if not native_download.done():
                native_download.set_result(
                    (
                        safe_filename(download.suggested_filename),
                        str(download.url),
                    )
                )
            await download.cancel()

        def on_download(download: object) -> None:
            asyncio.create_task(cancel_native(download))

        page.on("download", on_download)
        try:
            status, payload = await self._capture_generate_response(
                page, button, download_id
            )
            await _notice(notice, "Geçici adres isteği gönderildi")
            await _notice(notice, f"Sunucu HTTP {status}")
            if status == 419:
                await _notice(notice, "CSRF/oturum yenileniyor…")
                await self._refresh_csrf(page)
                status, payload = await self._capture_generate_response(
                    page, button, download_id
                )
                await _notice(notice, f"Sunucu HTTP {status}")
                if status == 419:
                    raise BrowserDirectError("CSRF oturumu yenilenemedi; yeniden deneyin.")
            if status in {401, 403}:
                raise UpgradeRequiredError("İndirme için yetkilendirme veya üyelik gerekiyor.")
            if status >= 400:
                raise BrowserDirectError(f"İndirme servisi HTTP {status} döndürdü.")
            if not payload.get("success") or not payload.get("download_url"):
                return payload, None
            try:
                filename, final_url = await asyncio.wait_for(
                    asyncio.shield(native_download),
                    timeout=self.options.timeout_seconds,
                )
            except TimeoutError as exc:
                raise BrowserDirectError(
                    "Tarayıcı gerçek dosya indirmesini başlatmadı."
                ) from exc
            # The JSON URL can be an HTML transition page. The Download event URL
            # is the attachment request Chrome actually selected.
            payload = dict(payload)
            payload["download_url"] = final_url
            return payload, filename
        finally:
            page.remove_listener("download", on_download)

    async def _capture_generate_response(
        self,
        page: object,
        button: object,
        download_id: str,
    ) -> tuple[int, dict[str, object]]:
        loop = asyncio.get_running_loop()
        captured: asyncio.Future[tuple[int, dict[str, object]]] = loop.create_future()
        binding_name = f"__game_downloader_capture_{id(captured)}"

        def receive_response(
            _source: object,
            status: int,
            body: str,
            response_url: str,
        ) -> None:
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("JSON response is not an object")
                if not captured.done():
                    captured.set_result((int(status), payload))
            except Exception as exc:
                if not captured.done():
                    captured.set_exception(
                        BrowserDirectError("İndirme servisi yanıtı okunamadı.")
                    )
                logger.warning(
                    "generate-download-url response capture failed status=%s url=%s "
                    "body_length=%s",
                    status,
                    response_url,
                    len(body),
                    exc_info=exc,
                )

        # Capture a clone inside the page before its own JavaScript can navigate to
        # the signed URL. Reading Playwright's Response afterwards races with that
        # navigation and Chromium then discards Network.getResponseBody's resource.
        await page.expose_binding(binding_name, receive_response)
        await page.evaluate(
            """
            ({ bindingName, downloadId }) => {
                const wantedPath = `/generate-download-url/${downloadId}`;
                const originalFetch = window.fetch;
                window.fetch = async function (...args) {
                    const response = await originalFetch.apply(this, args);
                    let pathname = '';
                    try {
                        pathname = new URL(response.url, window.location.href).pathname;
                    } catch (_) {}
                    if (pathname.endsWith(wantedPath)) {
                        try {
                            const body = await response.clone().text();
                            await window[bindingName](
                                response.status,
                                body,
                                response.url,
                            );
                        } finally {
                            window.fetch = originalFetch;
                        }
                    }
                    return response;
                };
            }
            """,
            {"bindingName": binding_name, "downloadId": download_id},
        )
        click_task = asyncio.create_task(button.click())
        try:
            result = await asyncio.wait_for(
                captured,
                timeout=self.options.timeout_seconds,
            )
            with suppress(Exception):
                await click_task
            return result
        finally:
            if not click_task.done():
                click_task.cancel()

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


async def _close_browser_resources(
    context: object | None,
    browser: object | None,
    playwright: object | None,
) -> None:
    if context is not None:
        with suppress(Exception):
            await context.close()
    if browser is not None:
        with suppress(Exception):
            await browser.close()
    if playwright is not None:
        with suppress(Exception):
            await playwright.stop()


def _bundled_browser(headless: bool) -> Path | None:
    roots = [Path.cwd() / ".playwright-browsers"]
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.insert(0, Path(frozen_root) / ".playwright-browsers")
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
    for root in roots:
        for pattern in patterns:
            candidates = sorted(path for path in root.glob(pattern) if path.is_file())
            if candidates:
                return candidates[-1]
    return None
