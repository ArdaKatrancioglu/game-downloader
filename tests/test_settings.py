import json

from game_downloader.settings import AppSettings, SettingsRepository, system_download_folder


def test_default_download_folder_is_the_os_downloads_folder():
    assert system_download_folder().name == "Downloads"


def test_saved_settings_use_web_search_names(tmp_path):
    path = tmp_path / "settings.json"
    SettingsRepository(path).save(
        AppSettings(
            web_search_url="https://search.example/",
            allowed_search_domains=["search.example"],
        )
    )

    payload = json.loads(path.read_text())
    assert payload["web_search_url"] == "https://search.example/"
    assert payload["allowed_search_domains"] == ["search.example"]


def test_auto_extract_zip_setting_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    repository.save(AppSettings(auto_extract_zip=True))

    assert repository.load().auto_extract_zip is True


def test_on_demand_zip_extraction_setting_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    repository.save(AppSettings(on_demand_zip_extraction=False))

    assert repository.load().on_demand_zip_extraction is False


def test_download_attempt_count_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    repository = SettingsRepository(path)
    repository.save(AppSettings(download_max_attempts=7))

    assert repository.load().download_max_attempts == 7
