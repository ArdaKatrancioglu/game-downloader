from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

DEFAULT_THEME = {
    "background": "#0b1020",
    "surface": "#121a2e",
    "surface_alt": "#0d1425",
    "border": "#24304a",
    "border_focus": "#6d8cff",
    "text": "#e8edf7",
    "muted": "#8f9bb3",
    "accent": "#5577ee",
    "accent_hover": "#6685f3",
    "accent_surface": "#263b70",
    "disabled": "#71809b",
}


def load_theme(path: Path) -> str:
    """Load editable colors from JSON, creating the file on first launch."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            json.dumps(DEFAULT_THEME, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    colors = dict(DEFAULT_THEME)
    try:
        configured = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(configured, dict):
            raise ValueError("theme root must be an object")
        for key, value in configured.items():
            if key in colors and isinstance(value, str) and _COLOR.fullmatch(value):
                colors[key] = value
            elif key in colors:
                logger.warning("Ignoring invalid theme color key=%s value=%r", key, value)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Theme file could not be loaded path=%s error=%s", path, exc)

    stylesheet = _STYLESHEET
    for key, value in colors.items():
        stylesheet = stylesheet.replace(f"@{key}@", value)
    return stylesheet


_STYLESHEET = """
QMainWindow, QWidget#appRoot {
    background: @background@;
    color: @text@;
    font-family: "Inter", "Segoe UI", "SF Pro Text", sans-serif;
    font-size: 14px;
}
QLabel {
    color: @text@;
}
QFrame#headerCard {
    background: transparent;
}
QLabel#pageTitle {
    color: @text@;
    font-size: 28px;
    font-weight: 700;
}
QLabel#mutedLabel {
    color: @muted@;
}
QLabel#sectionTitle {
    color: @text@;
    font-size: 16px;
    font-weight: 650;
}
QLabel#statusText {
    color: @text@;
}
QFrame.card, QGroupBox {
    background: @surface@;
    border: 1px solid @border@;
    border-radius: 14px;
}
QGroupBox {
    margin-top: 12px;
    padding: 18px 16px 14px 16px;
    font-size: 15px;
    font-weight: 650;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: @text@;
}
QLineEdit {
    min-height: 42px;
    padding: 0 13px;
    color: @text@;
    background: @surface_alt@;
    border: 1px solid @border@;
    border-radius: 10px;
    selection-background-color: @accent@;
}
QLineEdit:focus {
    border-color: @border_focus@;
}
QListWidget {
    color: @text@;
    background: @surface_alt@;
    border: 1px solid @border@;
    border-radius: 10px;
    padding: 6px;
    outline: none;
}
QListWidget::item {
    min-height: 46px;
    padding: 8px 10px;
    margin: 3px;
    border-radius: 8px;
}
QListWidget::item:hover {
    background: @surface@;
}
QListWidget::item:selected {
    background: @accent_surface@;
    color: @text@;
}
QPushButton {
    min-height: 38px;
    padding: 0 16px;
    color: @text@;
    background: @surface@;
    border: 1px solid @border@;
    border-radius: 9px;
    font-weight: 600;
}
QPushButton:hover {
    background: @accent_surface@;
    border-color: @border_focus@;
}
QPushButton:pressed {
    background: @surface_alt@;
}
QPushButton:disabled {
    color: @disabled@;
    background: @surface_alt@;
    border-color: @border@;
}
QPushButton#primaryButton {
    min-height: 44px;
    color: @text@;
    background: @accent@;
    border-color: @border_focus@;
}
QPushButton#primaryButton:hover {
    background: @accent_hover@;
}
QPushButton#primaryButton:disabled {
    color: @disabled@;
    background: @surface@;
    border-color: @border@;
}
QPushButton#quietButton {
    background: transparent;
}
QProgressBar {
    min-height: 10px;
    max-height: 10px;
    color: transparent;
    background: @surface_alt@;
    border: 0;
    border-radius: 5px;
}
QProgressBar::chunk {
    background: @accent@;
    border-radius: 5px;
}
QDialog {
    background: @background@;
    color: @text@;
}
QFormLayout QLabel, QCheckBox {
    color: @text@;
}
QSpinBox {
    min-height: 36px;
    padding: 0 8px;
    color: @text@;
    background: @surface_alt@;
    border: 1px solid @border@;
    border-radius: 8px;
}
"""
