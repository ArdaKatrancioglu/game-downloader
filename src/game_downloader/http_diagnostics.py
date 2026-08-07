from __future__ import annotations

import logging
import re
from time import monotonic

import httpx

from game_downloader.error_diagnostics import log_exception
from game_downloader.security import redact_url

_SECRET_HEADERS = {"authorization", "cookie", "proxy-authorization", "set-cookie"}
_USEFUL_RESPONSE_HEADERS = {
    "cf-ray",
    "content-length",
    "content-type",
    "location",
    "retry-after",
    "server",
    "via",
    "x-request-id",
}


def safe_url(value: str | httpx.URL) -> str:
    return redact_url(str(value))


def safe_headers(
    headers: httpx.Headers,
    *,
    response: bool = False,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers.multi_items():
        lowered = key.lower()
        if response and lowered not in _USEFUL_RESPONSE_HEADERS:
            continue
        if lowered in _SECRET_HEADERS:
            result[key] = "[REDACTED]"
        elif lowered in {"referer", "referrer"}:
            result[key] = safe_url(value)
        else:
            result[key] = value
    return result


def response_preview(response: httpx.Response, limit: int = 1000) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if not any(kind in content_type for kind in ("json", "text", "html", "xml")):
        return "<non-text response omitted>"
    try:
        value = response.text
    except (httpx.ResponseNotRead, UnicodeError):
        return "<response body unavailable>"
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
    value = re.sub(
        r'(?i)(["\']?(?:access_?token|api_?key|token)["\']?\s*[:=]\s*["\']?)'
        r"[^\"'\s&,}<]+",
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)([?&](?:t|v)=)[^\"'\s&<>]+",
        r"\1[REDACTED]",
        value,
    )
    compact = " ".join(value.split())
    return compact[:limit] + ("…" if len(compact) > limit else "")


def is_cloudflare_challenge(response: httpx.Response) -> bool:
    if response.status_code not in {403, 503}:
        return False
    server = response.headers.get("server", "").lower()
    edge_signal = server == "cloudflare" or bool(response.headers.get("cf-ray"))
    if not edge_signal:
        return False
    try:
        body = response.text.lower()
    except (httpx.ResponseNotRead, UnicodeError):
        body = ""
    markers = (
        "<title>just a moment...</title>",
        "challenges.cloudflare.com",
        "/cdn-cgi/challenge-platform/",
        "cf-chl-",
    )
    content_type = response.headers.get("content-type", "").lower()
    return any(marker in body for marker in markers) or "text/html" in content_type


class HttpTrace:
    def __init__(
        self,
        logger: logging.Logger,
        operation: str,
        method: str,
        url: str | httpx.URL,
        headers: httpx.Headers | dict[str, str] | None = None,
    ) -> None:
        self.logger = logger
        self.operation = operation
        self.method = method.upper()
        self.url = safe_url(url)
        self.started = monotonic()
        request_headers = httpx.Headers(headers or {})
        logger.info(
            "HTTP request operation=%s method=%s url=%s headers=%s",
            operation,
            self.method,
            self.url,
            safe_headers(request_headers),
        )

    def response(self, response: httpx.Response, *, include_error_body: bool = True) -> None:
        elapsed_ms = round((monotonic() - self.started) * 1000)
        self.logger.info(
            "HTTP response operation=%s method=%s url=%s status=%d elapsed_ms=%d "
            "http_version=%s headers=%s",
            self.operation,
            self.method,
            safe_url(response.url),
            response.status_code,
            elapsed_ms,
            response.http_version,
            safe_headers(response.headers, response=True),
        )
        if include_error_body and response.is_error:
            self.logger.warning(
                "HTTP error detail operation=%s status=%d body_preview=%r",
                self.operation,
                response.status_code,
                response_preview(response),
            )

    def exception(self, exc: Exception) -> None:
        elapsed_ms = round((monotonic() - self.started) * 1000)
        self.logger.warning(
            "HTTP transport failure operation=%s method=%s url=%s elapsed_ms=%d "
            "error_type=%s error=%s",
            self.operation,
            self.method,
            self.url,
            elapsed_ms,
            type(exc).__name__,
            exc,
        )
        log_exception(
            self.logger,
            f"http-transport:{self.operation}",
            exc,
        )
