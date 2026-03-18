# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")
pydicom_datas, pydicom_binaries, pydicom_hiddenimports = collect_all("pydicom")
cv2_datas, cv2_binaries, cv2_hiddenimports = collect_all("cv2")

datas = playwright_datas + pydicom_datas
binaries = playwright_binaries + pydicom_binaries
hiddenimports = playwright_hiddenimports + pydicom_hiddenimports
datas += cv2_datas
binaries += cv2_binaries
hiddenimports += cv2_hiddenimports


def browser_cache_roots():
	roots = []
	if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
		roots.append(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]).expanduser())

	if sys.platform == "win32":
		local_app_data = os.environ.get("LOCALAPPDATA")
		if local_app_data:
			roots.append(Path(local_app_data) / "ms-playwright")
		roots.append(Path.home() / "AppData" / "Local" / "ms-playwright")
	elif sys.platform == "darwin":
		roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")
	else:
		roots.append(Path.home() / ".cache" / "ms-playwright")

	unique = []
	for root in roots:
		if root not in unique:
			unique.append(root)
	return unique


for browser_cache in browser_cache_roots():
	if browser_cache.exists():
		for child in browser_cache.iterdir():
			if child.is_dir() and (child.name.startswith("chromium-") or child.name.startswith("ffmpeg-")):
				datas.append((str(child), f"ms-playwright/{child.name}"))

a = Analysis(
	["desktop_app.py"],
	pathex=[],
	binaries=binaries,
	datas=datas,
	hiddenimports=hiddenimports,
	hookspath=[],
	hooksconfig={},
	runtime_hooks=[],
	excludes=[],
	noarchive=False,
	optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
	pyz,
	a.scripts,
	[],
	exclude_binaries=True,
	name="Cloud DICOM Downloader",
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
	upx_exclude=[],
	name="Cloud DICOM Downloader",
)

if sys.platform == "darwin":
	app = BUNDLE(
		coll,
		name="Cloud DICOM Downloader.app",
		icon=None,
		bundle_identifier="com.codex.cloud-dicom-downloader",
	)
else:
	app = coll
