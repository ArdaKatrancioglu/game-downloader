from pathlib import Path


project_root = Path.cwd()
browser_bundle = project_root / ".build-assets" / "playwright-browsers"

if not browser_bundle.is_dir():
    raise SystemExit(
        "Bundled Chromium was not found. Run scripts/build_windows.ps1 so the "
        "matching Playwright browser is downloaded before packaging."
    )

browser_datas = [(str(browser_bundle), ".playwright-browsers")]

a = Analysis(
    [str(project_root / "src" / "game_downloader" / "app.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=browser_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="IpsumIndirici",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
