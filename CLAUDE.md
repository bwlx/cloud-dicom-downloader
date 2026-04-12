# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Medical DICOM image downloader for Chinese medical cloud platforms. Downloads CT/MRI DICOM files from online medical reports. Each supported hospital/platform has its own crawler module. The project provides both a CLI and a PySide6 desktop GUI.

## Commands

```bash
# CLI download
python downloader.py <url> [password] [--raw] [--output <dir>]

# Desktop app
pip install -r requirements-desktop.txt
python desktop_app.py

# Run all tests
pytest

# Run single test file
pytest test/test_hinacom.py

# Dev dependencies (includes pylibjpeg, numpy, pytest)
pip install -r requirements-dev.txt

# macOS build
./build_macos.sh [version]

# Windows build (on Windows)
.\build_windows.ps1
```

## Architecture

### URL Routing
`desktop_core.py` is the central dispatcher. `resolve_crawler_module(url)` maps a URL's host to the correct crawler module. `run_download_request(DownloadRequest)` orchestrates the full download: resolves the module, assembles args (url, optional password, optional `--raw`), sets the output directory via env var, and calls `module.run(*args)`.

### Crawler Modules (`crawlers/`)
Each site-specific crawler (e.g., `hinacom.py`, `shdc.py`, `ftimage.py`) exposes an async `run(*args)` function. The first arg is always the URL; additional args vary by site (password, `--raw` flag).

Two categories of crawlers:
- **HTTP-only**: Use `aiohttp` via `_utils.new_http_client()` to call site APIs directly and download DICOM files. Most crawlers are this type.
- **Browser-based**: Use Playwright via `_browser.py` for sites requiring JavaScript rendering (e.g., `ftimage`, `radonline`). These extend `PlaywrightCrawler` and run inside a managed Chromium instance.

### Shared Utilities (`crawlers/_utils.py`)
- `new_http_client()` - Creates aiohttp sessions with default headers, SSL via certifi, and response-dump-on-error
- `SeriesDirectory` - Manages output directory creation, zero-padded filenames, download tracking, and completeness checking
- `download_to_path()` / `download_bytes()` - Atomic downloads with retry and size validation
- `retry_async()` - Retry with exponential backoff for transient network errors
- `suggest_save_dir(patient, desc, datetime)` - Standard output path: `[patient]-[desc]-[datetime]`
- `pathify()` - Replaces illegal filename characters with full-width Unicode equivalents

### Output Directory
Controlled by env var `CDD_DOWNLOAD_ROOT` (defined in `runtime_config.py`). Default is `./download`. The desktop app sets this via `configured_output_dir()` context manager.

### Testing
- pytest with `asyncio_mode = auto` (no need for `@pytest.mark.asyncio`)
- Tests focus on parsing/logic, not live network calls
- Fixtures in `test/fixtures/`
- **测试中不得包含真实的患者链接、域名或参数**，一律使用虚构的占位值（如 `example-hospital.invalid`、`share-id-001`）

### Key Interfaces
- `DownloadRequest` dataclass: `url`, `password`, `raw`, `output_dir`
- `url_requires_password(url)` / `url_password_prompt(url)` - Password requirement checks
- `url_supports_raw(url)` - Only `medicalimagecloud.com` and `cq12320`

### Desktop App
- `desktop_app.py` - PySide6 GUI entry point
- `desktop_core.py` - Core logic shared between CLI and GUI
- `desktop_encoding.py` - Character encoding utilities
- `desktop_qr.py` - QR code scanning from local images

### Adding a New Crawler
1. Create `crawlers/<site>.py` with an async `run(*args)` function
2. Import and add host mapping in `desktop_core.py:resolve_crawler_module()`
3. If site needs password, update `url_requires_password()` and `url_password_prompt()`
4. Use `SeriesDirectory` + `suggest_save_dir()` for consistent output structure
