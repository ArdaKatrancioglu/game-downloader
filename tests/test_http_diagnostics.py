import logging

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
