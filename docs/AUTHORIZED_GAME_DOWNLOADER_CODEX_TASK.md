# Authorized Game Downloader — Codex Implementation Task

## 1. Goal

Build a simple desktop application in Python for Windows and macOS. It must help a user find, download, and safely extract only game archives that they own or are explicitly authorized to download and distribute.

The intended workflow is:

1. The user enters a catalog URL they control.
2. The app searches that catalog by requesting `/?s=<query>`.
3. The app shows the matching results as numbered or card-based choices.
4. The user picks one result.
5. The app follows the selected result page, finds the visible download section, and extracts the `DOWNLOAD HERE` link.
6. The app resolves that link to the authorized GoFile source.
7. The app downloads the archive through the official GoFile API whenever possible.
8. The app safely extracts the archive only after explicit confirmation.

## 2. Non-negotiable boundaries

- Do not bypass CAPTCHA, Cloudflare checks, ads, timers, anti-bot systems, sessions, or any other access control.
- Never automatically launch downloaded executables.
- Do not request administrator privileges.
- Restrict source domains through an allowlist.
- Stop the download and show a clear warning when an unexpected domain is reached.
- Use the official GoFile API before considering browser automation.
- A browser fallback is allowed only for the user’s own GoFile share, and only when explicitly enabled.
- If human verification is needed, use a visible browser and require the user to complete it.

## 3. Catalog workflow to implement

The first release must support a user-controlled HTML catalog with this behavior:

1. Search by requesting `GET <catalog-url>/?s=<query>`.
2. Parse the returned HTML search page.
3. Show each matching result title as a selectable item.
4. When the user selects a result, fetch the result page.
5. Parse the page for a visible `DOWNLOAD HERE` link or equivalent approved download anchor.
6. Extract the GoFile URL or GoFile share identifier from that link.
7. Pass the result to the GoFile storage layer.

This is a controlled HTML contract for a site the user owns. Do not scrape arbitrary third-party pages.

### Example HTML shape

The implementation should tolerate pages that resemble the following patterns:

```html
<a href="suevical" class="all-over-thumb-link">
  <span class="screen-reader-text">Suevical</span>
</a>

<p style="text-align: center;">
  <span style="color: #ff9900;">
    <strong>GOFILE</strong>
  </span>
  <br>
  <a href="//gofile.io/d/example123" target="_blank" rel="nofollow" class="shortc-button medium purple">
    DOWNLOAD HERE
  </a>
</p>
```

The parser should be resilient to cosmetic HTML changes, but it must stay within the allowlisted domain and must not execute page scripts.

## 4. GoFile research notes

Official documentation: <https://gofile.io/api>

- The REST API is documented as beta, so isolate it behind an adapter layer.
- It uses `Authorization: Bearer <token>` authentication.
- `GET /contents/{contentId}` retrieves folder/content information.
- `GET /contents/search` searches content within an account folder.
- `POST /contents/{contentId}/directlinks` creates direct access links.
- Some operations may require a Premium account. Explain 401, 403, 404, and 429 responses clearly in the UI.
- Never place API tokens in source code, logs, Git, or plain-text settings files.

Before writing the integration, verify the current official documentation. Do not invent endpoints, response fields, or undocumented behavior. If the API schema is unexpected, fail safely.

## 5. Technology choices

- Python 3.12, or the current supported stable Python available in the project
- Desktop UI: PySide6
- HTTP client: `httpx`
- Optional browser fallback: Playwright for Python with Chromium
- Models and validation: `pydantic`
- Settings: `pydantic-settings`
- Secure token storage: `keyring`
- Tests: `pytest`, `pytest-asyncio`, `respx`
- Linting: `ruff`
- Packaging: PyInstaller
- ZIP/TAR extraction: Python standard library
- RAR/7z extraction: an explicitly installed 7-Zip/7zz executable

Use `pyproject.toml`. If no package manager is already present, make the project compatible with `uv` while retaining normal `pip` installation support.

## 6. User experience

Build one main window with:

1. A prominent search field labelled “Enter a game title”.
2. A large “Search” button.
3. Result cards showing title, version, approximate download size, source name, and a “Select” button.
4. After selection: archive name and size, destination selector, available disk space, and a “Download” button.
5. During download: percent, downloaded/total size, current speed, estimated time remaining, pause, resume, and cancel controls.
6. On completion: file location, validation result, and an “Extract archive” button.
7. After extraction: only offer to open the destination folder. Never start an installer or executable.

Do not show raw stack traces to users. Save diagnostic details to logs and show short, clear English error messages in the UI.

## 7. Catalog provider design

Design a `CatalogProvider` protocol or abstract class:

```python
class CatalogProvider(Protocol):
    async def search(self, query: str) -> list[GameEntry]: ...
    async def get_release(self, game_id: str) -> GameRelease: ...
```

Implement two safe providers in the first release.

### 7.1 Local JSON catalog

Create `catalog.example.json` using this approximate schema:

```json
{
  "games": [
    {
      "id": "demo-game",
      "title": "Demo Game",
      "version": "1.0.0",
      "description": "Authorized demo content",
      "archive_size": 123456,
      "source": {
        "type": "gofile",
        "content_id": "REPLACE_WITH_OWN_CONTENT_ID"
      }
    }
  ]
}
```

Validate it with Pydantic. Invalid records must be skipped and logged without crashing the application.

### 7.2 User-controlled HTML catalog

Do not scrape arbitrary third-party pages. Instead, define a stable HTML contract for a page the user controls:

```html
<article
  data-game-id="demo-game"
  data-title="Demo Game"
  data-version="1.0.0"
  data-provider="gofile"
  data-content-id="OWN_CONTENT_ID">
</article>
```

- Fetch only HTTPS catalog domains explicitly allowed in settings.
- Use `selectolax` or `BeautifulSoup` to parse the HTML.
- Do not start Playwright for catalogs that do not require JavaScript.
- Treat all page text as data; never execute it as code.
- Do not automatically follow external links found in the page.
- Skip and log incomplete entries.
- Add local HTML fixtures for unit tests.

## 8. GoFile storage provider

Create a `StorageProvider` interface and implement `GoFileApiProvider`.

Responsibilities:

- Safely extract a `contentId` from a shared URL when needed.
- Read the user’s token from the operating system secure keychain.
- Retrieve content information through the official GoFile API.
- If a folder contains multiple files, let the user select the required file.
- Normalize filename, MIME type, and size data.
- Use an official API response or the official direct-link endpoint to obtain a download URL.
- Translate 401/403 into token/plan-access guidance.
- Respect `Retry-After` on 429; otherwise use limited exponential backoff.
- Never log raw API responses. When a schema mismatch occurs, redact tokens and URL parameters in diagnostic data.

Direct links must be considered temporary. Request a fresh one when a download needs to be restarted.

### 8.1 Controlled browser fallback

Implement a separate `GoFileBrowserFallback`, disabled by default and used only when the user has enabled it for their own GoFile content:

- Launch Playwright Chromium in visible mode.
- Navigate only to a validated `https://gofile.io/d/<contentId>` URL.
- Try to locate the user-visible control with accessible name “Download”.
- Capture a started download with Playwright’s `expect_download` event.
- If CAPTCHA or human verification appears, never solve it automatically. Ask the user to finish it in the visible browser and wait for a limited time.
- Close and stop on popups, ad tabs, or an unexpected domain.
- Do not extract hidden tokens from the DOM, replay private network requests, or circumvent any protection.
- The official API remains the default path.

## 9. Download manager

Implement `DownloadManager` with:

- Streaming downloads; never load a complete archive into memory.
- A `.part` temporary suffix.
- HTTP Range resume when the server supports it.
- A clear restart-from-zero notice when Range is unsupported.
- A maximum redirect count.
- Scheme and domain validation for every redirect.
- TLS verification always enabled.
- Connection/read timeouts.
- Limited retries with exponential backoff.
- Moving-average speed and ETA calculation.
- A lock preventing concurrent downloads to the same destination.
- Exact final-size checks when an expected size is known.
- SHA-256 verification when a trusted source supplies a checksum.
- Atomic removal of the `.part` suffix after completion.
- A warning when free space is less than 1.5× the expected archive size.

Do not broadly hard-code arbitrary CDN hosts. Permit only an HTTPS link issued by the official GoFile API. Reject a link that resolves to a local or private IP.

## 10. Secure archive extraction

Create `ArchiveExtractor`.

- Supported formats: `.zip`, `.tar`, `.tar.gz`, `.tgz`, `.7z`, `.rar`.
- Do not rely solely on extensions; validate file signatures where practical.
- List archive contents before extraction.
- Reject absolute paths, `..` traversal, drive prefixes, symlink/hardlink escapes, and any path that would leave the destination folder.
- Enforce configurable limits for total extracted size and file count.
- Warn on suspiciously high compression ratios to mitigate archive bombs.
- Never overwrite existing files without explicit user confirmation.
- Extract into a new temporary directory, then move it to the destination only on success.
- Safely clean incomplete extraction output after failure.
- Never execute `.exe`, `.msi`, `.bat`, `.cmd`, `.ps1`, `.app`, or similar files.
- If 7-Zip/7zz is absent for RAR/7z, do not download it automatically; show the user an official installation instruction instead.

## 11. Settings and privacy

Provide settings for:

- Catalog URL or local catalog file
- Allowed catalog domains
- Default download folder
- “Save / Test / Remove” controls for the GoFile API token
- Maximum extracted archive size
- Browser-fallback enable/disable switch
- Log level

Token rules:

- Store tokens in the OS keychain through `keyring`.
- Do not write tokens in plain text to settings, `.env`, or logs.
- Mask tokens in the UI.
- Never require a real token in tests.

## 12. Suggested project structure

```text
authorized-game-downloader/
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
├── catalog.example.json
├── src/
│   └── game_downloader/
│       ├── __init__.py
│       ├── app.py
│       ├── models.py
│       ├── settings.py
│       ├── security.py
│       ├── catalog/
│       │   ├── base.py
│       │   ├── json_provider.py
│       │   └── owned_html_provider.py
│       ├── storage/
│       │   ├── base.py
│       │   ├── gofile_api.py
│       │   └── gofile_browser_fallback.py
│       ├── download/
│       │   ├── manager.py
│       │   └── progress.py
│       ├── archive/
│       │   └── extractor.py
│       └── ui/
│           ├── main_window.py
│           ├── settings_dialog.py
│           └── workers.py
└── tests/
    ├── fixtures/
    ├── test_catalog_json.py
    ├── test_catalog_html.py
    ├── test_gofile_api.py
    ├── test_download_manager.py
    ├── test_archive_security.py
    └── test_security.py
```

## 13. Test strategy

Do not write tests that contact live third-party websites.

- Mock GoFile API responses with `respx`.
- Test small files, interrupted connections, Range support and lack of Range support, redirects, 401, 403, 404, 429, and malformed JSON.
- Use a local temporary HTTP server for download/resume integration tests.
- Test ZIP Slip, absolute paths, symlink escapes, archive bombs, excessive file counts, and overwrite protection.
- Test that a fake token never appears in logs.
- Keep service logic separate from the UI so it can be tested without the UI.
- Do not run a live Playwright fallback test by default. Document an optional smoke test only for the user’s own test share.

## 14. README requirements

The README must cover:

- The owned/authorized-content-only policy
- Installation and launch steps
- Where to obtain a GoFile API token
- How to build an example catalog
- The HTML contract for a user-controlled catalog
- API-first versus browser-fallback behavior
- 7-Zip/7zz requirements
- Security limitations
- Running tests
- Packaging with PyInstaller
- Known limitations

Do not include a token, a real private share link, or copyrighted sample content.

## 15. Implementation order

1. Inspect the existing workspace, any `AGENTS.md` files, and user changes.
2. Write a short implementation plan.
3. Implement models, security helpers, and the local JSON catalog provider.
4. Implement the GoFile API adapter according to current official documentation.
5. Complete the download manager and its tests.
6. Complete the secure extractor and attack-case tests.
7. Connect the services to the PySide6 UI.
8. Add the controlled Playwright fallback last, as a separate default-off feature.
9. Complete the README and packaging configuration.
10. Run formatting, static checks, and the complete test suite.
11. Validate the full flow with the local example catalog and a local test file.

## 16. Acceptance criteria

- The search, selection, download, and safe-extraction flow works.
- The UI remains responsive during downloads.
- Large files are not held entirely in memory.
- Interrupted downloads resume when the server supports it.
- API tokens never appear in source, settings, or logs.
- Unknown domains and private-IP redirects are rejected.
- All archive traversal tests pass.
- No executable is automatically launched.
- GoFile API errors are translated into understandable English messages.
- Playwright is not started when the API path is available.
- No CAPTCHA or anti-bot bypass code exists.
- `ruff check .` and `pytest` pass.
- The README is enough to install in a clean environment.

## 17. How Codex should work

- Make safe, reasonable decisions without unnecessary questions.
- Do not delete or overwrite user changes.
- Verify GoFile endpoints and response schemas against the official documentation; do not invent undocumented fields.
- If the API documentation has changed, follow the current official version and document the difference in the README.
- Do not request secrets or a real account token.
- Run relevant tests after each major stage.
- At handoff, briefly report modified files, validation steps, test results, and any remaining real-world verification the user must perform with their own GoFile account.