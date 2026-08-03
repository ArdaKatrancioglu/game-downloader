from pathlib import Path


project_root = Path.cwd()
browser_bundle = project_root / ".build-assets" / "playwright-browsers"
seven_zip_bundle = project_root / ".build-assets" / "7zip"

if not browser_bundle.is_dir():
    raise SystemExit(
        "Bundled Chromium was not found. Run scripts/build_windows.ps1 so the "
        "matching Playwright browser is downloaded before packaging."
    )

browser_datas = [(str(browser_bundle), ".playwright-browsers")]
tool_datas = (
    [(str(seven_zip_bundle), ".7zip")]
    if seven_zip_bundle.is_dir()
    else []
)

a = Analysis(
    [str(project_root / "src" / "game_downloader" / "app.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=browser_datas + tool_datas,
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
