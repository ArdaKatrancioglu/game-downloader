from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from game_downloader.archive.extractor import ArchiveExtractor
from game_downloader.catalog.json_provider import LocalJsonCatalogProvider
from game_downloader.catalog.owned_html_provider import OwnedHtmlCatalogProvider
from game_downloader.download.manager import DownloadManager
from game_downloader.models import (
    DownloadProgress,
    ExtractionLimits,
    GameEntry,
    GameRelease,
    ResolvedDownload,
)
from game_downloader.settings import AppSettings, SettingsRepository
from game_downloader.storage.gofile_browser_download import GoFileBrowserDownload
from game_downloader.ui.settings_dialog import SettingsDialog
from game_downloader.ui.workers import (
    CoroutineWorker,
    DownloadWorker,
    GoFileBrowserWorker,
)


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: AppSettings,
        repository: SettingsRepository,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.repository = repository
        self.entries: list[GameEntry] = []
        self.current_release: GameRelease | None = None
        self.resolved: ResolvedDownload | None = None
        self.downloaded_path: Path | None = None
        self.active_provider = None
        self.workers: set[
            CoroutineWorker | DownloadWorker | GoFileBrowserWorker
        ] = set()
        self.download_worker: DownloadWorker | None = None
        self.setWindowTitle("Authorized Game Downloader")
        self.resize(900, 700)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        toolbar = QHBoxLayout()
        settings_button = QPushButton("Settings")
        settings_button.clicked.connect(self._open_settings)
        toolbar.addStretch()
        toolbar.addWidget(settings_button)
        layout.addLayout(toolbar)

        title = QLabel("Download authorized game archives")
        title.setStyleSheet("font-size: 22px; font-weight: 600")
        layout.addWidget(title)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter a game title")
        self.search_input.returnPressed.connect(self._search)
        self.search_button = QPushButton("Search")
        self.search_button.setMinimumHeight(42)
        self.search_button.clicked.connect(self._search)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.results = QListWidget()
        self.results.itemDoubleClicked.connect(lambda _: self._select_result())
        layout.addWidget(self.results, 1)
        self.select_button = QPushButton("Select result")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._select_result)
        layout.addWidget(self.select_button)

        details = QGroupBox("Selected archive")
        grid = QGridLayout(details)
        self.archive_label = QLabel("No archive selected")
        self.size_label = QLabel("—")
        self.destination = QLineEdit(str(self.settings.default_download_folder))
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_destination)
        self.space_label = QLabel("Available space: —")
        grid.addWidget(QLabel("Archive"), 0, 0)
        grid.addWidget(self.archive_label, 0, 1, 1, 2)
        grid.addWidget(QLabel("Size"), 1, 0)
        grid.addWidget(self.size_label, 1, 1, 1, 2)
        grid.addWidget(QLabel("Destination"), 2, 0)
        grid.addWidget(self.destination, 2, 1)
        grid.addWidget(browse, 2, 2)
        grid.addWidget(self.space_label, 3, 1, 1, 2)
        layout.addWidget(details)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QLabel("Ready")
        controls = QHBoxLayout()
        self.download_button = QPushButton("Download")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._download)
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.cancel_button = QPushButton("Cancel")
        self.pause_button.clicked.connect(self._pause)
        self.resume_button.clicked.connect(self._resume)
        self.cancel_button.clicked.connect(self._cancel)
        for button in (self.pause_button, self.resume_button, self.cancel_button):
            button.setEnabled(False)
        controls.addWidget(self.download_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.resume_button)
        controls.addWidget(self.cancel_button)
        self.extract_button = QPushButton("Extract archive")
        self.extract_button.setEnabled(False)
        self.extract_button.clicked.connect(self._extract)
        controls.addStretch()
        controls.addWidget(self.extract_button)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addLayout(controls)
        self.setCentralWidget(root)

    def _provider(self):
        if self.settings.catalog_url:
            host = urlsplit(self.settings.catalog_url).hostname
            allowed = self.settings.allowed_catalog_domains
            if not allowed and host:
                allowed = [host]
            return OwnedHtmlCatalogProvider(self.settings.catalog_url, allowed)
        return LocalJsonCatalogProvider(self.settings.catalog_file)

    def _start_worker(
        self,
        worker: CoroutineWorker | DownloadWorker | GoFileBrowserWorker,
    ) -> None:
        self.workers.add(worker)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.start()

    def _search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            QMessageBox.information(self, "Search", "Enter a game title.")
            return
        self.search_button.setEnabled(False)
        self.status.setText("Searching the authorized catalog…")
        self.active_provider = self._provider()
        worker = CoroutineWorker(lambda: self.active_provider.search(query))
        worker.succeeded.connect(self._show_results)
        worker.failed.connect(self._show_error)
        worker.finished.connect(lambda: self.search_button.setEnabled(True))
        self._start_worker(worker)

    def _show_results(self, value: object) -> None:
        self.entries = list(value)
        self.results.clear()
        for entry in self.entries:
            size = _format_bytes(entry.archive_size)
            self.results.addItem(
                QListWidgetItem(
                    f"{entry.title}  •  Version {entry.version}  •  {size}  •  {entry.source_name}"
                )
            )
        self.select_button.setEnabled(bool(self.entries))
        self.status.setText(f"{len(self.entries)} result(s)")

    def _select_result(self) -> None:
        row = self.results.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select", "Choose a search result first.")
            return
        entry = self.entries[row]
        self.status.setText("Following the selected catalog page…")

        async def prepare() -> GameRelease:
            if self.active_provider is None:
                raise LookupError("Run the catalog search again.")
            return await self.active_provider.get_release(entry.id)

        worker = CoroutineWorker(prepare)
        worker.succeeded.connect(self._catalog_release_ready)
        worker.failed.connect(self._prepare_failed)
        self._start_worker(worker)

    def _catalog_release_ready(self, value: object) -> None:
        self.current_release = value
        self.resolved = None
        self.archive_label.setText(
            f"GoFile content {self.current_release.source.content_id}"
        )
        self.size_label.setText(_format_bytes(self.current_release.archive_size))
        self.download_button.setEnabled(True)
        self._update_space()
        self.status.setText(
            "GoFile share ready. Click Download to open the controlled browser."
        )

    def _prepare_failed(self, message: str) -> None:
        self._show_error(message)

    def _choose_destination(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select download destination")
        if path:
            self.destination.setText(path)
            self._update_space()

    def _update_space(self) -> None:
        path = Path(self.destination.text()).expanduser()
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            self.space_label.setText("Available space: unavailable")
            return
        self.space_label.setText(f"Available space: {_format_bytes(free)}")

    def _download(self) -> None:
        if self.current_release is None:
            return
        folder = Path(self.destination.text()).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        expected_size = self.current_release.archive_size
        if not DownloadManager.has_recommended_space(folder, expected_size):
            answer = QMessageBox.warning(
                self,
                "Low disk space",
                "Free space is below 1.5× the expected archive size. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if not self.settings.gofile_browser_download_enabled:
            self._show_error(
                "Enable “visible GoFile browser downloads (no API)” in Settings "
                "before starting an API-free download."
            )
            return
        profile_dir = self.repository.path.parent / "browser-profile" / "gofile"
        browser_download = GoFileBrowserDownload(
            enabled=True,
            remember_session=self.settings.remember_gofile_browser_session,
            profile_dir=profile_dir,
        )
        worker = GoFileBrowserWorker(
            browser_download,
            self.current_release.source.content_id,
            folder,
        )
        worker.notice.connect(self.status.setText)
        worker.succeeded.connect(self._download_finished)
        worker.failed.connect(self._download_failed)
        worker.finished.connect(lambda: self.download_button.setEnabled(True))
        self.progress.setRange(0, 0)
        self.status.setText(
            "Opening visible GoFile Chromium. Complete verification if shown; "
            "the app will click the single Download control."
        )
        self.download_button.setEnabled(False)
        self._start_worker(worker)

    def _download_progress(self, value: DownloadProgress) -> None:
        if value.percent is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(value.percent))
        eta = "—" if value.eta_seconds is None else f"{value.eta_seconds:.0f}s"
        self.status.setText(
            f"{_format_bytes(value.downloaded)} / {_format_bytes(value.total)}  •  "
            f"{_format_bytes(int(value.bytes_per_second))}/s  •  ETA {eta}"
        )

    def _pause(self) -> None:
        if self.download_worker:
            self.download_worker.pause_download()
            self.status.setText("Paused")

    def _resume(self) -> None:
        if self.download_worker:
            self.download_worker.resume_download()

    def _cancel(self) -> None:
        if self.download_worker:
            self.download_worker.cancel_download()

    def _download_controls_off(self) -> None:
        for button in (self.pause_button, self.resume_button, self.cancel_button):
            button.setEnabled(False)
        self.download_worker = None

    def _download_finished(self, value: object) -> None:
        self.downloaded_path = Path(value)
        self.archive_label.setText(self.downloaded_path.name)
        try:
            self.size_label.setText(_format_bytes(self.downloaded_path.stat().st_size))
        except OSError:
            self.size_label.setText("Unknown")
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status.setText(f"Validated download: {self.downloaded_path}")
        self.extract_button.setEnabled(True)

    def _download_failed(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self._show_error(message)

    def _extract(self) -> None:
        if not self.downloaded_path:
            return
        answer = QMessageBox.question(
            self,
            "Extract archive",
            "Inspect and safely extract this archive now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        destination = self.downloaded_path.parent / (
            self.downloaded_path.name.split(".", 1)[0] + "-extracted"
        )
        extractor = ArchiveExtractor(
            ExtractionLimits(
                max_total_size=self.settings.max_extracted_archive_size,
                max_files=self.settings.max_extracted_file_count,
            )
        )
        worker = CoroutineWorker(
            lambda: asyncio.to_thread(extractor.extract, self.downloaded_path, destination)
        )
        worker.succeeded.connect(self._extraction_finished)
        worker.failed.connect(self._extraction_failed)
        self.extract_button.setEnabled(False)
        self.status.setText(
            "Inspecting the archive and extracting it with the available tool…"
        )
        self._start_worker(worker)

    def _extraction_failed(self, message: str) -> None:
        self.extract_button.setEnabled(True)
        self._show_error(message)

    def _extraction_finished(self, value: object) -> None:
        self.extract_button.setEnabled(False)
        self.status.setText(f"Extraction validated: {value.destination}")
        answer = QMessageBox.information(
            self,
            "Extraction complete",
            "The archive was safely extracted. Open the destination folder?",
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Close,
        )
        if answer == QMessageBox.StandardButton.Open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(value.destination)))

    def _show_error(self, message: str) -> None:
        self.status.setText("Operation stopped")
        QMessageBox.warning(self, "Unable to continue", message)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)

        def save(settings: AppSettings) -> None:
            self.settings = settings
            self.repository.save(settings)
            self.destination.setText(str(settings.default_download_folder))

        dialog.settings_saved.connect(save)
        dialog.exec()

def _format_bytes(value: int | None) -> str:
    if value is None:
        return "Unknown"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "Unknown"
