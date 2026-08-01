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
from game_downloader.models import (
    ExtractionLimits,
    FuckingFastSource,
    GameEntry,
    GameRelease,
    MultipartDownloadProgress,
)
from game_downloader.settings import AppSettings, SettingsRepository
from game_downloader.storage.fuckingfast_download import FuckingFastDownloader
from game_downloader.ui.settings_dialog import SettingsDialog
from game_downloader.ui.theme import load_theme
from game_downloader.ui.workers import (
    CoroutineWorker,
    FuckingFastWorker,
)
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
        self.active_download_worker: FuckingFastWorker | None = None
        self.workers: set[CoroutineWorker | FuckingFastWorker] = set()
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
        self.part_progress_label = QLabel("Part: —")
        self.part_progress_label.setObjectName("mutedLabel")
        self.part_progress = QProgressBar()
        self.part_progress.setRange(0, 100)
        self.total_progress_label = QLabel("Toplam: —")
        self.total_progress_label.setObjectName("mutedLabel")
        self.total_progress = QProgressBar()
        self.total_progress.setRange(0, 100)
        self.transfer_label = QLabel("Hız: — · Part ETA: — · Toplam ETA: —")
        self.transfer_label.setObjectName("mutedLabel")
        self.status = QLabel("Hazır")
        self.status.setObjectName("statusText")
        self.status.setWordWrap(True)
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
        self.limit_speed_checkbox.toggled.connect(self.limit_speed_spin.setEnabled)
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
        layout.addWidget(self.part_progress_label)
        layout.addWidget(self.part_progress)
        layout.addWidget(self.total_progress_label)
        layout.addWidget(self.total_progress)
        layout.addWidget(self.transfer_label)
        layout.addWidget(self.status)
        layout.addLayout(controls)
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
        worker: CoroutineWorker | FuckingFastWorker,
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
            self.results.addItem(
                QListWidgetItem(
                    f"{entry.title}\n{entry.detail_url}"
                )
            )
        self.select_button.setEnabled(bool(self.entries))
        if self.entries:
            self.results.setCurrentRow(0)
        self.status.setText(f"Web aramasından {len(self.entries)} sonuç")

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
        if not isinstance(self.current_release.source, FuckingFastSource):
            self._prepare_failed("Seçilen sonuçta FuckingFast part bağlantısı yok.")
            return
        self.size_label.setText(f"{len(self.current_release.source.parts)} part")
        self.status.setText("Sıralı indirme başlatılıyor…")
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
        if (
            self.current_release is None
            or not isinstance(self.current_release.source, FuckingFastSource)
        ):
            return
        self.downloaded_path = None
        self.downloaded_paths = []
        self.extracted_path = None
        self.open_folder_button.setEnabled(False)
        self.extract_button.setEnabled(False)
        folder = Path(self.destination.text()).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        max_bytes_per_second = None
        if self.limit_speed_checkbox.isChecked():
            max_bytes_per_second = round(self.limit_speed_spin.value() * 1_000_000 / 8)
        worker = FuckingFastWorker(
            FuckingFastDownloader(
                part_delay_min_seconds=self.settings.fuckingfast_part_delay_min_seconds,
                part_delay_max_seconds=self.settings.fuckingfast_part_delay_max_seconds,
                max_bytes_per_second=max_bytes_per_second,
            ),
            self.current_release.source,
            folder,
        )
        self.active_download_worker = worker
        worker.notice.connect(self.status.setText)
        worker.progress.connect(self._download_progress)
        worker.succeeded.connect(self._download_finished)
        worker.cancelled.connect(self._download_cancelled)
        worker.failed.connect(self._download_failed)
        self.part_progress.setRange(0, 100)
        self.part_progress.setValue(0)
        self.total_progress.setRange(0, 100)
        self.total_progress.setValue(0)
        self.part_progress_label.setText("Part: hazırlanıyor…")
        self.total_progress_label.setText("Toplam: hesaplanıyor…")
        self.transfer_label.setText("Hız: — · Part ETA: — · Toplam ETA: —")
        self.limit_speed_checkbox.setEnabled(False)
        self.limit_speed_spin.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.pause_button.setText("Duraklat")
        self.pause_button.setProperty("paused", False)
        self.cancel_button.setEnabled(True)
        self.status.setText("İlk FuckingFast part’ı hazırlanıyor…")
        self._start_worker(worker)

    def _download_finished(self, value: object) -> None:
        self.downloaded_paths = [Path(path) for path in value]
        self.downloaded_path = self.downloaded_paths[0] if self.downloaded_paths else None
        total_size = 0
        for path in self.downloaded_paths:
            with suppress(OSError):
                total_size += path.stat().st_size
        self.archive_label.setText(f"{len(self.downloaded_paths)} part indirildi")
        self.size_label.setText(_format_bytes(total_size))
        self.part_progress.setRange(0, 100)
        self.part_progress.setValue(100)
        self.total_progress.setRange(0, 100)
        self.total_progress.setValue(100)
        self.part_progress_label.setText("Part: tamamlandı")
        self.total_progress_label.setText(
            f"Toplam: {_format_bytes(total_size)} / {_format_bytes(total_size)} (%100)"
        )
        self.transfer_label.setText("Hız: — · Part ETA: 0 sn · Toplam ETA: 0 sn")
        self._finish_download_controls()
        self.open_folder_button.setEnabled(bool(self.downloaded_paths))
        self.status.setText(
            f"Tamamlandı: {len(self.downloaded_paths)} part indirildi ve doğrulandı"
        )

    def _download_progress(self, value: object) -> None:
        progress = MultipartDownloadProgress.model_validate(value)
        part = progress.part
        part_total = _format_bytes(part.total)
        part_percent = "—" if part.percent is None else f"%{part.percent:.1f}"
        self.part_progress_label.setText(
            f"Part {progress.part_index}/{progress.part_count}: "
            f"{_format_bytes(part.downloaded)} / {part_total} ({part_percent})"
        )
        _set_progress_bar(self.part_progress, part.percent)
        if progress.estimated_total_bytes is None or progress.total_percent is None:
            self.total_progress_label.setText(
                f"Toplam: {_format_bytes(progress.completed_bytes + part.downloaded)} / "
                "hesaplanıyor…"
            )
            self.total_progress.setRange(0, 0)
        else:
            prefix = "≈ " if progress.total_is_estimate else ""
            self.total_progress_label.setText(
                f"Toplam: {_format_bytes(progress.completed_bytes + part.downloaded)} / "
                f"{prefix}{_format_bytes(progress.estimated_total_bytes)} "
                f"(%{progress.total_percent:.1f})"
            )
            _set_progress_bar(self.total_progress, progress.total_percent)
        self.transfer_label.setText(
            f"Hız: {_format_speed(part.bytes_per_second)} · "
            f"Part ETA: {_format_duration(part.eta_seconds)} · "
            f"Toplam ETA: {_format_duration(progress.total_eta_seconds)}"
        )

    def _download_failed(self, message: str) -> None:
        self.part_progress.setRange(0, 100)
        self.total_progress.setRange(0, 100)
        self._finish_download_controls()
        self._show_error(message)

    def _download_cancelled(self, message: str) -> None:
        self.part_progress.setRange(0, 100)
        self.total_progress.setRange(0, 100)
        self._finish_download_controls()
        self.status.setText(message)

    def _finish_download_controls(self) -> None:
        self.active_download_worker = None
        self.search_button.setEnabled(True)
        self.select_button.setEnabled(self.results.currentRow() >= 0)
        self.results.setEnabled(True)
        self.limit_speed_checkbox.setEnabled(True)
        self.limit_speed_spin.setEnabled(self.limit_speed_checkbox.isChecked())
        self.pause_button.setEnabled(False)
        self.pause_button.setText("Duraklat")
        self.cancel_button.setEnabled(False)

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

    def _confirm_cancel_download(self) -> None:
        worker = self.active_download_worker
        if worker is None:
            return
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("İndirmeyi iptal et")
        dialog.setText("İndirme iptal edilsin mi?")
        dialog.setInformativeText("Devam eden part durur; işlem geri alınamaz.")
        delete_parts = QCheckBox("İndirilmiş partları sil")
        delete_parts.setChecked(True)
        dialog.setCheckBox(delete_parts)
        confirm = dialog.addButton("İndirmeyi iptal et", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QMessageBox.StandardButton.No)
        dialog.exec()
        if dialog.clickedButton() is not confirm:
            return
        worker.cancel_download(delete_completed=delete_parts.isChecked())
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        if delete_parts.isChecked():
            self.status.setText("İndirme iptal ediliyor; tamamlanmış partlar silinecek…")
        else:
            self.status.setText("İndirme iptal ediliyor; tamamlanmış partlar korunacak…")

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


def _format_speed(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "—"
    return f"{_format_bytes(round(bytes_per_second))}/sn"


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
