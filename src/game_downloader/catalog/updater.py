from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from game_downloader.catalog.json_provider import parse_catalog_document
from game_downloader.http_diagnostics import safe_url
from game_downloader.security import (
    SecurityError,
    normalized_host,
    validate_https_url,
)

logger = logging.getLogger(__name__)
MAX_CATALOG_BYTES = 10 * 1024 * 1024
CatalogFetcher = Callable[[str, list[str], Path | None, bool], Awaitable[bytes]]
_CHALLENGE_HOST = "challenges.cloudflare.com"
_USEFUL_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "referer",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "user-agent",
}
_USEFUL_RESPONSE_HEADERS = {
    "cf-mitigated",
    "cf-ray",
    "content-length",
    "content-type",
    "location",
    "retry-after",
    "server",
}


async def update_catalog(
    url: str,
    destination: Path,
    allowed_hosts: list[str],
    *,
    profile_dir: Path | None = None,
    headless: bool = True,
    fetcher: CatalogFetcher | None = None,
) -> int:
    validate_https_url(url, allowed_hosts)
    logger.info(
        "Catalog update started url=%s destination=%s headless=%s profile=%s",
        safe_url(url),
        destination,
        headless,
        profile_dir,
    )
    catalog_fetcher = fetcher or _fetch_catalog_with_chromium
    try:
        content = await catalog_fetcher(
            url,
            allowed_hosts,
            profile_dir,
            headless,
        )
    except Exception:
        logger.exception(
            "Catalog update fetch failed url=%s headless=%s",
            safe_url(url),
            headless,
        )
        raise
    game_count = _validate_and_store_catalog(content, destination)
    logger.info(
        "Catalog update completed url=%s destination=%s games=%d bytes=%d",
        safe_url(url),
        destination,
        game_count,
        len(content),
    )
    return game_count


def install_catalog_file(source: Path, destination: Path) -> int:
    try:
        content = source.read_bytes()
    except OSError as exc:
        raise ValueError("Seçilen katalog dosyası okunamadı.") from exc
    game_count = _validate_and_store_catalog(content, destination)
    logger.info(
        "Local catalog imported source=%s destination=%s games=%d bytes=%d",
        source,
        destination,
        game_count,
        len(content),
    )
    return game_count


def _validate_and_store_catalog(content: bytes, destination: Path) -> int:
    if len(content) > MAX_CATALOG_BYTES:
        raise ValueError("Katalog dosyası izin verilen boyutu aşıyor.")
    try:
        raw = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Geçerli bir JSON kataloğu bulunamadı.") from exc
    game_count = len(parse_catalog_document(raw))
    if game_count == 0:
        raise ValueError("Katalogda kullanılabilir bir oyun bulunamadı.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(destination)
    return game_count


async def _fetch_catalog_with_chromium(
    url: str,
    allowed_hosts: list[str],
    profile_dir: Path | None,
    headless: bool,
) -> bytes:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Chromium bileşeni eksik. `uv sync --extra browser` ve "
            "`uv run playwright install chromium` komutlarını çalıştır."
        ) from exc

    if profile_dir is None:
        raise ValueError("Katalog tarayıcı profil klasörü kullanılamıyor.")
    await asyncio.to_thread(profile_dir.mkdir, parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        logger.info(
            "Launching catalog Chromium headless=%s profile=%s",
            headless,
            profile_dir,
        )
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(profile_dir),
                headless=headless,
                accept_downloads=False,
            )
        except Exception as exc:
            raise RuntimeError(
                "Katalog güncellemesi için Chromium açılamadı."
            ) from exc
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            document_responses = []

            def log_document_response(response) -> None:
                if response.request.resource_type != "document":
                    return
                document_responses.append(response)
                logger.info(
                    "Catalog Chromium response url=%s status=%d "
                    "request_headers=%s response_headers=%s",
                    safe_url(response.url),
                    response.status,
                    _selected_headers(
                        response.request.headers,
                        _USEFUL_REQUEST_HEADERS,
                    ),
                    _selected_headers(
                        response.headers,
                        _USEFUL_RESPONSE_HEADERS,
                    ),
                )

            def log_failed_request(request) -> None:
                if request.resource_type == "document":
                    logger.warning(
                        "Catalog Chromium request failed url=%s failure=%s",
                        safe_url(request.url),
                        request.failure,
                    )

            page.on("response", log_document_response)
            page.on("requestfailed", log_failed_request)
            page.on(
                "pageerror",
                lambda error: logger.warning(
                    "Catalog Chromium page error type=%s error=%s",
                    type(error).__name__,
                    error,
                ),
            )

            async def restrict_request(route) -> None:
                request = route.request
                try:
                    _validate_catalog_browser_url(request.url, allowed_hosts)
                except SecurityError:
                    logger.warning(
                        "Catalog Chromium blocked external request "
                        "resource_type=%s url=%s",
                        request.resource_type,
                        safe_url(request.url),
                    )
                    await route.abort()
                    return
                await route.continue_()

            await page.route("**/*", restrict_request)
            try:
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
            except Exception as exc:
                logger.exception(
                    "Catalog Chromium navigation failed url=%s",
                    safe_url(url),
                )
                raise RuntimeError(
                    "Katalog adresi Chromium ile açılamadı."
                ) from exc
            if response is None:
                raise RuntimeError("Chromium katalog yanıtı döndürmedi.")
            validate_https_url(response.url, allowed_hosts)
            content = await response.body()
            if not response.ok:
                if _is_cloudflare_challenge(
                    response.status,
                    response.headers,
                    content,
                ):
                    return await _wait_for_cloudflare_verification(
                        page,
                        response,
                        document_responses,
                        allowed_hosts,
                        headless,
                        content,
                    )
                logger.warning(
                    "Catalog Chromium HTTP error url=%s status=%d "
                    "headers=%s body_preview=%r",
                    safe_url(response.url),
                    response.status,
                    _selected_headers(
                        response.headers,
                        _USEFUL_RESPONSE_HEADERS,
                    ),
                    _body_preview(content),
                )
                raise RuntimeError(
                    f"Katalog sunucusu HTTP {response.status} döndürdü. "
                    "Ayrıntılar application.log dosyasına yazıldı."
                )
            if len(content) > MAX_CATALOG_BYTES:
                raise ValueError("Katalog dosyası izin verilen boyutu aşıyor.")
            return content
        finally:
            await context.close()


async def _wait_for_cloudflare_verification(
    page,
    initial_response,
    document_responses: list,
    allowed_hosts: list[str],
    headless: bool,
    initial_content: bytes,
) -> bytes:
    timeout_seconds = 20.0 if headless else 180.0
    logger.warning(
        "Cloudflare challenge detected url=%s status=%d headless=%s "
        "wait_seconds=%.0f headers=%s body_preview=%r",
        safe_url(initial_response.url),
        initial_response.status,
        headless,
        timeout_seconds,
        _selected_headers(
            initial_response.headers,
            _USEFUL_RESPONSE_HEADERS,
        ),
        _body_preview(initial_content),
    )
    deadline = monotonic() + timeout_seconds
    inspected: set[int] = {id(initial_response)}
    while monotonic() < deadline:
        if page.is_closed():
            break
        for candidate in reversed(document_responses):
            marker = id(candidate)
            if marker in inspected:
                continue
            inspected.add(marker)
            try:
                validate_https_url(candidate.url, allowed_hosts)
            except SecurityError:
                continue
            if not candidate.ok:
                continue
            content = await candidate.body()
            if len(content) > MAX_CATALOG_BYTES:
                raise ValueError("Katalog dosyası izin verilen boyutu aşıyor.")
            logger.info(
                "Cloudflare verification completed url=%s status=%d bytes=%d",
                safe_url(candidate.url),
                candidate.status,
                len(content),
            )
            return content
        await asyncio.sleep(0.25)

    logger.warning(
        "Cloudflare verification timed out url=%s headless=%s final_url=%s",
        safe_url(initial_response.url),
        headless,
        safe_url(page.url),
    )
    ray_id = initial_response.headers.get("cf-ray", "bilinmiyor")
    if headless:
        raise RuntimeError(
            "Cloudflare güvenlik doğrulaması headless Chromium'da tamamlanamadı. "
            "Ayarlar'dan 'Chromium'u arka planda çalıştır' seçeneğini kapatıp "
            f"yeniden deneyin. Ray ID: {ray_id}. Ayrıntılar application.log "
            "dosyasına yazıldı."
        )
    raise RuntimeError(
        "Cloudflare güvenlik doğrulaması üç dakika içinde tamamlanmadı. "
        f"Ray ID: {ray_id}. Ayrıntılar application.log dosyasına yazıldı."
    )


def _is_cloudflare_challenge(
    status: int,
    headers: dict[str, str],
    content: bytes,
) -> bool:
    if status not in {403, 503}:
        return False
    lowered_headers = {key.lower(): value for key, value in headers.items()}
    edge_signal = (
        lowered_headers.get("server", "").lower() == "cloudflare"
        or bool(lowered_headers.get("cf-ray"))
    )
    if not edge_signal:
        return False
    text = content[:100_000].decode("utf-8", "replace").casefold()
    return any(
        marker in text
        for marker in (
            "<title>just a moment...</title>",
            "challenges.cloudflare.com",
            "/cdn-cgi/challenge-platform/",
            "cf-chl-",
        )
    )


def _selected_headers(
    headers: dict[str, str],
    allowed: set[str],
) -> dict[str, str]:
    return {
        key.lower(): value
        for key, value in headers.items()
        if key.lower() in allowed
    }


def _validate_catalog_browser_url(
    url: str,
    allowed_hosts: list[str],
) -> None:
    try:
        validate_https_url(url, allowed_hosts)
        return
    except SecurityError as original_error:
        parsed = urlsplit(url)
        host = normalized_host(parsed.hostname)
        if (
            host != _CHALLENGE_HOST
            and not host.endswith(f".{_CHALLENGE_HOST}")
        ):
            raise original_error
        validate_https_url(url, [host])


def _body_preview(content: bytes, limit: int = 1000) -> str:
    text = content.decode("utf-8", "replace")
    text = re.sub(
        r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r'(?i)(["\']?(?:access_?token|api_?key|token)["\']?\s*[:=]\s*["\']?)'
        r"[^\"'\s&,}<]+",
        r"\1[REDACTED]",
        text,
    )
    compact = " ".join(text.split())
    return compact[:limit] + ("…" if len(compact) > limit else "")
