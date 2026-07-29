from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from game_downloader.http_diagnostics import safe_url
from game_downloader.security import extract_gofile_content_id, safe_filename

logger = logging.getLogger(__name__)
GOFILE_HOST = "gofile.io"
_DOWNLOAD_NAME = re.compile(r"\bdownload\b", re.IGNORECASE)


class GoFileBrowserDownloadError(RuntimeError):
    pass


class GoFileBrowserDownload:
    """API-free, visible browser download for an authorized public GoFile share."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        remember_session: bool = True,
        profile_dir: Path | None = None,
        timeout_seconds: float = 300.0,
        failure_hold_seconds: float = 15.0,
    ) -> None:
        self.enabled = enabled
        self.remember_session = remember_session
        self.profile_dir = profile_dir
        self.timeout_seconds = timeout_seconds
        self.failure_hold_seconds = failure_hold_seconds

    async def download(
        self,
        content_id: str,
        destination: Path,
        *,
        notice: Callable[[str], None] | None = None,
    ) -> Path:
        if not self.enabled:
            raise GoFileBrowserDownloadError(
                "Visible GoFile browser downloads are disabled in Settings."
            )
        content_id = extract_gofile_content_id(content_id)
        destination.mkdir(parents=True, exist_ok=True)
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise GoFileBrowserDownloadError(
                "GoFile browser downloads require the browser extra. Run "
                "`uv sync --extra browser` and `uv run playwright install chromium`."
            ) from exc

        temporary_profile: tempfile.TemporaryDirectory[str] | None = None
        if self.remember_session:
            if self.profile_dir is None:
                raise GoFileBrowserDownloadError(
                    "The dedicated GoFile browser profile folder is unavailable."
                )
            profile = self.profile_dir
            profile.mkdir(parents=True, exist_ok=True)
        else:
            temporary_profile = tempfile.TemporaryDirectory(
                prefix="authorized-game-downloader-gofile-"
            )
            profile = Path(temporary_profile.name)

        share_url = f"https://{GOFILE_HOST}/d/{content_id}"
        logger.info(
            "Starting visible API-free GoFile download share_url=%s "
            "remember_session=%s profile=%s",
            share_url,
            self.remember_session,
            profile if self.remember_session else "<temporary>",
        )
        _notify(notice, "Opening the visible GoFile Chromium window…")
        try:
            async with async_playwright() as playwright:
                try:
                    context = await playwright.chromium.launch_persistent_context(
                        str(profile),
                        headless=False,
                        accept_downloads=True,
                    )
                except Exception as exc:
                    raise GoFileBrowserDownloadError(
                        "Chromium could not be opened. Install it with "
                        "`uv run playwright install chromium`."
                    ) from exc
                try:
                    page = context.pages[0] if context.pages else await context.new_page()

                    async def inspect_extra_page(extra_page) -> None:
                        if extra_page is page:
                            return
                        await asyncio.sleep(2.0)
                        host = urlsplit(extra_page.url or "about:blank").hostname
                        if _is_gofile_host(host):
                            logger.info(
                                "Keeping GoFile popup url=%s host=%s",
                                safe_url(extra_page.url),
                                host,
                            )
                            return
                        logger.warning(
                            "Closing external GoFile popup url=%s host=%s",
                            safe_url(extra_page.url or "about:blank"),
                            host or "<none>",
                        )
                        await extra_page.close()

                    context.on(
                        "page",
                        lambda extra_page: asyncio.create_task(
                            inspect_extra_page(extra_page)
                        ),
                    )
                    try:
                        await page.goto(
                            share_url,
                            wait_until="domcontentloaded",
                            timeout=30_000,
                        )
                    except Exception as exc:
                        raise GoFileBrowserDownloadError(
                            "The visible browser could not open the GoFile share."
                        ) from exc
                    _validate_share_page(page.url, content_id)
                    _notify(notice, "GoFile page opened; locating the file Download button…")
                    logger.info(
                        "Visible GoFile page opened url=%s title=%r",
                        safe_url(page.url),
                        (await page.title())[:200],
                    )
                    control = await _wait_for_single_download_control(
                        page,
                        content_id,
                        self.timeout_seconds,
                    )
                    logger.info(
                        "Exactly one visible GoFile button.item_download found; "
                        "clicking it in the visible browser."
                    )
                    _notify(notice, "GoFile Download button found; clicking it now…")
                    try:
                        async with page.expect_download(
                            timeout=self.timeout_seconds * 1000
                        ) as download_info:
                            await control.click()
                            _notify(
                                notice,
                                "Download button clicked; waiting for Chromium's "
                                "download event…",
                            )
                        download = await download_info.value
                    except PlaywrightTimeoutError as exc:
                        raise GoFileBrowserDownloadError(
                            "The visible GoFile Download control did not start a "
                            "browser download within five minutes."
                        ) from exc
                    _validate_download_url(download.url)
                    filename = safe_filename(download.suggested_filename)
                    _notify(
                        notice,
                        f"Chromium download started: {filename}. Saving to .part…",
                    )
                    final_path = _available_download_path(destination, filename)
                    if final_path.parent != destination.resolve():
                        raise GoFileBrowserDownloadError(
                            "GoFile suggested an unsafe filename."
                        )
                    partial_path = final_path.with_name(final_path.name + ".part")
                    logger.info(
                        "GoFile browser download captured source=%s "
                        "suggested_filename=%r partial_path=%s",
                        safe_url(download.url),
                        download.suggested_filename,
                        partial_path,
                    )
                    await download.save_as(partial_path)
                    failure = await download.failure()
                    if failure:
                        raise GoFileBrowserDownloadError(
                            "The GoFile browser download failed before completion."
                        )
                    partial_path.replace(final_path)
                    _notify(notice, f"GoFile download completed: {filename}")
                    logger.info(
                        "GoFile browser download completed path=%s source=%s",
                        final_path,
                        safe_url(download.url),
                    )
                    return final_path
                except Exception as exc:
                    logger.exception(
                        "Visible GoFile browser download failed error_type=%s "
                        "error=%s hold_seconds=%.1f",
                        type(exc).__name__,
                        exc,
                        self.failure_hold_seconds,
                    )
                    if self.failure_hold_seconds > 0 and any(
                        not item.is_closed() for item in context.pages
                    ):
                        await asyncio.sleep(self.failure_hold_seconds)
                    raise
                finally:
                    await context.close()
        finally:
            if temporary_profile is not None:
                temporary_profile.cleanup()


async def _wait_for_single_download_control(
    page,
    content_id: str,
    timeout_seconds: float,
):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    next_diagnostic = loop.time()
    while loop.time() < deadline:
        if page.is_closed():
            raise GoFileBrowserDownloadError(
                "The GoFile browser window was closed before download started."
            )
        _validate_share_page(page.url, content_id)
        item_download_matches = page.locator("button.item_download")
        controls = await _visible_enabled_controls(item_download_matches)
        role_count = 0
        if not controls:
            for role in ("button", "link"):
                matches = page.get_by_role(role, name=_DOWNLOAD_NAME)
                role_count += await matches.count()
                controls.extend(await _visible_enabled_controls(matches))
        if len(controls) == 1:
            return controls[0]
        if len(controls) > 1:
            raise GoFileBrowserDownloadError(
                "The GoFile share contains multiple visible Download controls. "
                "Automatic selection is disabled to avoid downloading the wrong file."
            )
        if loop.time() >= next_diagnostic:
            challenge = await _challenge_visible(page)
            logger.info(
                "Waiting for GoFile Download control url=%s title=%r "
                "item_download_count=%d accessible_download_count=%d "
                "challenge_visible=%s remaining_seconds=%.1f",
                safe_url(page.url),
                (await page.title())[:200],
                await item_download_matches.count(),
                role_count,
                challenge,
                max(0.0, deadline - loop.time()),
            )
            next_diagnostic = loop.time() + 5.0
        await asyncio.sleep(0.5)
    raise GoFileBrowserDownloadError(
        "GoFile did not show exactly one Download control within five minutes. "
        "If verification is visible, complete it in the browser and keep the "
        "window open."
    )


async def _visible_enabled_controls(matches) -> list:
    controls = []
    for index in range(await matches.count()):
        candidate = matches.nth(index)
        if await candidate.is_visible() and await candidate.is_enabled():
            controls.append(candidate)
    return controls


async def _challenge_visible(page) -> bool:
    challenge_frame = page.locator(
        'iframe[src*="challenges.cloudflare.com"], iframe[title*="challenge" i]'
    )
    if await challenge_frame.count():
        return True
    body = (await page.locator("body").inner_text()).casefold()
    return any(
        marker in body
        for marker in (
            "verify you are human",
            "checking your browser",
            "just a moment",
            "security verification",
        )
    )


def _validate_share_page(url: str, content_id: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != GOFILE_HOST
        or parsed.path.rstrip("/") != f"/d/{content_id}"
        or parsed.username
        or parsed.password
    ):
        raise GoFileBrowserDownloadError(
            f"The browser reached an unexpected GoFile page: {safe_url(url)}"
        )


def _validate_download_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not _is_gofile_host(parsed.hostname)
        or parsed.username
        or parsed.password
        or not parsed.path
        or parsed.path == "/"
    ):
        raise GoFileBrowserDownloadError(
            f"GoFile started a download from an unexpected URL: {safe_url(url)}"
        )


def _is_gofile_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.rstrip(".").lower()
    return normalized == GOFILE_HOST or normalized.endswith(f".{GOFILE_HOST}")


def _available_download_path(destination: Path, filename: str) -> Path:
    destination = destination.resolve()
    candidate = (destination / filename).resolve()
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    index = 2
    while candidate.exists() or candidate.with_name(candidate.name + ".part").exists():
        candidate = (destination / f"{stem} ({index}){suffix}").resolve()
        index += 1
    return candidate


def _notify(callback: Callable[[str], None] | None, message: str) -> None:
    logger.info("Visible GoFile stage=%r", message)
    if callback is not None:
        callback(message)
