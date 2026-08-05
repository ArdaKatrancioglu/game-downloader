import logging
import ssl

import httpx

from game_downloader.http_diagnostics import HttpTrace


def test_http_403_trace_includes_diagnostics_and_redacts_secrets(caplog):
    request = httpx.Request(
        "GET",
        "https://catalog.example/search?token=URL_SECRET&s=demo",
        headers={
            "Authorization": "Bearer HEADER_SECRET",
            "Cookie": "session=COOKIE_SECRET",
            "Referer": "https://catalog.example/detail?t=REFERER_SECRET",
            "User-Agent": "test-client",
        },
    )
    response = httpx.Response(
        403,
        request=request,
        headers={
            "Content-Type": "application/json",
            "Server": "edge-server",
            "CF-Ray": "trace-123",
            "Set-Cookie": "session=RESPONSE_COOKIE_SECRET",
        },
        json={
            "error": "forbidden",
            "token": "BODY_SECRET",
            "download": "https://download.example/file?token=DIRECT_SECRET",
        },
    )

    with caplog.at_level(logging.INFO):
        trace = HttpTrace(
            logging.getLogger("test.http"),
            "catalog",
            request.method,
            request.url,
            request.headers,
        )
        trace.response(response)

    output = caplog.text
    assert "operation=catalog" in output
    assert "status=403" in output
    assert "edge-server" in output
    assert "trace-123" in output
    assert "forbidden" in output
    assert "URL_SECRET" not in output
    assert "HEADER_SECRET" not in output
    assert "COOKIE_SECRET" not in output
    assert "RESPONSE_COOKIE_SECRET" not in output
    assert "BODY_SECRET" not in output
    assert "DIRECT_SECRET" not in output
    assert "REFERER_SECRET" not in output


def test_tls_transport_failure_logs_full_traceback_and_cause_chain(caplog):
    trace = HttpTrace(
        logging.getLogger("test.http"),
        "download",
        "GET",
        "https://download.example/file",
    )
    try:
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed")
        except ssl.SSLCertVerificationError as cause:
            raise httpx.ConnectError("TLS handshake failed") from cause
    except httpx.ConnectError as error:
        with caplog.at_level(logging.ERROR):
            trace.exception(error)

    output = caplog.text
    assert "Exception diagnostic operation=http-transport:download" in output
    assert "exception:ConnectError(TLS handshake failed)" in output
    assert "cause:SSLCertVerificationError" in output
    assert "certificate verify failed" in output
    assert "The above exception was the direct cause" in output
