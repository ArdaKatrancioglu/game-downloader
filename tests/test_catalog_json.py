import json

import pytest

from game_downloader.catalog.json_provider import LocalJsonCatalogProvider


@pytest.mark.asyncio
async def test_json_catalog_skips_invalid_records(tmp_path, caplog):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "games": [
                    {
                        "id": "demo",
                        "title": "Demo Game",
                        "version": "1",
                        "archive_size": 12,
                        "source": {"type": "gofile", "content_id": "own123"},
                    },
                    {"id": "", "title": "Broken"},
                ]
            }
        )
    )
    provider = LocalJsonCatalogProvider(path)
    results = await provider.search("demo")
    assert [item.title for item in results] == ["Demo Game"]
    assert (await provider.get_release("demo")).source.content_id == "own123"
    assert "Skipping invalid catalog record" in caplog.text


@pytest.mark.asyncio
async def test_hydra_catalog_parses_gofile_records_and_skips_other_hosts(tmp_path):
    path = tmp_path / "steamrip.json"
    path.write_text(
        json.dumps(
            {
                "name": "SteamRip",
                "downloads": [
                    {
                        "title": "Demo Game Free Download (v1.2 + Online)",
                        "uploadDate": "2026-07-17T13:12:08+00:00",
                        "fileSize": "1.5\u00a0GB",
                        "uris": [
                            "https://gofile.io/d/demo123",
                            "https://other.example/file",
                        ],
                    },
                    {
                        "title": "Unsupported Free Download",
                        "fileSize": "10 MB",
                        "uris": ["https://other.example/file"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    provider = LocalJsonCatalogProvider(path)
    results = await provider.search("demo")
    release = await provider.get_release(results[0].id)

    assert len(results) == 1
    assert release.title == "Demo Game"
    assert release.version == "v1.2 + Online"
    assert release.archive_size == int(1.5 * 1024**3)
    assert release.source.content_id == "demo123"


@pytest.mark.asyncio
async def test_hydra_catalog_keeps_filecrypt_container_when_gofile_is_absent(tmp_path):
    path = tmp_path / "steamrip.json"
    path.write_text(
        json.dumps(
            {
                "name": "SteamRip",
                "downloads": [
                    {
                        "title": "Container Game Free Download",
                        "fileSize": "4 GB",
                        "uris": [
                            "https://www.filecrypt.cc/Container/AB5EBFD7FB.html"
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    provider = LocalJsonCatalogProvider(path)
    results = await provider.search("container game")
    release = await provider.get_release(results[0].id)

    assert release.source.type == "filecrypt"
    assert str(release.source.url) == (
        "https://www.filecrypt.cc/Container/AB5EBFD7FB.html"
    )
    assert release.source_name == "FileCrypt"
