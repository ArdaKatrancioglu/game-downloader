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

# Bundle 7-Zip so ZIP methods unsupported by Python (for example Deflate64)
# work on target computers without requiring WinRAR or 7-Zip installation.
$SevenZipExe = Join-Path $env:ProgramFiles "7-Zip\7z.exe"
if (-not (Test-Path $SevenZipExe)) {
    winget install --id 7zip.7zip --exact --silent --accept-package-agreements --accept-source-agreements
}
if (-not (Test-Path $SevenZipExe)) {
    throw "7-Zip installation completed without the expected executable: $SevenZipExe"
}
$SevenZipSource = Split-Path -Parent $SevenZipExe
$SevenZipBundle = Join-Path $ProjectRoot ".build-assets\7zip"
New-Item -ItemType Directory -Force -Path $SevenZipBundle | Out-Null
Copy-Item (Join-Path $SevenZipSource "7z.exe") $SevenZipBundle -Force
Copy-Item (Join-Path $SevenZipSource "7z.dll") $SevenZipBundle -Force
$SevenZipLicense = Join-Path $SevenZipSource "License.txt"
if (Test-Path $SevenZipLicense) {
    Copy-Item $SevenZipLicense $SevenZipBundle -Force
}

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
