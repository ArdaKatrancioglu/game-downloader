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
        self.setWindowTitle("Settings")
        self.settings = settings

        self.catalog_url = QLineEdit(settings.catalog_url or "")
        self.catalog_file = QLineEdit(str(settings.catalog_file))
        catalog_browse = QPushButton("Browse…")
        catalog_browse.clicked.connect(self._browse_catalog)
        catalog_row = QHBoxLayout()
        catalog_row.addWidget(self.catalog_file)
        catalog_row.addWidget(catalog_browse)

        self.allowed_domains = QLineEdit(", ".join(settings.allowed_catalog_domains))
        self.download_folder = QLineEdit(str(settings.default_download_folder))
        folder_browse = QPushButton("Browse…")
        folder_browse.clicked.connect(self._browse_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.download_folder)
        folder_row.addWidget(folder_browse)

        self.max_size = QSpinBox()
        self.max_size.setRange(1, 10_000)
        self.max_size.setValue(max(1, settings.max_extracted_archive_size // 1024**3))
        self.browser_fallback = QCheckBox(
            "Enable visible GoFile browser downloads (no API)"
        )
        self.browser_fallback.setChecked(settings.gofile_browser_download_enabled)
        self.remember_browser = QCheckBox(
            "Remember the dedicated GoFile browser session"
        )
        self.remember_browser.setChecked(settings.remember_gofile_browser_session)
        self.remember_browser.setEnabled(self.browser_fallback.isChecked())
        self.browser_fallback.toggled.connect(self.remember_browser.setEnabled)
        form = QFormLayout()
        form.addRow("Catalog URL (optional)", self.catalog_url)
        form.addRow("Local catalog file", catalog_row)
        form.addRow("Allowed catalog domains", self.allowed_domains)
        form.addRow("Default download folder", folder_row)
        form.addRow("Maximum extraction (GiB)", self.max_size)
        form.addRow("", self.browser_fallback)
        form.addRow("", self.remember_browser)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_settings)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse_catalog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select catalog", filter="JSON (*.json)")
        if path:
            self.catalog_file.setText(path)

    def _browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select download folder")
        if path:
            self.download_folder.setText(path)

    def _save_settings(self) -> None:
        from pathlib import Path

        updated = self.settings.model_copy(
            update={
                "catalog_url": self.catalog_url.text().strip() or None,
                "catalog_file": Path(self.catalog_file.text()).expanduser(),
                "allowed_catalog_domains": [
                    value.strip()
                    for value in self.allowed_domains.text().split(",")
                    if value.strip()
                ],
                "default_download_folder": Path(self.download_folder.text()).expanduser(),
                "max_extracted_archive_size": self.max_size.value() * 1024**3,
                "gofile_browser_download_enabled": self.browser_fallback.isChecked(),
                "remember_gofile_browser_session": self.remember_browser.isChecked(),
            }
        )
        self.settings_saved.emit(updated)
        self.accept()
