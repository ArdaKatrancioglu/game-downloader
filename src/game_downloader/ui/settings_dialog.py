from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
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

        self.max_size = QSpinBox()
        self.max_size.setRange(1, 10_000)
        self.max_size.setValue(max(1, settings.max_extracted_archive_size // 1024**3))
        self.part_delay_min = QSpinBox()
        self.part_delay_min.setRange(0, 300)
        self.part_delay_min.setSuffix(" sn")
        self.part_delay_min.setValue(round(settings.fuckingfast_part_delay_min_seconds))
        self.part_delay_max = QSpinBox()
        self.part_delay_max.setRange(0, 300)
        self.part_delay_max.setSuffix(" sn")
        self.part_delay_max.setValue(round(settings.fuckingfast_part_delay_max_seconds))
        form = QFormLayout()
        form.addRow("Web arama adresi", self.web_search_url)
        form.addRow("İzin verilen site alan adları", self.allowed_domains)
        form.addRow("İndirme klasörü", folder_row)
        form.addRow("Part arası bekleme (min)", self.part_delay_min)
        form.addRow("Part arası bekleme (max)", self.part_delay_max)
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

    def _save_settings(self) -> None:
        from pathlib import Path

        if self.part_delay_min.value() > self.part_delay_max.value():
            QMessageBox.warning(
                self,
                "Geçersiz bekleme aralığı",
                "Minimum bekleme, maksimum beklemeden büyük olamaz.",
            )
            return

        updated = self.settings.model_copy(
            update={
                "web_search_url": self.web_search_url.text().strip() or None,
                "allowed_search_domains": [
                    value.strip()
                    for value in self.allowed_domains.text().split(",")
                    if value.strip()
                ],
                "default_download_folder": Path(self.download_folder.text()).expanduser(),
                "fuckingfast_part_delay_min_seconds": float(self.part_delay_min.value()),
                "fuckingfast_part_delay_max_seconds": float(self.part_delay_max.value()),
                "max_extracted_archive_size": self.max_size.value() * 1024**3,
            }
        )
        self.settings_saved.emit(updated)
        self.accept()
