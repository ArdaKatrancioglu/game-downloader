from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication

from game_downloader.settings import SettingsRepository
from game_downloader.ui.main_window import MainWindow


def _state_folder() -> Path:
    override = os.environ.get("GAME_DOWNLOADER_STATE_DIR")
    return Path(override) if override else Path.home() / ".authorized-game-downloader"


def _configure_logging(level: str, log_folder: Path) -> None:
    log_folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_folder / "application.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    logging.getLogger(__name__).info(
        "Application logging started level=%s log_file=%s",
        level.upper(),
        log_folder / "application.log",
    )


def main() -> int:
    state_folder = _state_folder()
    repository = SettingsRepository(state_folder / "settings.json")
    settings = repository.load()
    _configure_logging(settings.log_level, state_folder)
    application = QApplication(sys.argv)
    window = MainWindow(settings, repository)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
