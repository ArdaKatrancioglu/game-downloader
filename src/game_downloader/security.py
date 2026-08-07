from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class SecurityError(ValueError):
    """Raised when an external value violates an explicit security boundary."""


_TOKEN_PATTERN = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)(\S+)")
_SENSITIVE_QUERY = {
    "t",
    "v",
    "token",
    "password",
    "auth",
    "key",
    "apikey",
    "api_key",
    "signature",
    "sig",
}
_LONG_SECRET_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]{64,}$")
_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)


def normalized_host(host: str | None) -> str:
    if not host:
        raise SecurityError("The URL does not contain a host.")
    return host.rstrip(".").lower().encode("idna").decode("ascii")


def validate_https_url(
    url: str,
    allowed_hosts: Iterable[str],
    *,
    allow_local_http: bool = False,
) -> str:
    parsed = urlsplit(url)
    host = normalized_host(parsed.hostname)
    allowed = {normalized_host(item) for item in allowed_hosts}
    local_test = allow_local_http and host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local_test):
        raise SecurityError("Only HTTPS URLs are allowed.")
    if parsed.username or parsed.password:
        raise SecurityError("Credentials in URLs are not allowed.")
    if host not in allowed:
        raise SecurityError(f"Unexpected domain: {host}")
    return url


def safe_folder_name(name: str) -> str:
    candidate = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip().rstrip(". ")
    if not candidate or candidate in {".", ".."}:
        raise SecurityError("İçerik adı klasör olarak kullanılamıyor.")
    return candidate[:120].rstrip(". ")


def is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global


async def ensure_public_host(host: str) -> None:
    try:
        infos = await __import__("asyncio").get_running_loop().run_in_executor(
            None,
            lambda: socket.getaddrinfo(host, None, type=socket.SOCK_STREAM),
        )
    except socket.gaierror as exc:
        raise SecurityError("The download host could not be resolved.") from exc
    addresses = {item[4][0] for item in infos}
    if not addresses or any(not is_public_ip(address) for address in addresses):
        raise SecurityError("The download URL resolves to a local or private network.")


def safe_filename(name: str) -> str:
    candidate = name.replace("\\", "/").split("/")[-1].strip()
    if not candidate or candidate in {".", ".."} or "\x00" in candidate:
        raise SecurityError("The remote filename is invalid.")
    return candidate


def redact_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    path = "/".join(
        "<redacted>" if _LONG_SECRET_PATH_SEGMENT.fullmatch(part) else part
        for part in parsed.path.split("/")
    )
    query = [
        (key, "<redacted>" if key.lower() in _SENSITIVE_QUERY else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(query), ""))


def redact_diagnostic(value: str) -> str:
    redacted = _TOKEN_PATTERN.sub(r"\1<redacted>", value)
    return _URL_PATTERN.sub(lambda match: redact_url(match.group(0)), redacted)
