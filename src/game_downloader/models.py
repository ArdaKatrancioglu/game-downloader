from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class BrowserDownloadRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(default="Download", max_length=500)
    size: int | None = Field(default=None, ge=0)


class BrowserDirectSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["browser_direct"] = "browser_direct"
    page_url: HttpUrl
    downloads: list[BrowserDownloadRecord] = Field(default_factory=list)


class GameEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    version: str = Field(default="Unknown", max_length=100)
    description: str = Field(default="", max_length=4000)
    archive_size: int | None = Field(default=None, ge=0)
    image_url: HttpUrl | None = None
    cover_url: HttpUrl | None = None
    release_date: str | None = Field(default=None, max_length=100)
    genres: list[dict[str, object] | str] = Field(default_factory=list)
    source_name: str = "Browser Direct"
    detail_url: HttpUrl | None = None
    source: BrowserDirectSource | None = None


class GameRelease(GameEntry):
    source: BrowserDirectSource


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
    require_attachment: bool = False


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
