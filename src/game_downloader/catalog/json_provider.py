from __future__ import annotations

import json
import logging
import re
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from game_downloader.models import CatalogDocument, GameEntry, GameRelease
from game_downloader.security import (
    SecurityError,
    extract_gofile_content_id,
    validate_filecrypt_container_url,
)

logger = logging.getLogger(__name__)
_FREE_DOWNLOAD = re.compile(r"\s+Free Download\b", re.IGNORECASE)
_FILE_SIZE = re.compile(
    r"^\s*(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>B|KB|MB|GB|TB)\s*$",
    re.IGNORECASE,
)
_SIZE_MULTIPLIERS = {
    "B": 1,
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
}


class _UnsupportedSource(ValueError):
    pass


class LocalJsonCatalogProvider:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._games: dict[str, GameRelease] = {}

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ValueError("Yerel katalog dosyası okunamadı veya geçersiz.") from exc
        self._games = parse_catalog_document(raw)

    def validate(self) -> int:
        self._load()
        return len(self._games)

    async def search(self, query: str) -> list[GameEntry]:
        self._load()
        needle = query.casefold().strip()
        return [
            GameEntry.model_validate(game.model_dump())
            for game in self._games.values()
            if needle in game.title.casefold()
        ]

    async def get_release(self, game_id: str) -> GameRelease:
        if not self._games:
            self._load()
        try:
            return self._games[game_id]
        except KeyError as exc:
            raise LookupError("Seçilen oyun artık katalogda bulunmuyor.") from exc


def parse_catalog_document(raw: object) -> dict[str, GameRelease]:
    if not isinstance(raw, dict):
        raise ValueError("Katalog JSON nesnesi olmalı.")
    if "downloads" in raw:
        return _parse_hydra_catalog(raw)
    return _parse_native_catalog(raw)


def _parse_native_catalog(raw: dict) -> dict[str, GameRelease]:
    try:
        document = CatalogDocument.model_validate(raw)
    except ValidationError as exc:
        raise ValueError("Yerel katalog biçimi geçersiz.") from exc
    games: dict[str, GameRelease] = {}
    for index, record in enumerate(document.games):
        try:
            release = GameRelease.model_validate(record)
        except ValidationError as exc:
            logger.warning("Skipping invalid catalog record at index %d: %s", index, exc)
            continue
        games[release.id] = release
    return games


def _parse_hydra_catalog(raw: dict) -> dict[str, GameRelease]:
    downloads = raw.get("downloads")
    if not isinstance(downloads, list):
        raise ValueError("Hydra kataloğunda downloads listesi bulunamadı.")

    games: dict[str, GameRelease] = {}
    for index, record in enumerate(downloads):
        try:
            release = _hydra_release(record)
        except _UnsupportedSource:
            logger.debug(
                "Skipping Hydra catalog record without a GoFile source at index %d.",
                index,
            )
            continue
        except (TypeError, ValueError, ValidationError, SecurityError) as exc:
            logger.warning(
                "Skipping invalid Hydra catalog record at index %d: %s",
                index,
                exc,
            )
            continue
        games[release.id] = release
    if downloads and not games:
        raise ValueError("Hydra kataloğunda desteklenen bir GoFile kaydı yok.")
    return games


def _hydra_release(record: object) -> GameRelease:
    if not isinstance(record, dict):
        raise TypeError("download kaydı bir JSON nesnesi olmalı")
    raw_title = record.get("title")
    if not isinstance(raw_title, str) or not raw_title.strip():
        raise ValueError("oyun adı eksik")
    title, version = _split_hydra_title(raw_title)
    uris = record.get("uris")
    if not isinstance(uris, list):
        raise ValueError("bağlantı listesi eksik")

    content_id = None
    filecrypt_url = None
    for uri in uris:
        if not isinstance(uri, str):
            continue
        try:
            content_id = extract_gofile_content_id(uri)
            break
        except SecurityError:
            try:
                filecrypt_url = validate_filecrypt_container_url(uri)
            except SecurityError:
                continue
    if content_id is None and filecrypt_url is None:
        raise _UnsupportedSource("desteklenen bir GoFile bağlantısı yok")

    upload_date = record.get("uploadDate")
    description = (
        f"Katalog tarihi: {upload_date}"
        if isinstance(upload_date, str) and upload_date
        else ""
    )
    if content_id is not None:
        source = {"type": "gofile", "content_id": content_id}
        game_id = f"hydra-{content_id}"
        source_name = "GoFile"
    else:
        assert filecrypt_url is not None
        digest = sha256(filecrypt_url.encode("utf-8")).hexdigest()[:20]
        source = {"type": "filecrypt", "url": filecrypt_url}
        game_id = f"hydra-filecrypt-{digest}"
        source_name = "FileCrypt"
    return GameRelease(
        id=game_id,
        title=title,
        version=version,
        description=description,
        archive_size=_parse_file_size(record.get("fileSize")),
        source_name=source_name,
        source=source,
    )


def _split_hydra_title(value: str) -> tuple[str, str]:
    normalized = " ".join(value.split())
    marker = _FREE_DOWNLOAD.search(normalized)
    if marker is None:
        return normalized, "Unknown"
    title = normalized[: marker.start()].strip()
    suffix = normalized[marker.end() :].strip()
    if suffix.startswith("(") and suffix.endswith(")"):
        suffix = suffix[1:-1].strip()
    return title or normalized, suffix or "Unknown"


def _parse_file_size(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = _FILE_SIZE.fullmatch(value.replace("\xa0", " "))
    if match is None:
        return None
    amount = float(match.group("amount").replace(",", "."))
    return int(amount * _SIZE_MULTIPLIERS[match.group("unit").upper()])
