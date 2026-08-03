$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/ and retry."
}

uv sync --extra dev --extra packaging --frozen

# Download the Chromium revision pinned by uv.lock into a project-local folder.
# PyInstaller embeds this folder in the one-file executable below.
$BrowserBundle = Join-Path $ProjectRoot ".build-assets\playwright-browsers"
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserBundle
uv run playwright install chromium

uv run ruff check .
uv run pytest
uv run pyinstaller --noconfirm --clean ipsum_indirici.spec

$Executable = Join-Path $ProjectRoot "dist\IpsumIndirici.exe"
if (-not (Test-Path $Executable)) {
    throw "Build completed without the expected executable: $Executable"
}

Write-Host ""
Write-Host "Windows build ready:"
Write-Host $Executable
Write-Host "Bu tek EXE dosyasını başka bir Windows bilgisayara doğrudan kopyalayabilirsiniz."
