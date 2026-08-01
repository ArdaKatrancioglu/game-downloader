import json

import pytest

from game_downloader.catalog.updater import (
    _body_preview,
    _is_cloudflare_challenge,
    _validate_catalog_browser_url,
    install_catalog_file,
    update_catalog,
)
from game_downloader.security import SecurityError


@pytest.mark.asyncio
async def test_catalog_update_validates_then_atomically_replaces_file(tmp_path):
    destination = tmp_path / "steamrip.json"
    destination.write_text('{"old": true}', encoding="utf-8")
    payload = {
        "name": "SteamRip",
        "downloads": [
            {
                "title": "Demo Free Download",
                "fileSize": "2 GB",
                "uris": ["https://gofile.io/d/demo123"],
            }
        ],
    }
    profile_dir = tmp_path / "browser-profile"
    calls = []

    async def chromium_fetcher(url, allowed_hosts, received_profile, headless):
        calls.append((url, allowed_hosts, received_profile, headless))
        return json.dumps(payload).encode()

    count = await update_catalog(
        "https://hydralinks.cloud/sources/steamrip.json",
        destination,
        ["hydralinks.cloud"],
        profile_dir=profile_dir,
        headless=False,
        fetcher=chromium_fetcher,
    )

    assert count == 1
    assert calls == [
        (
            "https://hydralinks.cloud/sources/steamrip.json",
            ["hydralinks.cloud"],
            profile_dir,
            False,
        )
    ]
    assert json.loads(destination.read_text(encoding="utf-8")) == payload
    assert not destination.with_suffix(".json.tmp").exists()


@pytest.mark.asyncio
async def test_invalid_catalog_update_preserves_existing_file(tmp_path):
    destination = tmp_path / "steamrip.json"
    destination.write_text('{"old": true}', encoding="utf-8")

    async def chromium_fetcher(url, allowed_hosts, profile_dir, headless):
        return b'{"downloads": "bad"}'

    with pytest.raises(ValueError, match="downloads"):
        await update_catalog(
            "https://hydralinks.cloud/sources/steamrip.json",
            destination,
            ["hydralinks.cloud"],
            fetcher=chromium_fetcher,
        )

    assert destination.read_text(encoding="utf-8") == '{"old": true}'


@pytest.mark.asyncio
async def test_empty_catalog_update_preserves_existing_file(tmp_path):
    destination = tmp_path / "steamrip.json"
    destination.write_text('{"old": true}', encoding="utf-8")

    async def chromium_fetcher(url, allowed_hosts, profile_dir, headless):
        return b'{"name": "SteamRip", "downloads": []}'

    with pytest.raises(ValueError, match="kullanılabilir"):
        await update_catalog(
            "https://hydralinks.cloud/sources/steamrip.json",
            destination,
            ["hydralinks.cloud"],
            fetcher=chromium_fetcher,
        )

    assert destination.read_text(encoding="utf-8") == '{"old": true}'


def test_cloudflare_challenge_is_identified_from_safe_signals():
    assert _is_cloudflare_challenge(
        403,
        {
            "server": "cloudflare",
            "cf-ray": "example",
            "content-type": "text/html",
        },
        b"<html><title>Just a moment...</title>"
        b"<script src='/cdn-cgi/challenge-platform/test'></script></html>",
    )
    assert not _is_cloudflare_challenge(
        403,
        {"server": "example"},
        b"<html>Forbidden</html>",
    )


def test_catalog_error_preview_redacts_tokens():
    preview = _body_preview(
        b'{"access_token":"secret-value","message":"Forbidden"}'
    )

    assert "secret-value" not in preview
    assert "[REDACTED]" in preview


def test_catalog_browser_allows_only_catalog_and_cloudflare_challenge_hosts():
    _validate_catalog_browser_url(
        "https://hydralinks.cloud/sources/steamrip.json",
        ["hydralinks.cloud"],
    )
    _validate_catalog_browser_url(
        "https://challenges.cloudflare.com/cdn-cgi/challenge",
        ["hydralinks.cloud"],
    )
    _validate_catalog_browser_url(
        "https://hagen.challenges.cloudflare.com/cdn-cgi/challenge",
        ["hydralinks.cloud"],
    )

    with pytest.raises(SecurityError):
        _validate_catalog_browser_url(
            "https://challenges.cloudflare.com.evil.example/test",
            ["hydralinks.cloud"],
        )


def test_local_catalog_file_can_be_imported_without_network(tmp_path):
    source = tmp_path / "downloaded.json"
    destination = tmp_path / "state" / "steamrip.json"
    source.write_text(
        json.dumps(
            {
                "name": "SteamRip",
                "downloads": [
                    {
                        "title": "Demo Free Download",
                        "fileSize": "1 GB",
                        "uris": ["https://gofile.io/d/demo123"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    count = install_catalog_file(source, destination)

    assert count == 1
    assert json.loads(destination.read_text(encoding="utf-8"))["name"] == "SteamRip"
