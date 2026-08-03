from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QPainter, QPainterPath, QPixmap
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
from game_downloader.ui.workers import (
    BrowserDirectWorker,
    CoroutineWorker,
    ExtractionWorker,
    fetch_image,
    fetch_images,
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
        self.poster_pixmap: QPixmap | None = None
        self.image_cache: dict[str, bytes] = {}
        self.image_request_token = 0
        self.active_provider = None
        self.active_download_worker: BrowserDirectWorker | None = None
        self.workers: set[CoroutineWorker | BrowserDirectWorker | ExtractionWorker] = set()
        self.theme_path = self.repository.path.parent / "theme.json"
        self.setWindowTitle("Ipsum İndirici")
        self.setMinimumSize(1280, 850)
        self.resize(1280, 850)
        self.setStyleSheet(load_theme(self.theme_path))
        self._build_ui()
        QTimer.singleShot(0, self._resize_poster_canvas)

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
        details_content = QHBoxLayout()
        details_content.setSpacing(16)
        self.image_preview = QLabel("Görsel yok")
        self.image_preview.setObjectName("imagePreview")
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Game posters are portrait-oriented (approximately 2:3). Keeping the
        # canvas at that ratio prevents metadata from being painted over it.
        self.image_preview.setFixedSize(190, 285)
        details_content.addWidget(
            self.image_preview,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        grid = QGridLayout()
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self.archive_label = QLabel("—")
        self.archive_label.setObjectName("metadataValue")
        self.version_label = QLabel("—")
        self.version_label.setObjectName("metadataValue")
        self.size_label = QLabel("—")
        self.size_label.setObjectName("metadataValue")
        self.link_label = QLabel("—")
        self.link_label.setObjectName("metadataValue")
        self.link_label.setWordWrap(True)
        self.overview_label = QLabel("—")
        self.overview_label.setObjectName("metadataValue")
        self.overview_label.setWordWrap(True)
        game_title = QLabel("Oyun:")
        game_title.setObjectName("metadataTitle")
        version_title = QLabel("Version:")
        version_title.setObjectName("metadataTitle")
        size_title = QLabel("Boyut:")
        size_title.setObjectName("metadataTitle")
        link_title = QLabel("Link:")
        link_title.setObjectName("metadataTitle")
        overview_title = QLabel("Açıklama:")
        overview_title.setObjectName("metadataTitle")
        grid.addWidget(game_title, 0, 0)
        grid.addWidget(self.archive_label, 0, 1, 1, 2)
        grid.addWidget(overview_title, 1, 0)
        grid.addWidget(self.overview_label, 1, 1, 1, 2)
        grid.addWidget(version_title, 2, 0)
        grid.addWidget(self.version_label, 2, 1, 1, 2)
        grid.addWidget(size_title, 3, 0)
        grid.addWidget(self.size_label, 3, 1, 1, 2)
        grid.addWidget(link_title, 4, 0)
        grid.addWidget(self.link_label, 4, 1, 1, 2)
        details_content.addLayout(grid, 1)
        details_layout.addLayout(details_content)
        details_layout.addStretch()
        content_row.addWidget(self.details_card, 2)
        layout.addLayout(content_row, 1)

        activity_card = QFrame()
        activity_card.setProperty("class", "card")
        activity_layout = QVBoxLayout(activity_card)
        activity_layout.setContentsMargins(18, 14, 18, 18)
        activity_layout.setSpacing(10)
        activity_title = QLabel("İşlem Durumu")
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._resize_poster_canvas)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "image_preview"):
            QTimer.singleShot(0, self._resize_poster_canvas)

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
        worker: CoroutineWorker | BrowserDirectWorker | ExtractionWorker,
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
            item = QListWidgetItem(entry.title)
            font = QFont(self.results.font())
            font.setBold(True)
            item.setFont(font)
            self.results.addItem(item)
        self._prefetch_result_images(self.entries)
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
        self._load_selected_image(entry)
        self.archive_label.setText(entry.title)
        self.version_label.setText(entry.version)
        self.size_label.setText(_format_bytes(entry.archive_size))
        self.link_label.setText(str(entry.detail_url or "—"))
        self.overview_label.setText(
            _truncate_description(entry.description) or "—"
        )

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
        self.link_label.setText(str(self.current_release.detail_url or "—"))
        self.overview_label.setText(
            _truncate_description(self.current_release.description) or "—"
        )
        self._download_browser_direct()

    def _prepare_failed(self, message: str) -> None:
        self._set_browsing_enabled(True)
        self.search_button.setEnabled(True)
        self.select_button.setEnabled(self.results.currentRow() >= 0)
        self.results.setEnabled(True)
        self._show_error(message)

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
        phases = 3 if self.settings.auto_extract_zip else 1
        self.part_progress_label.setText(f"Aşama 1/{phases} · İndirme hazırlanıyor…")
        self.total_progress_label.setText("Toplam süreç: %0")
        self.part_eta_label.setText("Bu aşamanın kalan süresi: hesaplanıyor…")
        self.total_eta_label.setText("Toplam kalan süre: hesaplanıyor…")
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
        phases = 3 if self.settings.auto_extract_zip else 1
        percent = "—" if progress.percent is None else f"%{progress.percent:.1f}"
        text = (
            f"{_format_bytes(progress.downloaded)} / {_format_bytes(progress.total)} "
            f"({percent})"
        )
        self.part_progress_label.setText(f"Aşama 1/{phases} · İndirme: {text}")
        _set_progress_bar(self.part_progress, progress.percent)
        overall_percent = (
            None if progress.percent is None else progress.percent / phases
        )
        _set_progress_bar(self.total_progress, overall_percent)
        overall_text = "—" if overall_percent is None else f"%{overall_percent:.1f}"
        self.total_progress_label.setText(f"Toplam süreç: {overall_text}")
        self.transfer_label.setText(f"Hız: {_format_speed(progress.bytes_per_second)}")
        eta = _format_duration(progress.eta_seconds)
        self.part_eta_label.setText(f"Bu aşamanın kalan süresi: {eta}")
        self.total_eta_label.setText(
            f"Toplam kalan süre: {eta} + sonraki aşamalar"
            if phases == 3 else f"Toplam kalan süre: {eta}"
        )

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
        uses_three_phases = (
            self.settings.auto_extract_zip
            and self.downloaded_path is not None
            and self.downloaded_path.suffix.casefold() == ".zip"
        )
        self.part_progress_label.setText(
            "Aşama 1/3 · İndirme tamamlandı"
            if uses_three_phases else "Aşama 1/1 · İndirme tamamlandı"
        )
        self.total_progress.setValue(33 if uses_three_phases else 100)
        self.total_progress_label.setText(
            "Toplam süreç: %33.3" if uses_three_phases else "Toplam süreç: %100"
        )
        self.transfer_label.setText("Hız: —")
        self.part_eta_label.setText("Bu aşamanın kalan süresi: 0 sn")
        self.total_eta_label.setText(
            "Toplam kalan süre: çıkarma aşamasında hesaplanacak"
            if uses_three_phases else "Toplam kalan süre: 0 sn"
        )
        self._finish_download_controls()
        self.open_folder_button.setEnabled(bool(self.downloaded_paths))
        if uses_three_phases:
            self._start_extraction()
            return
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
        self.image_request_token += 1
        self.poster_pixmap = None
        self.image_preview.clear()
        self.image_preview.setText("Görsel yok")
        self.archive_label.setText("—")
        self.version_label.setText("—")
        self.size_label.setText("—")
        self.link_label.setText("—")
        self.overview_label.setText("—")

    def _load_selected_image(self, entry: GameEntry) -> None:
        self.image_request_token += 1
        token = self.image_request_token
        # ``image_url`` is the poster; ``cover_url`` is generally a landscape
        # backdrop and is only a fallback when no poster was supplied.
        image_url = entry.image_url or entry.cover_url
        self.poster_pixmap = None
        self.image_preview.clear()
        if image_url is None:
            self.image_preview.setText("Görsel yok")
            return
        image_url_text = str(image_url)
        if cached := self.image_cache.get(image_url_text):
            self._show_selected_image(token, cached)
            return
        self.image_preview.setText("Görsel yükleniyor…")
        worker = CoroutineWorker(lambda: fetch_image(image_url_text))
        worker.succeeded.connect(
            lambda data, request_token=token, url=image_url_text: self._cache_and_show_image(
                request_token, url, data
            )
        )
        worker.failed.connect(
            lambda _message, request_token=token: self._image_load_failed(request_token)
        )
        self._start_worker(worker)

    def _prefetch_result_images(self, entries: list[GameEntry]) -> None:
        urls = [
            str(entry.image_url or entry.cover_url)
            for entry in entries
            if entry.image_url or entry.cover_url
        ]
        missing = [url for url in urls if url not in self.image_cache]
        if not missing:
            return
        worker = CoroutineWorker(lambda: fetch_images(missing))
        worker.succeeded.connect(self._store_image_cache)
        self._start_worker(worker)

    def _store_image_cache(self, value: object) -> None:
        if isinstance(value, dict):
            self.image_cache.update(
                {
                    url: data
                    for url, data in value.items()
                    if isinstance(url, str) and isinstance(data, bytes)
                }
            )

    def _cache_and_show_image(self, token: int, url: str, data: object) -> None:
        if isinstance(data, bytes):
            self.image_cache[url] = data
        self._show_selected_image(token, data)

    def _show_selected_image(self, token: int, data: object) -> None:
        if token != self.image_request_token:
            return
        pixmap = QPixmap()
        if not isinstance(data, bytes) or not pixmap.loadFromData(data):
            self._image_load_failed(token)
            return
        self.poster_pixmap = pixmap
        self._resize_poster_canvas()

    def _image_load_failed(self, token: int) -> None:
        if token == self.image_request_token:
            self.poster_pixmap = None
            self.image_preview.clear()
            self.image_preview.setText("Görsel yüklenemedi")

    def _resize_poster_canvas(self) -> None:
        """Scale the poster canvas with the available selected-card space."""
        available_width = max(190, int(self.details_card.width() * 0.4))
        available_height = max(285, self.details_card.height())
        poster_width = min(available_width, available_height * 2 // 3)
        poster_width = max(190, poster_width)
        poster_height = poster_width * 3 // 2
        if self.image_preview.width() != poster_width:
            self.image_preview.setFixedSize(poster_width, poster_height)
        self._scale_metadata_fonts(poster_width)
        if self.poster_pixmap is not None:
            self.image_preview.setText("")
            scaled = self.poster_pixmap.scaled(
                self.image_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            rounded = QPixmap(self.image_preview.size())
            rounded.fill(Qt.GlobalColor.transparent)
            painter = QPainter(rounded)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # Match QLabel#imagePreview's 15px corner radius exactly.
            radius = 15
            path = QPainterPath()
            path.addRoundedRect(QRectF(rounded.rect()), radius, radius)
            painter.setClipPath(path)
            x = (rounded.width() - scaled.width()) // 2
            y = (rounded.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.end()
            self.image_preview.setPixmap(rounded)

    def _scale_metadata_fonts(self, poster_width: int) -> None:
        """Slightly enlarge metadata text as the poster grows."""
        scale = min(2.0, max(1.0, poster_width / 190 * 0.35 + 0.65))
        for label in self.details_card.findChildren(QLabel):
            if label.objectName() == "metadataTitle":
                base_size = 14
            elif label.objectName() == "metadataValue":
                base_size = 13
            else:
                continue
            # The theme's font-size rule overrides QLabel.setFont(), so apply
            # the calculated size through an inline stylesheet.
            label.setStyleSheet(f"font-size: {base_size * scale:.1f}px;")

    def _reset_download_progress(self) -> None:
        self.part_progress.setRange(0, 100)
        self.part_progress.setValue(0)
        self.total_progress.setRange(0, 100)
        self.total_progress.setValue(0)
        self.part_progress_label.setText("Aktif aşama: —")
        self.total_progress_label.setText("Toplam süreç: —")
        self.transfer_label.setText("Hız: —")
        self.part_eta_label.setText("Bu aşamanın kalan süresi: —")
        self.total_eta_label.setText("Toplam kalan süre: —")

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
        archive = self.downloaded_path
        destination = _available_extraction_destination(
            archive,
        )
        extractor = ArchiveExtractor(
            ExtractionLimits(
                max_total_size=self.settings.max_extracted_archive_size,
                max_files=self.settings.max_extracted_file_count,
            )
        )
        worker = ExtractionWorker(extractor, archive, destination)
        worker.progress.connect(self._extraction_progress)
        worker.succeeded.connect(self._extraction_finished)
        worker.failed.connect(self._extraction_failed)
        self.extract_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self.part_progress.setRange(0, 0)
        self.part_progress_label.setText("Aşama 2/3 · Arşiv inceleniyor…")
        self.total_progress.setRange(0, 100)
        self.total_progress.setValue(33)
        self.total_progress_label.setText("Toplam süreç: %33.3")
        self.part_eta_label.setText("Bu aşamanın kalan süresi: hesaplanıyor…")
        self.total_eta_label.setText("Toplam kalan süre: hesaplanıyor…")
        self.transfer_label.setText("Çıkarma hızı: hesaplanıyor…")
        self.status.setText("Aşama 2/3: Arşiv çıkarılıyor…")
        self._start_worker(worker)

    def _extraction_progress(self, value: object) -> None:
        progress = DownloadProgress.model_validate(value)
        percent = progress.percent or 0.0
        text = (
            f"{_format_bytes(progress.downloaded)} / {_format_bytes(progress.total)} "
            f"(%{percent:.1f})"
        )
        self.part_progress_label.setText(f"Aşama 2/3 · Arşiv çıkarma: {text}")
        _set_progress_bar(self.part_progress, percent)
        overall_percent = (100.0 + percent) / 3
        _set_progress_bar(self.total_progress, overall_percent)
        self.total_progress_label.setText(f"Toplam süreç: %{overall_percent:.1f}")
        eta = _format_duration(progress.eta_seconds)
        self.part_eta_label.setText(f"Bu aşamanın kalan süresi: {eta}")
        self.total_eta_label.setText(f"Toplam kalan süre: {eta}")
        self.transfer_label.setText(
            f"Çıkarma hızı: {_format_speed(progress.bytes_per_second)}"
        )

    def _extraction_failed(self, message: str) -> None:
        self.part_progress_label.setText("Aşama 2/3 · Arşiv çıkarma başarısız")
        self.total_progress_label.setText("Toplam süreç: Aşama 2/3'te durdu")
        self.part_eta_label.setText("Bu aşamanın kalan süresi: —")
        self.total_eta_label.setText("Toplam kalan süre: —")
        self.extract_button.setEnabled(True)
        self.search_button.setEnabled(True)
        self.select_button.setEnabled(True)
        self.results.setEnabled(True)
        self._show_error(message)

    def _extraction_finished(self, value: object) -> None:
        self.extracted_path = Path(value.destination)
        self.part_progress.setRange(0, 100)
        self.part_progress.setValue(100)
        self.part_progress_label.setText("Aşama 2/3 · Arşiv çıkarma tamamlandı")
        self.total_progress.setValue(67)
        self.total_progress_label.setText("Toplam süreç: %66.7")
        self.part_eta_label.setText("Bu aşamanın kalan süresi: 0 sn")
        self.total_eta_label.setText("Toplam kalan süre: ZIP silme")
        self.transfer_label.setText("Hız: —")
        self.status.setText("Aşama 3/3: ZIP dosyası siliniyor…")
        self.part_progress.setRange(0, 0)
        self.part_progress_label.setText("Aşama 3/3 · ZIP dosyası siliniyor…")
        QTimer.singleShot(0, self._finish_archive_deletion)

    def _finish_archive_deletion(self) -> None:
        archive_removed = True
        archive = self.downloaded_path
        if archive is not None:
            try:
                archive.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                archive_removed = False
            if archive_removed:
                self.downloaded_path = None
                self.downloaded_paths = [
                    path for path in self.downloaded_paths if path != archive
                ]
        self.extract_button.setEnabled(False)
        self.search_button.setEnabled(True)
        self.select_button.setEnabled(True)
        self.results.setEnabled(True)
        self.open_folder_button.setEnabled(True)
        self.part_progress.setRange(0, 100)
        if archive_removed:
            self.part_progress.setValue(100)
            self.total_progress.setValue(100)
            self.part_progress_label.setText("Aşama 3/3 · ZIP dosyası silindi")
            self.total_progress_label.setText("Toplam süreç: %100")
            self.part_eta_label.setText("Bu aşamanın kalan süresi: 0 sn")
            self.total_eta_label.setText("Toplam kalan süre: 0 sn")
            self.status.setText("Tüm aşamalar tamamlandı")
        else:
            self.part_progress.setValue(0)
            self.part_progress_label.setText("Aşama 3/3 · ZIP dosyası silinemedi")
            self.total_progress.setValue(67)
            self.total_progress_label.setText("Toplam süreç: %66.7")
            self.part_eta_label.setText("Bu aşamanın kalan süresi: —")
            self.total_eta_label.setText("Toplam kalan süre: —")
            self.status.setText("Arşiv çıkarıldı; ZIP dosyası silinemedi")

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

        dialog.settings_saved.connect(save)
        dialog.exec()

def _format_bytes(value: int | None) -> str:
    if value is None:
        return "Bilinmiyor"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "Bilinmiyor"


def _truncate_description(text: str, limit: int = 150) -> str:
    """Shorten descriptions for display without changing the stored value."""
    text = text.strip()
    if len(text) <= limit:
        return text
    shortened = text[:limit].rsplit(" ", 1)[0]
    return shortened + "…"


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
