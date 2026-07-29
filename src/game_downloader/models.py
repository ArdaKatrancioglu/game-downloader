from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class GoFileSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["gofile"] = "gofile"
    content_id: str = Field(min_length=3, max_length=128)

    @field_validator("content_id")
    @classmethod
    def content_id_is_safe(cls, value: str) -> str:
        if not all(character.isalnum() or character in "-_" for character in value):
            raise ValueError("content_id contains unsupported characters")
        return value


class GameEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    version: str = Field(default="Unknown", max_length=100)
    description: str = Field(default="", max_length=4000)
    archive_size: int | None = Field(default=None, ge=0)
    source_name: str = "GoFile"
    detail_url: HttpUrl | None = None
    source: GoFileSource | None = None


class GameRelease(GameEntry):
    source: GoFileSource


class CatalogDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    games: list[dict]


class RemoteFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    size: int = Field(ge=0)
    mime_type: str | None = None


class ResolvedDownload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    filename: str
    size: int | None = Field(default=None, ge=0)
    mime_type: str | None = None
    url: HttpUrl
    referer: HttpUrl | None = None
    checksum_sha256: str | None = None


class DownloadProgress(BaseModel):
    downloaded: int
    total: int | None
    percent: float | None
    bytes_per_second: float
    eta_seconds: float | None


class ExtractionLimits(BaseModel):
    max_total_size: int = Field(default=50 * 1024**3, gt=0)
    max_files: int = Field(default=100_000, gt=0)
    max_compression_ratio: float = Field(default=1000.0, gt=1)


class ExtractionResult(BaseModel):
    destination: Path
    file_count: int
    total_size: int
