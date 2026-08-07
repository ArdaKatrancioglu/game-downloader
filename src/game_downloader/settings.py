from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def system_download_folder() -> Path:
    """Return the user's configured Downloads known folder on Windows/macOS."""
    if sys.platform == "win32":
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            folder_id = "{374DE290-123F-4565-9164-39C4925E467B}"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _ = winreg.QueryValueEx(key, folder_id)
            return Path(os.path.expandvars(value)).expanduser()
        except (OSError, TypeError, ValueError):
            logger.warning("Windows Downloads known folder could not be read; using fallback")
    return Path.home() / "Downloads"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GAME_DOWNLOADER_",
        extra="ignore",
    )

    web_search_url: str = "https://ankergames.net/"
    allowed_search_domains: list[str] = Field(default_factory=list)
    default_download_folder: Path = Field(default_factory=system_download_folder)
    chrome_executable_path: Path | None = None
    browser_headless: bool = True
    browser_timeout_seconds: float = Field(default=30.0, ge=1, le=300)
    auto_extract_zip: bool = True
    download_max_attempts: int = Field(default=3, ge=1, le=10)
    max_extracted_archive_size: int = 200 * 1024**3
    max_extracted_file_count: int = 100_000
    log_level: str = "INFO"

class SettingsRepository:
    """Persist application settings."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path.home() / ".authorized-game-downloader" / "settings.json"

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            return AppSettings.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            logger.warning("Ignoring invalid application settings: %s", exc)
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = settings.model_dump(mode="json")
        # This allowlist makes it impossible to accidentally serialize a future secret field.
        allowed = set(AppSettings.model_fields)
        clean = {key: payload[key] for key in allowed}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        temporary.replace(self.path)
