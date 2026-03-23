from pathlib import Path

from crawlers import _browser


def test_find_packaged_chromium_supports_google_chrome_for_testing(monkeypatch, tmp_path):
	root = tmp_path / "runtime"
	executable = root / "ms-playwright" / "chromium-1208" / "chrome-mac-arm64" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"
	executable.parent.mkdir(parents=True)
	executable.write_text("", encoding="utf-8")

	monkeypatch.setattr(_browser.sys, "platform", "darwin")
	monkeypatch.setattr(_browser, "_runtime_search_roots", lambda: [root])

	assert _browser._find_packaged_chromium() == executable
