import json

from game_downloader.settings import AppSettings, SettingsRepository


def test_saved_settings_use_web_search_names(tmp_path):
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
