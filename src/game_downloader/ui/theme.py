APP_STYLESHEET = """
QMainWindow, QWidget#appRoot {
    background: #0b1020;
    color: #e8edf7;
    font-family: "Inter", "Segoe UI", "SF Pro Text", sans-serif;
    font-size: 14px;
}
QLabel {
    color: #dce4f3;
}
QFrame#headerCard {
    background: transparent;
}
QLabel#eyebrow {
    color: #7c9cff;
    font-size: 12px;
    font-weight: 700;
}
QLabel#pageTitle {
    color: #f7f9ff;
    font-size: 28px;
    font-weight: 700;
}
QLabel#subtitle, QLabel#mutedLabel {
    color: #8f9bb3;
}
QLabel#sectionTitle {
    color: #f1f4fb;
    font-size: 16px;
    font-weight: 650;
}
QLabel#statusText {
    color: #d6deef;
}
QLabel#stepText {
    color: #7c9cff;
    font-size: 12px;
    font-weight: 600;
}
QFrame.card, QGroupBox {
    background: #121a2e;
    border: 1px solid #24304a;
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
    color: #eef2fb;
}
QLineEdit {
    min-height: 42px;
    padding: 0 13px;
    color: #f4f7ff;
    background: #0d1425;
    border: 1px solid #2a3855;
    border-radius: 10px;
    selection-background-color: #5577ee;
}
QLineEdit:focus {
    border-color: #6d8cff;
}
QListWidget {
    color: #e7ecf7;
    background: #0d1425;
    border: 1px solid #263552;
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
    background: #18243e;
}
QListWidget::item:selected {
    background: #263b70;
    color: #ffffff;
}
QPushButton {
    min-height: 38px;
    padding: 0 16px;
    color: #e9efff;
    background: #202c45;
    border: 1px solid #31415f;
    border-radius: 9px;
    font-weight: 600;
}
QPushButton:hover {
    background: #293957;
    border-color: #48608b;
}
QPushButton:pressed {
    background: #18243a;
}
QPushButton:disabled {
    color: #65718a;
    background: #151d2e;
    border-color: #202a3e;
}
QPushButton#primaryButton {
    min-height: 44px;
    color: #ffffff;
    background: #5577ee;
    border-color: #6e8cff;
}
QPushButton#primaryButton:hover {
    background: #6685f3;
}
QPushButton#primaryButton:disabled {
    color: #71809b;
    background: #19233a;
    border-color: #26334d;
}
QPushButton#quietButton {
    background: transparent;
}
QProgressBar {
    min-height: 10px;
    max-height: 10px;
    color: transparent;
    background: #0c1323;
    border: 0;
    border-radius: 5px;
}
QProgressBar::chunk {
    background: #5f80f4;
    border-radius: 5px;
}
QDialog {
    background: #10172a;
    color: #eef2fa;
}
QFormLayout QLabel, QCheckBox {
    color: #dce4f3;
}
QSpinBox {
    min-height: 36px;
    padding: 0 8px;
    color: #f4f7ff;
    background: #0d1425;
    border: 1px solid #2a3855;
    border-radius: 8px;
}
"""
