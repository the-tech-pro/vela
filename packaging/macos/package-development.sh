#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${VELA_APP_PATH:-$ROOT/antra-wails/build/bin/Vela.app}"
RAW_ARCH="${VELA_TARGET_ARCH:-$(uname -m)}"

case "$(printf '%s' "$RAW_ARCH" | tr '[:upper:]' '[:lower:]')" in
  arm64|aarch64)
    PUBLIC_ARCH="arm64"
    ;;
  amd64|x86_64)
    PUBLIC_ARCH="amd64"
    ;;
  *)
    echo "Unsupported development package architecture: $RAW_ARCH" >&2
    exit 2
    ;;
esac

INFO_PLIST="$APP/Contents/Info.plist"
test -d "$APP" || { echo "Missing app bundle: $APP" >&2; exit 1; }
test -f "$INFO_PLIST" || { echo "Missing app Info.plist: $INFO_PLIST" >&2; exit 1; }
test "$(/usr/libexec/PlistBuddy -c 'Print :VelaBuildMode' "$INFO_PLIST")" = "development" || {
  echo "Development packaging accepts only VelaBuildMode=development." >&2
  exit 1
}

is_macho() {
  file -b "$1" | grep -q "Mach-O"
}

# This package marker is not used at runtime. PyInstaller's imageio hook can
# mark it as nested executable content, which invalidates the enclosing app.
rm -f "$APP/Contents/Helpers/VelaBackend/_internal/imageio_ffmpeg/binaries/README.md"

# Ad-hoc signing does not establish distributable trust. It makes the
# development app internally coherent so native CI can launch and test it.
while IFS= read -r -d '' item; do
  if ! is_macho "$item"; then
    chmod a-x "$item"
    continue
  fi
  if [[ "$item" != "$APP/Contents/MacOS/Vela" ]]; then
    codesign --force --sign - "$item"
  fi
done < <(find "$APP/Contents" -type f -print0)

while IFS= read -r -d '' bundle; do
  codesign --force --sign - "$bundle"
done < <(
  find "$APP/Contents" -depth -type d \
    \( -name '*.framework' -o -name '*.bundle' -o -name '*.xpc' -o -name '*.appex' -o -name '*.plugin' -o -name '*.app' \) \
    -print0
)

codesign --force --sign - "$APP/Contents/MacOS/Vela"
codesign --force --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

OUTPUT_DIR="$ROOT/dist/development"
DMG="$OUTPUT_DIR/Vela-macOS-$PUBLIC_ARCH.dmg"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
mkdir -p "$OUTPUT_DIR" "$TEMP_DIR/dmg"
ditto "$APP" "$TEMP_DIR/dmg/Vela.app"
ln -s /Applications "$TEMP_DIR/dmg/Applications"
rm -f "$DMG" "$DMG.sha256"
hdiutil create -volname "Vela Development" -srcfolder "$TEMP_DIR/dmg" \
  -ov -format UDZO "$DMG"
codesign --force --sign - "$DMG"
codesign --verify --strict --verbose=2 "$DMG"
hdiutil verify "$DMG"
DMG_HASH="$(shasum -a 256 "$DMG" | awk '{print $1}')"
printf '%s  %s\n' "$DMG_HASH" "$(basename "$DMG")" >"$DMG.sha256"

echo "Created development-only $DMG"
echo "This artifact is ad-hoc signed, not notarized, and must not be released."
