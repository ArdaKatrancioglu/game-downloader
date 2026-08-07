from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


@dataclass(frozen=True)
class DownloadChoices:
    destination: Path
    auto_extract: bool
    shutdown_after_completion: bool


class DownloadDialog(QDialog):
    """Xbox-style modal overlay for per-download choices and space checks."""

    def __init__(
        self,
        destination: Path,
        game_size: int | None,
        auto_extract: bool,
        game_title: str = "Oyun",
        game_version: str = "Unknown",
        cover_data: bytes | None = None,
        archive_available: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.game_size = game_size
        self.game_version = game_version
        self.setWindowTitle(f"{game_title}’i indir")
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if parent is not None:
            self.resize(parent.size())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        panel = QFrame()
        panel.setObjectName("downloadModalPanel")
        panel.setStyleSheet(_MODAL_STYLE)
        panel.setMinimumWidth(620)
        panel.setMaximumWidth(680)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(32, 28, 32, 32)
        panel_layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel(f"{game_title}’i indir")
        title.setObjectName("downloadModalTitle")
        title.setWordWrap(True)
        header_text = QVBoxLayout()
        header_text.setSpacing(4)
        header_text.addWidget(title)
        self.header_summary = QLabel()
        self.header_summary.setObjectName("headerSummary")
        header_text.addWidget(self.header_summary)
        header.addLayout(header_text, 1)
        close = QPushButton("✕")
        close.setObjectName("modalCloseButton")
        close.setFixedSize(38, 38)
        close.clicked.connect(self.reject)
        header.addWidget(close)
        panel_layout.addLayout(header)

        game_row = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setObjectName("gameCover")
        self.cover.setFixedSize(64, 64)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if cover_data:
            pixmap = QPixmap()
            if pixmap.loadFromData(cover_data):
                self.cover.setPixmap(pixmap.scaled(
                    self.cover.size(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
        else:
            self.cover.hide()
        game_row.addWidget(self.cover)
        summary_text = QVBoxLayout()
        summary_text.setSpacing(5)
        self.game_title = QLabel(game_title)
        self.game_title.setObjectName("gameTitle")
        self.size_summary = QLabel()
        self.size_summary.setObjectName("sizeSummary")
        summary_text.addWidget(self.game_title)
        summary_text.addWidget(self.size_summary)
        game_row.addLayout(summary_text)
        game_row.addStretch(1)
        panel_layout.addLayout(game_row)

        destination_label = QLabel("İndirme konumu")
        destination_label.setObjectName("sectionTitle")
        panel_layout.addWidget(destination_label)

        path_card = QFrame()
        path_card.setObjectName("destinationCard")
        folder_row = QHBoxLayout(path_card)
        folder_row.setContentsMargins(12, 5, 6, 5)
        folder_row.setSpacing(10)
        self.folder = QLineEdit(str(destination.expanduser()))
        self.folder.setObjectName("destinationPath")
        self.folder.setReadOnly(True)
        self.folder.setToolTip("İndirilecek klasör")
        self.folder.textChanged.connect(self._update_space)
        browse = QPushButton("Değiştir…")
        browse.setObjectName("browseButton")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        panel_layout.addWidget(path_card)
        self.free_label = QLabel()
        self.free_label.setObjectName("freeSpace")
        panel_layout.addWidget(self.free_label)
        self.destination_error = QLabel()
        self.destination_error.setObjectName("spaceErrorLabel")
        panel_layout.addWidget(self.destination_error)

        options_title = QLabel("İndirme seçenekleri")
        options_title.setObjectName("sectionTitle")
        panel_layout.addWidget(options_title)
        options_layout = QVBoxLayout()
        options_layout.setSpacing(12)
        self.auto_extract = QCheckBox("ZIP arşivini otomatik çıkar")
        self.auto_extract.setChecked(auto_extract and archive_available)
        self.auto_extract.setVisible(archive_available)
        self.auto_extract.toggled.connect(self._update_space)
        options_layout.addWidget(self.auto_extract)
        completion_label = QLabel("Tamamlandığında")
        completion_label.setObjectName("fieldLabel")
        options_layout.addWidget(completion_label)
        self.completion = QComboBox()
        self.completion.setMinimumHeight(46)
        self.completion.addItem("Hiçbir şey yapma", False)
        self.completion.addItem("Bilgisayarı kapat", True)
        options_layout.addWidget(self.completion)
        self.required_space_label = QLabel()
        self.required_space_label.setObjectName("secondaryText")
        options_layout.addWidget(self.required_space_label)
        panel_layout.addLayout(options_layout)

        self.space_error = QLabel()
        self.space_error.setObjectName("spaceErrorLabel")
        panel_layout.addWidget(self.space_error)
        self.download_button = QPushButton("İndirmeyi başlat")
        self.download_button.setObjectName("primaryButton")
        self.download_button.setMinimumHeight(48)
        self.download_button.clicked.connect(self.accept)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Vazgeç")
        cancel.setObjectName("secondaryButton")
        cancel.setMinimumHeight(48)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        actions.addWidget(self.download_button)
        panel_layout.addLayout(actions)

        row.addWidget(panel)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()
        self.setStyleSheet("QDialog { background: rgba(0, 0, 0, 190); }")
        self._update_space()

    def choices(self) -> DownloadChoices:
        return DownloadChoices(
            destination=Path(self.folder.text()).expanduser(),
            auto_extract=self.auto_extract.isChecked(),
            shutdown_after_completion=bool(self.completion.currentData()),
        )

    def mousePressEvent(self, event) -> None:
        panel = self.findChild(QFrame, "downloadModalPanel")
        if panel is not None and not panel.geometry().contains(event.position().toPoint()):
            self.reject()
            return
        super().mousePressEvent(event)

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "İndirme yeri", self.folder.text()
        )
        if selected:
            self.folder.setText(selected)

    def _required_space(self) -> int | None:
        if self.game_size is None:
            return None
        # Extraction temporarily keeps both the archive and extracted content.
        return int(self.game_size * (2.5 if self.auto_extract.isChecked() else 1.0))

    def _update_space(self, _value: object = None) -> None:
        folder = Path(self.folder.text()).expanduser()
        probe = folder
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            free = shutil.disk_usage(probe).free
        except OSError:
            free = None
        required = self._required_space()
        valid_folder = not folder.exists() or folder.is_dir()
        writable_folder = False
        probe = folder
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if valid_folder and probe.exists():
            writable_folder = bool(os.access(probe, os.W_OK))
        enough = bool(self.folder.text().strip()) and valid_folder and free is not None and (
            required is None or free >= required
        ) and writable_folder
        formatted_free = _format_bytes(free)
        self.free_label.setText(f"{formatted_free} kullanılabilir")
        summary = f"Sürüm {self.game_version} · {_format_bytes(self.game_size)}"
        self.header_summary.setText(summary)
        self.size_summary.setText(summary)
        self.required_space_label.setText(
            (
                f"İndirme: {_format_bytes(self.game_size)} · "
                f"Tahmini toplam alan: {_format_bytes(required)}"
            )
            if self.auto_extract.isChecked() and required is not None
            else ""
        )
        if not self.folder.text().strip():
            error = "Bir indirme klasörü seçin."
        elif not valid_folder:
            error = "Bu yol bir klasör değil."
        elif not writable_folder:
            error = "Bu klasöre yazma izni bulunmuyor."
        elif free is None:
            error = "Disk alanı kontrol edilemedi."
        elif free is not None and required is not None and free < required:
            error = f"En az {_format_bytes(required)} boş alan gerekiyor."
        else:
            error = ""
        self.destination_error.setText(error)
        self.destination_error.setVisible(bool(error))
        self.space_error.clear()
        self._set_destination_state(enough)
        self.download_button.setEnabled(enough)

    def _set_destination_state(self, enough: bool) -> None:
        self.findChild(QFrame, "destinationCard").setProperty("insufficient", not enough)
        card = self.findChild(QFrame, "destinationCard")
        if card is not None:
            card.style().unpolish(card)
            card.style().polish(card)


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "Bilinmiyor"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "Bilinmiyor"


_MODAL_STYLE = """
QFrame#downloadModalPanel { background: #121a2e; border: 1px solid #344363; border-radius: 16px; }
QLabel#downloadModalTitle { color: #f3f6ff; font-size: 21px; font-weight: 700; }
QLabel#headerSummary, QLabel#sizeSummary, QLabel#secondaryText { color: #aeb9ce; font-size: 13px; }
QLabel#gameTitle { color: #f3f6ff; font-size: 16px; font-weight: 700; }
QLabel#gameCover { background: #202b43; border-radius: 10px; }
QLabel#sectionTitle { color: #f3f6ff; font-size: 15px; font-weight: 700; margin-top: 8px; }
QLabel#fieldLabel { color: #cbd4e5; font-size: 13px; font-weight: 600; }
QFrame#destinationCard { background: #1b2438; border: 1px solid #3a4968; border-radius: 10px; }
QFrame#destinationCard:hover { border-color: #5577ee; }
QFrame#destinationCard[insufficient="true"] { border-color: #c56b73; }
QLineEdit#destinationPath {
    min-height: 42px; border: 0; background: transparent;
    color: #e8edf8; padding: 0 2px;
}
QPushButton#browseButton {
    min-height: 34px; padding: 0 12px; background: transparent;
    border: 1px solid #52688f; border-radius: 8px; color: #dce7ff;
}
QPushButton#browseButton:hover { background: rgba(255,255,255,0.12); }
QPushButton#modalCloseButton {
    min-height: 36px; padding: 0; background: transparent; border: 0;
    color: #8f9bb3; font-size: 17px;
}
QPushButton#modalCloseButton:hover { color: #ffffff; background: #263b70; border-radius: 8px; }
QLabel#freeSpace { color: #aeb9ce; font-size: 12px; }
QLabel#spaceErrorLabel { color: #ffb0b6; font-size: 12px; padding: 2px 0; }
QComboBox {
    min-height: 44px; padding: 0 12px; background: #1b2438;
    border: 1px solid #3a4968; border-radius: 10px; color: #e8edf8;
}
QComboBox:hover, QComboBox:focus { border-color: #5577ee; }
QPushButton#primaryButton {
    min-height: 48px; padding: 0 18px; border-radius: 10px;
    background: #5577ee; border: 1px solid #7f9cff; color: #ffffff;
    font-size: 15px; font-weight: 700;
}
QPushButton#primaryButton:hover { background: #6685f3; }
QPushButton#primaryButton:disabled { background: #26314a; border-color: #344363; color: #71809b; }
QPushButton#secondaryButton {
    min-height: 48px; padding: 0 18px; background: #26314a;
    border: 1px solid #344363; border-radius: 10px; color: #d1d7e5;
}
QPushButton#secondaryButton:hover { background: #344363; color: #ffffff; }
"""
