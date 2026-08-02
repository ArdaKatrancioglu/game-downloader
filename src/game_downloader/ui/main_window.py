from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
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
from game_downloader.download.manager import DownloadManager
from game_downloader.models import (
    BrowserDirectSource,
    DownloadProgress,
    ExtractionLimits,
    GameEntry,
    GameRelease,
)
from game_downloader.security import safe_folder_name
from game_downloader.settings import AppSettings, SettingsRepository
from game_downloader.storage.browser_direct import BrowserDirectDownloader, BrowserOptions
from game_downloader.ui.settings_dialog import SettingsDialog
from game_downloader.ui.theme import load_theme
from game_downloader.ui.workers import BrowserDirectWorker, CoroutineWorker
from game_downloader.web_search import InternetSearchProvider


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
        self.downloaded_paths: list[Path] = []
        self.extracted_path: Path | None = None
        self.active_provider = None
        self.active_download_worker: BrowserDirectWorker | None = None
        self.workers: set[CoroutineWorker | BrowserDirectWorker] = set()
        self.theme_path = self.repository.path.parent / "theme.json"
        self.setWindowTitle("Ipsum İndirici")
        self.setMinimumSize(1040, 680)
        self.resize(1280, 780)
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
        title = QLabel("Ipsum İndirici")
        title.setObjectName("pageTitle")
        header_layout.addWidget(title)
        header_layout.addStretch()
        settings_button = QPushButton("Ayarlar")
        settings_button.setObjectName("quietButton")
        settings_button.clicked.connect(self._open_settings)
        header_layout.addWidget(settings_button)
        layout.addWidget(header)

        self.search_card = QFrame()
        self.search_card.setProperty("class", "card")
        search_layout = QVBoxLayout(self.search_card)
        search_layout.setContentsMargins(18, 14, 18, 18)
        search_layout.setSpacing(10)
        search_title = QLabel("Oyun ara")
        search_title.setObjectName("sectionTitle")
        search_layout.addWidget(search_title)
        search_layout.addWidget(_divider())
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
        layout.addWidget(self.search_card)

        content_row = QHBoxLayout()
        content_row.setSpacing(14)

        self.results_card = QFrame()
        self.results_card.setProperty("class", "card")
        results_layout = QVBoxLayout(self.results_card)
        results_layout.setContentsMargins(18, 14, 18, 18)
        results_layout.setSpacing(10)
        results_title = QLabel("Sonuçlar")
        results_title.setObjectName("sectionTitle")
        results_layout.addWidget(results_title)
        results_layout.addWidget(_divider())
        self.results = QListWidget()
        self.results.setSpacing(2)
        self.results.itemDoubleClicked.connect(lambda _: self._select_result())
        self.results.currentRowChanged.connect(self._preview_selected_result)
        results_layout.addWidget(self.results, 1)
        self.select_button = QPushButton("İndir")
        self.select_button.setObjectName("primaryButton")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._select_result)
        results_layout.addWidget(self.select_button)
        content_row.addWidget(self.results_card, 3)

        self.details_card = QFrame()
        self.details_card.setProperty("class", "card")
        self.details_card.setMinimumWidth(360)
        details_layout = QVBoxLayout(self.details_card)
        details_layout.setContentsMargins(18, 14, 18, 18)
        details_layout.setSpacing(10)
        details_title = QLabel("Seçilen")
        details_title.setObjectName("sectionTitle")
        details_layout.addWidget(details_title)
        details_layout.addWidget(_divider())
        grid = QGridLayout()
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self.archive_label = QLabel("—")
        self.archive_label.setObjectName("metadataValue")
        self.version_label = QLabel("—")
        self.version_label.setObjectName("metadataValue")
        self.size_label = QLabel("—")
        self.size_label.setObjectName("metadataValue")
        self.space_label = QLabel("Boş alan: —")
        self.space_label.setObjectName("mutedLabel")
        game_title = QLabel("Oyun:")
        game_title.setObjectName("metadataTitle")
        version_title = QLabel("Version:")
        version_title.setObjectName("metadataTitle")
        size_title = QLabel("Boyut:")
        size_title.setObjectName("metadataTitle")
        grid.addWidget(game_title, 0, 0)
        grid.addWidget(self.archive_label, 0, 1, 1, 2)
        grid.addWidget(version_title, 1, 0)
        grid.addWidget(self.version_label, 1, 1, 1, 2)
        grid.addWidget(size_title, 2, 0)
        grid.addWidget(self.size_label, 2, 1, 1, 2)
        grid.addWidget(self.space_label, 3, 1, 1, 2)
        details_layout.addLayout(grid)
        details_layout.addStretch()
        content_row.addWidget(self.details_card, 2)
        layout.addLayout(content_row, 1)

        activity_card = QFrame()
        activity_card.setProperty("class", "card")
        activity_layout = QVBoxLayout(activity_card)
        activity_layout.setContentsMargins(18, 14, 18, 18)
        activity_layout.setSpacing(10)
        activity_title = QLabel("İndirme Durumu")
        activity_title.setObjectName("sectionTitle")
        activity_layout.addWidget(activity_title)
        activity_layout.addWidget(_divider())
        self.part_progress_label = QLabel("Dosya: —")
        self.part_progress_label.setObjectName("mutedLabel")
        self.part_progress = QProgressBar()
        self.part_progress.setRange(0, 100)
        self.total_progress_label = QLabel("Toplam: —")
        self.total_progress_label.setObjectName("mutedLabel")
        self.total_progress = QProgressBar()
        self.total_progress.setRange(0, 100)
        self.transfer_label = QLabel("Hız: —")
        self.transfer_label.setObjectName("mutedLabel")
        self.part_eta_label = QLabel("Kalan süre: —")
        self.part_eta_label.setObjectName("mutedLabel")
        self.total_eta_label = QLabel("Kalan süre: —")
        self.total_eta_label.setObjectName("mutedLabel")
        self.status = QLabel("Hazır")
        self.status.setObjectName("statusText")
        self.status.setWordWrap(True)
        progress_row = QHBoxLayout()
        progress_row.setSpacing(22)
        part_column = QVBoxLayout()
        part_column.setSpacing(7)
        part_column.addWidget(self.part_progress_label)
        part_column.addWidget(self.part_progress)
        part_column.addWidget(self.part_eta_label)
        total_column = QVBoxLayout()
        total_column.setSpacing(7)
        total_column.addWidget(self.total_progress_label)
        total_column.addWidget(self.total_progress)
        total_column.addWidget(self.total_eta_label)
        progress_row.addLayout(part_column, 1)
        progress_row.addLayout(total_column, 1)
        activity_layout.addLayout(progress_row)
        activity_layout.addWidget(self.transfer_label)

        controls = QHBoxLayout()
        self.extract_button = QPushButton("Tekrar çıkar")
        self.extract_button.setEnabled(False)
        self.extract_button.setVisible(False)
        self.extract_button.clicked.connect(self._extract)
        self.open_folder_button = QPushButton("Klasörü aç")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self._open_extracted_folder)
        self.limit_speed_checkbox = QCheckBox("İndirme hızını sınırla")
        self.limit_speed_spin = QDoubleSpinBox()
        self.limit_speed_spin.setRange(0.1, 10_000)
        self.limit_speed_spin.setValue(20.0)
        self.limit_speed_spin.setDecimals(1)
        self.limit_speed_spin.setSuffix(" Mbit/sn")
        self.limit_speed_spin.setEnabled(False)
        self.limit_speed_checkbox.toggled.connect(self._speed_limit_changed)
        self.limit_speed_spin.valueChanged.connect(self._speed_limit_changed)
        self.pause_button = QPushButton("Duraklat")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.cancel_button = QPushButton("İptal et")
        self.cancel_button.setObjectName("dangerButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._confirm_cancel_download)
        controls.addWidget(self.limit_speed_checkbox)
        controls.addWidget(self.limit_speed_spin)
        controls.addStretch()
        controls.addWidget(self.extract_button)
        controls.addWidget(self.open_folder_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.cancel_button)
        activity_layout.addLayout(controls)
        layout.addWidget(activity_card)
        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        signature = QLabel("Made with Love by Arda")
        signature.setObjectName("signatureLabel")
        footer.addWidget(signature)
        layout.addLayout(footer)
        self.setCentralWidget(root)

    def _provider(self):
        if not self.settings.web_search_url:
            raise ValueError("Ayarlar bölümünde web arama adresini belirtin.")
        host = urlsplit(self.settings.web_search_url).hostname
        if not host:
            raise ValueError("Ayarlar bölümünde geçerli bir web arama adresi belirtin.")
        allowed = self.settings.allowed_search_domains or [host]
        return InternetSearchProvider(self.settings.web_search_url, allowed)

    def _start_worker(
        self,
        worker: CoroutineWorker | BrowserDirectWorker,
    ) -> None:
        self.workers.add(worker)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.start()

    def _search(self) -> None:
        if self.active_download_worker is not None:
            return
        query = self.search_input.text().strip()
        if not query:
            self.status.setText("Oyun adı yaz.")
            self.search_input.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.search_button.setEnabled(False)
        self.status.setText("Aranıyor…")
        try:
            self.active_provider = self._provider()
        except ValueError as exc:
            self._show_error(str(exc))
            return
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
            metadata = [
                f"ID: {entry.id}",
                f"Boyut: {_format_bytes(entry.archive_size)}",
            ]
            if entry.release_date:
                metadata.append(f"Yayın: {entry.release_date}")
            if entry.image_url:
                metadata.append(f"Görsel: {entry.image_url}")
            if entry.cover_url:
                metadata.append(f"Kapak: {entry.cover_url}")
            if entry.genres:
                genre_names = [
                    str(genre.get("name", "")) if isinstance(genre, dict) else genre
                    for genre in entry.genres
                ]
                metadata.append(f"Tür: {', '.join(name for name in genre_names if name)}")
            self.results.addItem(
                QListWidgetItem(
                    f"{entry.title}\n{' · '.join(metadata)}\n{entry.detail_url}"
                )
            )
        self.select_button.setEnabled(bool(self.entries))
        if self.entries:
            self.results.setCurrentRow(0)
        self.status.setText(f"Web aramasından {len(self.entries)} sonuç")

    def _preview_selected_result(self, row: int) -> None:
        self.select_button.setEnabled(
            row >= 0 and self.active_download_worker is None
        )
        if row < 0 or row >= len(self.entries):
            self._reset_selected_metadata()
            return
        entry = self.entries[row]
        self.archive_label.setText(entry.title)
        self.version_label.setText(entry.version)
        self.size_label.setText(_format_bytes(entry.archive_size))

    def _select_result(self) -> None:
        row = self.results.currentRow()
        if row < 0:
            self.status.setText("Bir sonuç seç.")
            return
        entry = self.entries[row]
        self._set_browsing_enabled(False)
        self.search_button.setEnabled(False)
        self.select_button.setEnabled(False)
        self.results.setEnabled(False)
        self.status.setText("Bağlantı bulunuyor…")

        async def prepare() -> GameRelease:
            if self.active_provider is None:
                raise LookupError("Run the catalog search again.")
            return await self.active_provider.get_release(entry.id)

        worker = CoroutineWorker(prepare)
        worker.succeeded.connect(self._release_ready)
        worker.failed.connect(self._prepare_failed)
        self._start_worker(worker)

    def _release_ready(self, value: object) -> None:
        self.current_release = value
        self.archive_label.setText(self.current_release.title)
        self.version_label.setText(self.current_release.version)
        self.size_label.setText(_format_bytes(self.current_release.archive_size))
        self._update_space()
        self._download_browser_direct()

    def _prepare_failed(self, message: str) -> None:
        self._set_browsing_enabled(True)
        self.search_button.setEnabled(True)
        self.select_button.setEnabled(self.results.currentRow() >= 0)
        self.results.setEnabled(True)
        self._show_error(message)

    def _update_space(self) -> None:
        path = self.settings.default_download_folder.expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(path).free
        except OSError:
            self.space_label.setText("Boş alan: bilinmiyor")
            return
        self.space_label.setText(f"Boş alan: {_format_bytes(free)}")

    def _download_browser_direct(self) -> None:
        if self.current_release is None or not isinstance(
            self.current_release.source, BrowserDirectSource
        ):
            return
        folder = self.settings.default_download_folder.expanduser() / safe_folder_name(
            self.current_release.title
        )
        max_speed = None
        if self.limit_speed_checkbox.isChecked():
            max_speed = round(self.limit_speed_spin.value() * 1_000_000 / 8)
        worker = BrowserDirectWorker(
            self._browser_downloader(max_bytes_per_second=max_speed),
            self.current_release.source,
            folder,
        )
        self.active_download_worker = worker
        worker.notice.connect(self.status.setText)
        worker.progress.connect(self._direct_download_progress)
        worker.succeeded.connect(self._direct_download_finished)
        worker.cancelled.connect(self._download_cancelled)
        worker.failed.connect(self._download_failed)
        self.part_progress_label.setText("Dosya: hazırlanıyor…")
        self.total_progress_label.setText("Toplam: hesaplanıyor…")
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self._set_browsing_enabled(False)
        self._start_worker(worker)

    def _browser_downloader(
        self, *, max_bytes_per_second: int | None = None
    ) -> BrowserDirectDownloader:
        return BrowserDirectDownloader(
            BrowserOptions(
                executable_path=self.settings.chrome_executable_path,
                headless=self.settings.browser_headless,
                timeout_seconds=self.settings.browser_timeout_seconds,
            ),
            manager=DownloadManager(max_bytes_per_second=max_bytes_per_second),
        )

    def _direct_download_progress(self, value: object) -> None:
        progress = DownloadProgress.model_validate(value)
        percent = "—" if progress.percent is None else f"%{progress.percent:.1f}"
        text = (
            f"{_format_bytes(progress.downloaded)} / {_format_bytes(progress.total)} "
            f"({percent})"
        )
        self.part_progress_label.setText(f"Dosya: {text}")
        self.total_progress_label.setText(f"Toplam: {text}")
        _set_progress_bar(self.part_progress, progress.percent)
        _set_progress_bar(self.total_progress, progress.percent)
        self.transfer_label.setText(f"Hız: {_format_speed(progress.bytes_per_second)}")
        eta = _format_duration(progress.eta_seconds)
        self.part_eta_label.setText(f"Kalan süre: {eta}")
        self.total_eta_label.setText(f"Kalan süre: {eta}")

    def _direct_download_finished(self, value: object) -> None:
        path = Path(value)
        self._download_finished([path])

    def _download_finished(self, value: object) -> None:
        self.downloaded_paths = [Path(path) for path in value]
        self.downloaded_path = self.downloaded_paths[0] if self.downloaded_paths else None
        total_size = 0
        for path in self.downloaded_paths:
            with suppress(OSError):
                total_size += path.stat().st_size
        self.size_label.setText(_format_total_gb(total_size))
        self.part_progress.setRange(0, 100)
        self.part_progress.setValue(100)
        self.total_progress.setRange(0, 100)
        self.total_progress.setValue(100)
        self.part_progress_label.setText("Dosya: tamamlandı")
        self.total_progress_label.setText(
            f"Toplam: {_format_bytes(total_size)} / {_format_bytes(total_size)} (%100)"
        )
        self.transfer_label.setText("Hız: —")
        self.part_eta_label.setText("Kalan süre: 0 sn")
        self.total_eta_label.setText("Kalan süre: 0 sn")
        self._finish_download_controls()
        self.open_folder_button.setEnabled(bool(self.downloaded_paths))
        self.status.setText(f"Tamamlandı: {self.downloaded_path.name}")

    def _download_failed(self, message: str) -> None:
        self._reset_download_progress()
        self._finish_download_controls()
        self._show_error(message)

    def _download_cancelled(self, message: str) -> None:
        self._reset_download_progress()
        self._finish_download_controls()
        self.current_release = None
        self._reset_selected_metadata()
        self.status.setText(message)

    def _reset_selected_metadata(self) -> None:
        self.archive_label.setText("—")
        self.version_label.setText("—")
        self.size_label.setText("—")

    def _reset_download_progress(self) -> None:
        self.part_progress.setRange(0, 100)
        self.part_progress.setValue(0)
        self.total_progress.setRange(0, 100)
        self.total_progress.setValue(0)
        self.part_progress_label.setText("Dosya: —")
        self.total_progress_label.setText("Toplam: —")
        self.transfer_label.setText("Hız: —")
        self.part_eta_label.setText("Kalan süre: —")
        self.total_eta_label.setText("Kalan süre: —")

    def _finish_download_controls(self) -> None:
        self.active_download_worker = None
        self.search_button.setEnabled(True)
        self.select_button.setEnabled(self.results.currentRow() >= 0)
        self.results.setEnabled(True)
        self._set_browsing_enabled(True)
        self.limit_speed_checkbox.setEnabled(True)
        self.limit_speed_spin.setEnabled(self.limit_speed_checkbox.isChecked())
        self.pause_button.setEnabled(False)
        self.pause_button.setText("Duraklat")
        self.cancel_button.setEnabled(False)
        self.select_button.setText("İndir")

    def _set_browsing_enabled(self, enabled: bool) -> None:
        self.search_card.setEnabled(enabled)
        self.results_card.setEnabled(enabled)
        self.details_card.setEnabled(enabled)
        self.search_input.setEnabled(enabled)
        self.search_button.setEnabled(enabled)
        self.results.setEnabled(enabled)
        self.select_button.setEnabled(enabled and self.results.currentRow() >= 0)

    def _toggle_pause(self) -> None:
        worker = self.active_download_worker
        if worker is None:
            return
        if self.pause_button.property("paused"):
            worker.resume_download()
            self.pause_button.setText("Duraklat")
            self.pause_button.setProperty("paused", False)
            self.status.setText("İndirme devam ediyor…")
        else:
            worker.pause_download()
            self.pause_button.setText("Devam et")
            self.pause_button.setProperty("paused", True)
            self.status.setText("İndirme duraklatıldı")

    def _speed_limit_changed(self, _value: object = None) -> None:
        enabled = self.limit_speed_checkbox.isChecked()
        self.limit_speed_spin.setEnabled(enabled)
        worker = self.active_download_worker
        if worker is None:
            return
        max_bytes_per_second = None
        if enabled:
            max_bytes_per_second = round(
                self.limit_speed_spin.value() * 1_000_000 / 8
            )
        worker.set_speed_limit(max_bytes_per_second)
        if enabled:
            self.status.setText(
                f"Hız sınırı {self.limit_speed_spin.value():.1f} Mbit/sn olarak uygulandı"
            )
        else:
            self.status.setText("İndirme hızı sınırı kaldırıldı")

    def _confirm_cancel_download(self) -> None:
        worker = self.active_download_worker
        if worker is None:
            return
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("İndirmeyi iptal et")
        dialog.setText("İndirme iptal edilsin mi?")
        dialog.setInformativeText(
            "Aktarım durur; yarım .part dosyası daha sonra devam edebilmek için korunur."
        )
        confirm = dialog.addButton("İndirmeyi iptal et", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QMessageBox.StandardButton.No)
        dialog.exec()
        if dialog.clickedButton() is not confirm:
            return
        worker.cancel_download()
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.status.setText("İndirme iptal ediliyor; yarım dosya korunacak…")

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
        elif self.downloaded_paths:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.downloaded_paths[0].parent))
            )

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
            self._update_space()

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


def _format_speed(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "—"
    return f"{bytes_per_second * 8 / 1_000_000:.1f} Mbit/sn"


def _format_total_gb(value: int) -> str:
    gibibytes = value / 1024**3
    rounded = max(1, round(gibibytes / 5) * 5)
    return f"{rounded} GB"


def _divider() -> QFrame:
    divider = QFrame()
    divider.setObjectName("cardDivider")
    divider.setFrameShape(QFrame.Shape.HLine)
    return divider


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "hesaplanıyor…"
    total = max(0, round(seconds))
    hours, remaining = divmod(total, 3600)
    minutes, seconds = divmod(remaining, 60)
    if hours:
        return f"{hours} sa {minutes:02d} dk"
    if minutes:
        return f"{minutes} dk {seconds:02d} sn"
    return f"{seconds} sn"


def _set_progress_bar(bar: QProgressBar, percent: float | None) -> None:
    if percent is None:
        bar.setRange(0, 0)
        return
    bar.setRange(0, 100)
    bar.setValue(max(0, min(100, round(percent))))


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
