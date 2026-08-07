import pytest

from game_downloader.security import (
    SecurityError,
    redact_diagnostic,
    redact_url,
    validate_https_url,
)


def test_url_allowlist_is_exact():
    assert (
        validate_https_url("https://catalog.example/path", ["catalog.example"])
        == "https://catalog.example/path"
    )
    with pytest.raises(SecurityError):
        validate_https_url("https://catalog.example.evil.test/path", ["catalog.example"])


def test_redaction_removes_bearer_and_sensitive_query():
    text = redact_diagnostic("Authorization: Bearer SECRET")
    assert "SECRET" not in text
    url = redact_diagnostic("https://example.test/a?token=SECRET&safe=yes")
    assert "SECRET" not in url
    assert "safe=yes" in url


def test_redaction_removes_temporary_download_token():
    url = redact_diagnostic(
        "https://download.example/file?token=temporary-secret"
    )
    assert "temporary-secret" not in url
    assert "token=%3Credacted%3E" in url


def test_redaction_removes_signed_download_path_and_query():
    path_token = "aB_9-" * 20
    redacted = redact_url(
        f"https://download.example/download/{path_token}?sig=secret&safe=yes"
    )
    assert path_token not in redacted
    assert "secret" not in redacted
    assert "safe=yes" in redacted
