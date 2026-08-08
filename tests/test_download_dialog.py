from game_downloader.archive.http_range import DEFAULT_DISK_CACHE_BYTES
from game_downloader.ui.download_dialog import _required_download_space


def test_on_demand_initial_space_check_only_requires_metadata_workspace() -> None:
    assert _required_download_space(
        100 * 1024**3,
        auto_extract=True,
        on_demand_extract=True,
    ) == DEFAULT_DISK_CACHE_BYTES


def test_normal_extract_keeps_archive_plus_extraction_estimate() -> None:
    assert _required_download_space(
        10 * 1024**3,
        auto_extract=True,
        on_demand_extract=False,
    ) == 25 * 1024**3


def test_prepared_on_demand_dialog_uses_exact_extracted_size() -> None:
    assert _required_download_space(
        100 * 1024**3,
        auto_extract=True,
        on_demand_extract=True,
        exact_extracted_size=130 * 1024**3,
    ) == 130 * 1024**3 + DEFAULT_DISK_CACHE_BYTES
