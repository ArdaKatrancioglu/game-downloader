from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from game_downloader.settings import AppSettings


class SettingsDialog(QDialog):
    settings_saved = Signal(object)

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ayarlar")
        self.settings = settings

        self.web_search_url = QLineEdit(settings.web_search_url or "")
        self.allowed_domains = QLineEdit(", ".join(settings.allowed_search_domains))
        self.download_folder = QLineEdit(str(settings.default_download_folder))
        folder_browse = QPushButton("Gözat")
        folder_browse.clicked.connect(self._browse_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.download_folder)
        folder_row.addWidget(folder_browse)
        self.chrome_path = QLineEdit(
            str(settings.chrome_executable_path) if settings.chrome_executable_path else ""
        )
        chrome_browse = QPushButton("Gözat")
        chrome_browse.clicked.connect(self._browse_chrome)
        chrome_row = QHBoxLayout()
        chrome_row.addWidget(self.chrome_path)
        chrome_row.addWidget(chrome_browse)
        self.browser_headless = QCheckBox("Arka planda çalıştır")
        self.browser_headless.setChecked(settings.browser_headless)
        self.browser_timeout = QSpinBox()
        self.browser_timeout.setRange(1, 300)
        self.browser_timeout.setSuffix(" sn")
        self.browser_timeout.setValue(round(settings.browser_timeout_seconds))
        self.auto_extract_zip = QCheckBox("İndirme tamamlanınca ZIP dosyasını otomatik çıkar")
        self.auto_extract_zip.setChecked(settings.auto_extract_zip)

        self.max_size = QSpinBox()
        self.max_size.setRange(1, 10_000)
        self.max_size.setValue(max(1, settings.max_extracted_archive_size // 1024**3))
        form = QFormLayout()
        form.addRow("Web arama adresi", self.web_search_url)
        form.addRow("İzin verilen site alan adları", self.allowed_domains)
        form.addRow("İndirme klasörü", folder_row)
        form.addRow("Chrome çalıştırılabilir dosyası", chrome_row)
        form.addRow("Tarayıcı modu", self.browser_headless)
        form.addRow("Tarayıcı zaman aşımı", self.browser_timeout)
        form.addRow("Arşiv işlemi", self.auto_extract_zip)
        form.addRow("Maksimum çıkarma (GiB)", self.max_size)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Kaydet")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("İptal")
        buttons.accepted.connect(self._save_settings)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "İndirme klasörü")
        if path:
            self.download_folder.setText(path)

    def _browse_chrome(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chrome çalıştırılabilir dosyası")
        if path:
            self.chrome_path.setText(path)

    def _save_settings(self) -> None:
        from pathlib import Path

        updated = self.settings.model_copy(
            update={
                "web_search_url": self.web_search_url.text().strip() or None,
                "allowed_search_domains": [
                    value.strip()
                    for value in self.allowed_domains.text().split(",")
                    if value.strip()
                ],
                "default_download_folder": Path(self.download_folder.text()).expanduser(),
                "chrome_executable_path": (
                    Path(self.chrome_path.text()).expanduser()
                    if self.chrome_path.text().strip() else None
                ),
                "browser_headless": self.browser_headless.isChecked(),
                "browser_timeout_seconds": float(self.browser_timeout.value()),
                "auto_extract_zip": self.auto_extract_zip.isChecked(),
                "max_extracted_archive_size": self.max_size.value() * 1024**3,
            }
        )
        self.settings_saved.emit(updated)
        self.accept()
