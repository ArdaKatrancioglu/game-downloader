import json

from game_downloader.settings import AppSettings, SettingsRepository


def test_legacy_catalog_search_settings_are_migrated():
    settings = AppSettings.model_validate(
        {
            "catalog_url": "https://search.example/",
            "allowed_catalog_domains": ["search.example"],
        }
    )

    assert settings.web_search_url == "https://search.example/"
    assert settings.allowed_search_domains == ["search.example"]
    assert settings.fuckingfast_part_delay_min_seconds == 15
    assert settings.fuckingfast_part_delay_max_seconds == 30


def test_saved_settings_use_web_search_names_and_drop_catalog_fields(tmp_path):
    path = tmp_path / "settings.json"
    SettingsRepository(path).save(
        AppSettings(
            web_search_url="https://search.example/",
            allowed_search_domains=["search.example"],
            fuckingfast_part_delay_min_seconds=15,
            fuckingfast_part_delay_max_seconds=30,
        )
    )

    payload = json.loads(path.read_text())
    assert payload["web_search_url"] == "https://search.example/"
    assert payload["allowed_search_domains"] == ["search.example"]
    assert payload["fuckingfast_part_delay_min_seconds"] == 15
    assert payload["fuckingfast_part_delay_max_seconds"] == 30
    assert all("catalog" not in key for key in payload)
