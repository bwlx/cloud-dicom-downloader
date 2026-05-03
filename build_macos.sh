#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

VERSION="${1:-${BUILD_VERSION:-0.1.0}}"
APP_NAME="Cloud DICOM Downloader.app"
SAFE_VERSION="$(printf '%s' "$VERSION" | sed 's/[^0-9A-Za-z._-]/-/g')"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAC_ARCH="${2:-${MACOS_BUILD_ARCH:-$(uname -m)}}"
case "$MAC_ARCH" in
    x86_64|intel)
        MAC_ARCH="intel"
        ;;
    arm64|apple-silicon|apple_silicon)
        MAC_ARCH="apple-silicon"
        ;;
    *)
        echo "Unsupported macOS architecture label: $MAC_ARCH" >&2
        exit 1
        ;;
esac
if [ "$MAC_ARCH" = "intel" ]; then
    TARGET_MACHINE="x86_64"
else
    TARGET_MACHINE="arm64"
fi
DMG_NAME="Cloud-DICOM-Downloader-macOS-${MAC_ARCH}-unsigned-${SAFE_VERSION}.dmg"
STAGE_DIR="$ROOT_DIR/build/dmg"
APP_EXE="dist/$APP_NAME/Contents/MacOS/Cloud DICOM Downloader"

PYTHON_MACHINE="$($PYTHON_BIN - <<'PY'
import platform
print(platform.machine())
PY
)"

if [ "$PYTHON_MACHINE" != "$TARGET_MACHINE" ]; then
    echo "Python interpreter arch ($PYTHON_MACHINE) does not match requested macOS target ($TARGET_MACHINE)." >&2
    echo "Use a native $TARGET_MACHINE Python interpreter before running build_macos.sh." >&2
    exit 1
fi

$PYTHON_BIN -m pip install -r requirements-packaging.txt
$PYTHON_BIN -m playwright install chromium
$PYTHON_BIN -m PyInstaller --noconfirm cloud_dicom_downloader.spec

BUILT_ARCHS="$(lipo -archs "$APP_EXE")"
case " $BUILT_ARCHS " in
    *" $TARGET_MACHINE "*)
        ;;
    *)
        echo "Built app archs ($BUILT_ARCHS) do not include expected target $TARGET_MACHINE." >&2
        exit 1
        ;;
esac

$PYTHON_BIN - <<'PY'
import os
import shutil
import sys
from pathlib import Path


def browser_cache_roots():
    roots = []
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_path:
        roots.append(Path(env_path).expanduser())

    roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    roots.append(Path.home() / ".cache" / "ms-playwright")

    unique = []
    for root in roots:
        if root not in unique and root.exists():
            unique.append(root)
    return unique


root_dir = Path.cwd()
resources_dir = root_dir / "dist" / "Cloud DICOM Downloader.app" / "Contents" / "Resources"
target_dir = resources_dir / "ms-playwright"
target_dir.mkdir(parents=True, exist_ok=True)

copied = False
for cache_root in browser_cache_roots():
    for child in cache_root.iterdir():
        if not child.is_dir():
            continue
        if not (child.name.startswith("chromium-") or child.name.startswith("ffmpeg-")):
            continue
        destination = target_dir / child.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(child, destination, symlinks=True)
        copied = True

if not copied:
    raise SystemExit("No Playwright browser cache directories found for macOS packaging.")
PY

codesign --force --deep --sign - "dist/$APP_NAME"

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
echo "Note: the app is ad-hoc signed only. For external distribution on macOS, sign with a Developer ID certificate and notarize it before release."
