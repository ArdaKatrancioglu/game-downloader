# Authorized Game Downloader

A Windows/macOS PySide6 application for finding, downloading, validating, and
safely extracting game archives that you own or are explicitly authorized to
download and distribute.

This project does **not** integrate with SteamRIP or any other piracy catalog.
It does not bypass CAPTCHA, Cloudflare, advertising, timers, sessions, or any
other access control. Never use it for content you do not have permission to
download.

## Install and run

Python 3.12 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e .
authorized-game-downloader
```

With [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
uv run authorized-game-downloader
```

Open **Settings** before the first remote download. Choose either a local JSON
catalog or an HTTPS catalog URL, set the exact allowed catalog domains, choose
a download folder, and save the settings.

## GoFile browser integration

The app uses GoFile's public download-page flow and does not call the GoFile
API. This is intentional: GoFile documents public download pages for free
users, while direct links and full API access are Premium features.

After selecting an authorized result, click **Download**. When **visible
GoFile browser downloads (no API)** is enabled in Settings, the app opens a
dedicated visible Chromium profile at exactly
`https://gofile.io/d/<contentId>`. It waits for exactly one visible control
whose accessible name is **Download**, clicks it, captures Playwright's browser
download event, saves into `<filename>.part`, and publishes the final filename
only after Chromium reports successful completion. The Chromium window closes
automatically after the file is saved.

The browser is deliberately headed rather than hidden. If GoFile presents
CAPTCHA or human verification, complete it yourself in that visible window.
The app never solves, suppresses, or disguises automation from a challenge. If
multiple visible Download controls exist, automatic selection stops rather
than guessing; free bulk-download behavior is not emulated.

Install the optional browser support and Chromium once:

```bash
uv sync --extra dev --extra browser
uv run playwright install chromium
```

The dedicated browser profile can be remembered under the application state
folder. Unexpected non-GoFile download hosts are rejected; GoFile subdomains
are accepted for the captured file transfer. No API token, daily Chrome
profile, system-browser handoff, CAPTCHA solver, stealth plugin, user-agent
spoofing, or hidden challenge-token extraction is used.

## Catalogs

### Local JSON

Copy `catalog.example.json`, replace its placeholder with your own GoFile
content ID, and point Settings at the copy:

```json
{
  "games": [{
    "id": "demo-game",
    "title": "Demo Game",
    "version": "1.0.0",
    "description": "Authorized demo content",
    "archive_size": 123456,
    "source": {
      "type": "gofile",
      "content_id": "REPLACE_WITH_OWN_CONTENT_ID"
    }
  }]
}
```

Invalid records are skipped and logged without stopping valid results.

### User-controlled HTML catalog

Only configure a site that you own or control. Searching issues:

```text
GET https://your-catalog.example/?s=<query>
```

The search page may use the stable contract:

```html
<article data-game-id="demo-game"
         data-title="Demo Game"
         data-version="1.0.0"
         data-provider="gofile"
         data-archive-size="123456">
  <a href="/demo-game">Details</a>
</article>
```

The app also tolerates a result anchor using
`class="all-over-thumb-link"` and a `.screen-reader-text` title. It displays
results before following anything. Only the selected same-domain detail page
is fetched. That page must contain a visible approved anchor:

```html
<a href="https://gofile.io/d/YOUR_CONTENT_ID">DOWNLOAD HERE</a>
```

Catalog pages are parsed as inert HTML. Scripts are never executed, external
catalog links are not followed, HTTPS and the exact domain allowlist are
enforced, and unexpected redirects stop the operation.

## Downloads and extraction

Captured GoFile browser downloads are never loaded fully into application
memory. Playwright owns the network transfer; the app saves to
`<filename>.part` and atomically renames after completion. The UI warns below
1.5× free-space headroom. Playwright does not expose byte-level progress for a
browser `Download`, so this path uses an indeterminate progress indicator.

ZIP and TAR formats use the Python standard library. RAR and 7z require an
already installed `7zz` or `7z` executable from the [official 7-Zip
site](https://www.7-zip.org/); the app never downloads or installs it.

Before extraction, entries are listed and checked for absolute/drive paths,
`..` traversal, link/device escapes, file-count and total-size limits, and
suspicious compression ratios. Extraction occurs in a temporary sibling folder
and becomes visible only on success. A nonempty destination is never
overwritten. The application never launches installers, executables, scripts,
or extracted applications; after success it only offers to open the folder.

## Tests and checks

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
```

Tests use local fixtures, `httpx.MockTransport`, and mocked Playwright page
objects. They never contact a live catalog or GoFile and never need an account.
The end-to-end test covers search, selection, GoFile content-ID extraction,
captured local archive transfer, and safe extraction.

Optional manual smoke test: upload a tiny archive you created to your own
GoFile account, put its public content ID in a private local catalog, and run
the browser download/extraction flow using only that test share.

## Diagnosing HTTP 403 responses

The application writes request/response diagnostics to
`~/.authorized-game-downloader/application.log` (or
`$GAME_DOWNLOADER_STATE_DIR/application.log` when that override is set).
Each request records its operation, method, sanitized URL and request headers;
each response records status, elapsed time, HTTP version, redirects, server,
content type, `CF-Ray`, request ID, and a short textual error-body preview.
Authorization, cookies, token-like query parameters, and token-like response
fields are redacted.

To watch the log while reproducing a failure on macOS or Linux:

```bash
tail -f ~/.authorized-game-downloader/application.log
```

Compare the failing entry's URL, method, `User-Agent`, redirect chain, and
response headers with `curl -v`. A catalog response can legitimately differ
because curl and the application have different headers, proxy/environment
settings, cookies, IP reputation, or edge-cache paths. The application does
not add browser impersonation, cookies, CAPTCHA handling, or anti-bot bypasses.

## Packaging

Install PyInstaller and build on each target operating system:

```bash
uv sync --extra packaging
uv run pyinstaller authorized_game_downloader.spec
```

Build Windows artifacts on Windows and macOS artifacts on macOS. Code signing,
notarization, and installer creation remain release-owner responsibilities.

## Security limitations and known limitations

- Authorization and catalog ownership are user responsibilities.
- GoFile's public download-page UI can change. Browser automation stops safely
  rather than guessing when its accessible Download control no longer matches.
- A share with multiple visible files requires explicit selection and is not
  automatically downloaded in this first browser-only release.
- DNS checks reduce SSRF risk but cannot eliminate DNS rebinding between
  resolution and connection.
- RAR/7z safety depends partly on the locally installed 7-Zip implementation;
  extracted output is checked again before it is published.
- Password-protected archives are not supported in this first release.
- Packaging does not install 7-Zip automatically.
