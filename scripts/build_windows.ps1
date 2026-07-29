$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/ and retry."
}

uv sync --extra dev --extra browser --extra packaging

$BrowserBundle = Join-Path $ProjectRoot ".build-assets\playwright-browsers"
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserBundle
uv run playwright install chromium

uv run ruff check .
uv run pytest
uv run pyinstaller --noconfirm --clean authorized_game_downloader.spec

$Executable = Join-Path $ProjectRoot "dist\AuthorizedGameDownloader\AuthorizedGameDownloader.exe"
if (-not (Test-Path $Executable)) {
    throw "Build completed without the expected executable: $Executable"
}

Write-Host ""
Write-Host "Windows build ready:"
Write-Host $Executable
Write-Host "Copy the entire dist\AuthorizedGameDownloader folder when testing another PC."
