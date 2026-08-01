from __future__ import annotations

from contextlib import suppress

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from game_downloader.security import SecurityError, extract_gofile_content_id


class FileCryptLinkDialog(QDialog):
    def __init__(self, container_url: str, parent=None) -> None:
        super().__init__(parent)
        self.container_url = container_url
        self.content_id: str | None = None
        self.setWindowTitle("FileCrypt bağlantısını hazırla")
        self.setMinimumWidth(560)
        self.setModal(True)

        description = QLabel(
            "1. Tarayıcıda doğrulamayı tamamlayın.\n"
            "2. GoFile sayfası açılınca adres çubuğundaki bağlantıyı kopyalayın.\n"
            "3. Uygulama bağlantıyı panodan otomatik algılayıp indirmeye devam eder."
        )
        description.setWordWrap(True)
        self.link_input = QLineEdit()
        self.link_input.setPlaceholderText("https://gofile.io/d/...")
        self.link_input.returnPressed.connect(self._accept_input)
        self.status = QLabel("GoFile bağlantısı bekleniyor…")
        self.status.setWordWrap(True)

        open_button = QPushButton("FileCrypt'i tarayıcıda aç")
        open_button.clicked.connect(self.open_container)
        paste_button = QPushButton("Panodan al")
        paste_button.clicked.connect(self._paste)
        cancel_button = QPushButton("İptal")
        cancel_button.clicked.connect(self.reject)

        controls = QHBoxLayout()
        controls.addWidget(open_button)
        controls.addStretch()
        controls.addWidget(paste_button)
        controls.addWidget(cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(description)
        layout.addWidget(self.link_input)
        layout.addWidget(self.status)
        layout.addLayout(controls)

        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self._clipboard_changed)

    def open_container(self) -> None:
        if not QDesktopServices.openUrl(QUrl(self.container_url)):
            self.status.setText("FileCrypt bağlantısı varsayılan tarayıcıda açılamadı.")

    def _clipboard_changed(self) -> None:
        value = self.clipboard.text().strip()
        if value:
            self.link_input.setText(value)
            self._try_accept(value, automatic=True)

    def _paste(self) -> None:
        value = self.clipboard.text().strip()
        self.link_input.setText(value)
        self._try_accept(value, automatic=False)

    def _accept_input(self) -> None:
        self._try_accept(self.link_input.text().strip(), automatic=False)

    def _try_accept(self, value: str, *, automatic: bool) -> None:
        try:
            content_id = extract_gofile_content_id(value)
        except SecurityError:
            if not automatic:
                self.status.setText(
                    "Bu bir GoFile paylaşım bağlantısı değil. "
                    "Adres https://gofile.io/d/... biçiminde olmalı."
                )
            return
        self.content_id = content_id
        self.status.setText("GoFile bağlantısı bulundu; indirmeye geçiliyor…")
        self.accept()

    def done(self, result: int) -> None:
        with suppress(RuntimeError):
            self.clipboard.dataChanged.disconnect(self._clipboard_changed)
        super().done(result)
