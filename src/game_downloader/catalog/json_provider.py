from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from game_downloader.models import CatalogDocument, GameEntry, GameRelease

logger = logging.getLogger(__name__)


class LocalJsonCatalogProvider:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._games: dict[str, GameRelease] = {}

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            document = CatalogDocument.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ValueError("The local catalog file is invalid.") from exc
        games: dict[str, GameRelease] = {}
        for index, record in enumerate(document.games):
            try:
                release = GameRelease.model_validate(record)
            except ValidationError as exc:
                logger.warning("Skipping invalid catalog record at index %d: %s", index, exc)
                continue
            games[release.id] = release
        self._games = games

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
            raise LookupError("The selected game is no longer in the catalog.") from exc

