import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("playwright")
binaries = []
datas = [("catalog.example.json", ".")]

if sys.platform == "darwin":
    import PySide6

    pyside_folder = Path(PySide6.__file__).parent
    pyside_runtime = next(pyside_folder.glob("libpyside6*.dylib"))
    # PyInstaller/PySide can rewrite QtGui's @rpath to this root location.
    binaries.append((str(pyside_runtime), "."))

browser_root_value = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
if browser_root_value:
    browser_root = Path(browser_root_value)
    if browser_root.is_dir():
        datas.append((str(browser_root), "playwright-browsers"))

a = Analysis(
    ["src/game_downloader/app.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["packaging/playwright_runtime.py"],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AuthorizedGameDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AuthorizedGameDownloader",
)
