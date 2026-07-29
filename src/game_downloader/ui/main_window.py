from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
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
    ExtractionLimits,
    GameEntry,
    GameRelease,
)
from game_downloader.settings import AppSettings, SettingsRepository
from game_downloader.storage.gofile_browser_download import GoFileBrowserDownload
from game_downloader.ui.settings_dialog import SettingsDialog
from game_downloader.ui.theme import load_theme
from game_downloader.ui.workers import (
    CoroutineWorker,
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
        self.downloaded_path: Path | None = None
        self.extracted_path: Path | None = None
        self.active_provider = None
        self.workers: set[CoroutineWorker | GoFileBrowserWorker] = set()
        self.theme_path = self.repository.path.parent / "theme.json"
        self.setWindowTitle("Oyun İndirici")
        self.setMinimumSize(940, 720)
        self.resize(1080, 800)
        self.setStyleSheet(load_theme(self.theme_path))
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(30, 24, 30, 28)
        layout.setSpacing(14)

        header = QFrame()
        header.setObjectName("headerCard")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Oyun İndirici")
        title.setObjectName("pageTitle")
        header_layout.addWidget(title)
        header_layout.addStretch()
        theme_button = QPushButton("Tema")
        theme_button.setObjectName("quietButton")
        theme_button.clicked.connect(self._open_theme)
        logs_button = QPushButton("Kayıtlar")
        logs_button.setObjectName("quietButton")
        logs_button.clicked.connect(self._open_logs)
        settings_button = QPushButton("Ayarlar")
        settings_button.setObjectName("quietButton")
        settings_button.clicked.connect(self._open_settings)
        header_layout.addWidget(theme_button)
        header_layout.addWidget(logs_button)
        header_layout.addWidget(settings_button)
        layout.addWidget(header)

        search_card = QFrame()
        search_card.setProperty("class", "card")
        search_layout = QVBoxLayout(search_card)
        search_layout.setContentsMargins(18, 14, 18, 18)
        search_layout.setSpacing(10)
        search_title = QLabel("Oyun ara")
        search_title.setObjectName("sectionTitle")
        search_layout.addWidget(search_title)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Oyun adı")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.returnPressed.connect(self._search)
        self.search_button = QPushButton("Ara")
        self.search_button.setObjectName("primaryButton")
        self.search_button.setMinimumHeight(42)
        self.search_button.clicked.connect(self._search)
        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.search_button)
        search_layout.addLayout(search_row)
        layout.addWidget(search_card)

        results_title = QLabel("Sonuçlar")
        results_title.setObjectName("sectionTitle")
        layout.addWidget(results_title)
        self.results = QListWidget()
        self.results.setSpacing(2)
        self.results.itemDoubleClicked.connect(lambda _: self._select_result())
        self.results.currentRowChanged.connect(
            lambda row: self.select_button.setEnabled(row >= 0)
        )
        layout.addWidget(self.results, 1)
        self.select_button = QPushButton("İndir")
        self.select_button.setObjectName("primaryButton")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._select_result)
        layout.addWidget(self.select_button)

        details = QGroupBox("Seçilen")
        details.setMinimumHeight(165)
        grid = QGridLayout(details)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self.archive_label = QLabel("—")
        self.size_label = QLabel("—")
        self.destination = QLineEdit(str(self.settings.default_download_folder))
        browse = QPushButton("Değiştir")
        browse.clicked.connect(self._choose_destination)
        self.space_label = QLabel("Boş alan: —")
        self.space_label.setObjectName("mutedLabel")
        grid.addWidget(QLabel("Oyun"), 0, 0)
        grid.addWidget(self.archive_label, 0, 1, 1, 2)
        grid.addWidget(QLabel("Boyut"), 1, 0)
        grid.addWidget(self.size_label, 1, 1, 1, 2)
        grid.addWidget(QLabel("Klasör"), 2, 0)
        grid.addWidget(self.destination, 2, 1)
        grid.addWidget(browse, 2, 2)
        grid.addWidget(self.space_label, 3, 1, 1, 2)
        layout.addWidget(details)
        layout.addSpacing(6)

        activity_title = QLabel("Durum")
        activity_title.setObjectName("sectionTitle")
        layout.addWidget(activity_title)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QLabel("Hazır")
        self.status.setObjectName("statusText")
        self.status.setWordWrap(True)
        controls = QHBoxLayout()
        self.extract_button = QPushButton("Tekrar çıkar")
        self.extract_button.setEnabled(False)
        self.extract_button.clicked.connect(self._extract)
        self.open_folder_button = QPushButton("Klasörü aç")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self._open_extracted_folder)
        controls.addStretch()
        controls.addWidget(self.extract_button)
        controls.addWidget(self.open_folder_button)
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
        worker: CoroutineWorker | GoFileBrowserWorker,
    ) -> None:
        self.workers.add(worker)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.start()

    def _search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            self.status.setText("Oyun adı yaz.")
            self.search_input.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.search_button.setEnabled(False)
        self.status.setText("Aranıyor…")
        self.active_provider = self._provider()
        worker = CoroutineWorker(lambda: self.active_provider.search(query))
        worker.succeeded.connect(self._show_results)
        worker.failed.connect(self._show_error)
        worker.finished.connect(lambda: self.search_button.setEnabled(True))
        self._start_worker(worker)

    def _show_results(self, value: object) -> None:
        self.entries = list(value)
        self.results.setEnabled(True)
        self.results.clear()
        for entry in self.entries:
            size = _format_bytes(entry.archive_size)
            self.results.addItem(
                QListWidgetItem(
                    f"{entry.title}  •  {entry.version}  •  {size}"
                )
            )
        self.select_button.setEnabled(bool(self.entries))
        if self.entries:
            self.results.setCurrentRow(0)
        self.status.setText(f"{len(self.entries)} sonuç")

    def _select_result(self) -> None:
        row = self.results.currentRow()
        if row < 0:
            self.status.setText("Bir sonuç seç.")
            return
        entry = self.entries[row]
        self.search_button.setEnabled(False)
        self.select_button.setEnabled(False)
        self.results.setEnabled(False)
        self.status.setText("Bağlantı bulunuyor…")

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
        self.archive_label.setText(
            f"{self.current_release.title} · {self.current_release.version}"
        )
        self.size_label.setText(_format_bytes(self.current_release.archive_size))
        self._update_space()
        self.status.setText("İndirme başlatılıyor…")
        self._download()

    def _prepare_failed(self, message: str) -> None:
        self.search_button.setEnabled(True)
        self.select_button.setEnabled(self.results.currentRow() >= 0)
        self.results.setEnabled(True)
        self._show_error(message)

    def _choose_destination(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "İndirme klasörü")
        if path:
            self.destination.setText(path)
            self._update_space()

    def _update_space(self) -> None:
        path = Path(self.destination.text()).expanduser()
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            self.space_label.setText("Boş alan: bilinmiyor")
            return
        self.space_label.setText(f"Boş alan: {_format_bytes(free)}")

    def _download(self) -> None:
        if self.current_release is None:
            return
        self.extracted_path = None
        self.open_folder_button.setEnabled(False)
        self.extract_button.setEnabled(False)
        folder = Path(self.destination.text()).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        expected_size = self.current_release.archive_size
        if not DownloadManager.has_recommended_space(folder, expected_size):
            answer = QMessageBox.warning(
                self,
                "Disk alanı az",
                "Boş alan önerilen miktarın altında. Devam edilsin mi?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.search_button.setEnabled(True)
                self.select_button.setEnabled(True)
                self.results.setEnabled(True)
                return
        if not self.settings.gofile_browser_download_enabled:
            self._show_error(
                "Ayarlar bölümünden GoFile tarayıcı indirmelerini etkinleştir."
            )
            self.search_button.setEnabled(True)
            self.select_button.setEnabled(True)
            self.results.setEnabled(True)
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
        self.progress.setRange(0, 0)
        self.status.setText("GoFile açılıyor…")
        self._start_worker(worker)

    def _download_finished(self, value: object) -> None:
        self.downloaded_path = Path(value)
        self.archive_label.setText(self.downloaded_path.name)
        try:
            self.size_label.setText(_format_bytes(self.downloaded_path.stat().st_size))
        except OSError:
            self.size_label.setText("Bilinmiyor")
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status.setText("Arşiv çıkarılıyor…")
        self._start_extraction()

    def _download_failed(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self.search_button.setEnabled(True)
        self.select_button.setEnabled(True)
        self.results.setEnabled(True)
        self._show_error(message)

    def _extract(self) -> None:
        self._start_extraction()

    def _start_extraction(self) -> None:
        if not self.downloaded_path:
            return
        destination = _available_extraction_destination(
            self.downloaded_path,
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
        self.open_folder_button.setEnabled(False)
        self.status.setText("Arşiv çıkarılıyor…")
        self._start_worker(worker)

    def _extraction_failed(self, message: str) -> None:
        self.extract_button.setEnabled(True)
        self.search_button.setEnabled(True)
        self.select_button.setEnabled(True)
        self.results.setEnabled(True)
        self._show_error(message)

    def _extraction_finished(self, value: object) -> None:
        self.extracted_path = Path(value.destination)
        self.extract_button.setEnabled(False)
        self.search_button.setEnabled(True)
        self.select_button.setEnabled(True)
        self.results.setEnabled(True)
        self.open_folder_button.setEnabled(True)
        self.status.setText("Tamamlandı")

    def _open_extracted_folder(self) -> None:
        if self.extracted_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.extracted_path)))

    def _open_logs(self) -> None:
        self.repository.path.parent.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.repository.path.parent)))

    def _open_theme(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.theme_path)))

    def _show_error(self, message: str) -> None:
        self.status.setText("İşlem durdu")
        QMessageBox.warning(self, "Hata", message)

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
        return "Bilinmiyor"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "Bilinmiyor"


def _available_extraction_destination(archive: Path) -> Path:
    name = archive.name
    lowered = name.casefold()
    for suffix in (".tar.gz", ".tgz", ".zip", ".tar", ".7z", ".rar"):
        if lowered.endswith(suffix):
            name = name[: -len(suffix)]
            break
    if not name:
        name = "archive"
    candidate = archive.parent / f"{name}-extracted"
    index = 2
    while candidate.exists():
        candidate = archive.parent / f"{name}-extracted ({index})"
        index += 1
    return candidate
