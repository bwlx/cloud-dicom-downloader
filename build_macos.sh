#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="Cloud DICOM Downloader.app"
DMG_NAME="Cloud-DICOM-Downloader-macOS-unsigned.dmg"
STAGE_DIR="$ROOT_DIR/build/dmg"

python -m pip install -r requirements-packaging.txt
python -m playwright install chromium
python -m PyInstaller --noconfirm cloud_dicom_downloader.spec
codesign --remove-signature "dist/$APP_NAME" 2>/dev/null || true

rm -rf "$STAGE_DIR" "dist/$DMG_NAME"
mkdir -p "$STAGE_DIR"
cp -R "dist/$APP_NAME" "$STAGE_DIR/"
ln -sfn /Applications "$STAGE_DIR/Applications"

hdiutil create \
	-volname "Cloud DICOM Downloader" \
	-srcfolder "$STAGE_DIR" \
	-ov \
	-format UDZO \
	"dist/$DMG_NAME"

echo "Built dist/$APP_NAME"
echo "Built dist/$DMG_NAME"
echo "Note: the app is unsigned. For external distribution on macOS, sign and notarize it before release."
