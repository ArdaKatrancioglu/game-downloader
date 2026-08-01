from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GAME_DOWNLOADER_",
        extra="ignore",
    )

    web_search_url: str | None = None
    allowed_search_domains: list[str] = Field(default_factory=list)
    default_download_folder: Path = Path.home() / "Downloads"
    max_extracted_archive_size: int = 50 * 1024**3
    max_extracted_file_count: int = 100_000
    log_level: str = "INFO"

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_catalog_search_settings(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        if "web_search_url" not in migrated and "catalog_url" in migrated:
            migrated["web_search_url"] = migrated["catalog_url"]
        if (
            "allowed_search_domains" not in migrated
            and "allowed_catalog_domains" in migrated
        ):
            migrated["allowed_search_domains"] = migrated["allowed_catalog_domains"]
        return migrated


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
