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
