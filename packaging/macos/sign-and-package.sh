#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${VELA_APP_PATH:-$ROOT/antra-wails/build/bin/Vela.app}"
PYTHON="${PYTHON:-python3}"
INFO_PLIST="$APP/Contents/Info.plist"
RAW_ARCH="${VELA_TARGET_ARCH:-$(uname -m)}"
case "$(printf '%s' "$RAW_ARCH" | tr '[:upper:]' '[:lower:]')" in
  arm64|aarch64)
    TARGET_ARCH="arm64"
    PUBLIC_ARCH="arm64"
    ;;
  amd64|x86_64)
    TARGET_ARCH="amd64"
    PUBLIC_ARCH="amd64"
    ;;
  *)
    echo "Unsupported release architecture: $RAW_ARCH" >&2
    exit 1
    ;;
esac

IDENTITY="${VELA_CODESIGN_IDENTITY:?Set VELA_CODESIGN_IDENTITY to a Developer ID Application identity}"
APP_ENTITLEMENTS="$ROOT/packaging/macos/Vela.entitlements"
BACKEND_ENTITLEMENTS="$ROOT/packaging/macos/VelaBackend.entitlements"
NOTARIZE="${VELA_NOTARIZE:-0}"

test -d "$APP" || { echo "Missing app bundle: $APP" >&2; exit 1; }
test -f "$INFO_PLIST" || { echo "Missing app Info.plist: $INFO_PLIST" >&2; exit 1; }
test -f "$APP/Contents/Helpers/VelaBackend/VelaBackend" || {
  echo "Backend must remain in Contents/Helpers/VelaBackend: $APP" >&2
  exit 1
}

plist_value() {
  /usr/libexec/PlistBuddy -c "Print :$1" "$INFO_PLIST"
}

BUILD_MODE="$(plist_value VelaBuildMode)"
if [[ "$BUILD_MODE" != "release" ]]; then
  echo "Refusing to package a non-release app (VelaBuildMode=$BUILD_MODE)" >&2
  echo "Rebuild with: python3 build_desktop.py --build-mode release --target-arch $TARGET_ARCH" >&2
  exit 1
fi

VERSION="$(plist_value CFBundleShortVersionString)"
BUNDLE_VERSION="$(plist_value CFBundleVersion)"
CANONICAL_VERSION="$(PYTHONPATH="$ROOT" "$PYTHON" -c \
  'from build_desktop import read_product_version; print(read_product_version())')"
if [[ "$VERSION" != "$CANONICAL_VERSION" || "$BUNDLE_VERSION" != "$CANONICAL_VERSION" ]]; then
  echo "App version does not match antra/__init__.py ($CANONICAL_VERSION): $VERSION / $BUNDLE_VERSION" >&2
  exit 1
fi

BUNDLE_ID="$(plist_value CFBundleIdentifier)"
if [[ ! "$BUNDLE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9-]*(\.[A-Za-z0-9][A-Za-z0-9-]*){2,}$ ]]; then
  echo "Release bundle identifier is not reverse-DNS: $BUNDLE_ID" >&2
  exit 1
fi
case ".$BUNDLE_ID." in
  *.example.*|*.invalid.*|*.localhost.*|*.local.)
    echo "Release bundle identifier uses a placeholder/local domain: $BUNDLE_ID" >&2
    exit 1
    ;;
esac

ICON_NAME="$(plist_value CFBundleIconFile)"
test "$ICON_NAME" = "Vela.icns" || {
  echo "Release app must declare the approved Vela.icns icon, found: $ICON_NAME" >&2
  exit 1
}
test -f "$APP/Contents/Resources/Vela.icns" || {
  echo "Release app is missing Contents/Resources/Vela.icns" >&2
  exit 1
}

if [[ "$NOTARIZE" != "0" && "$NOTARIZE" != "1" ]]; then
  echo "VELA_NOTARIZE must be 0 or 1" >&2
  exit 1
fi
if [[ "$NOTARIZE" == "1" ]]; then
  : "${VELA_NOTARY_PROFILE:?Set VELA_NOTARY_PROFILE to a notarytool keychain profile name}"
fi

OUTPUT_DIR="${VELA_OUTPUT_DIR:-$ROOT/dist/macos/$VERSION/$PUBLIC_ARCH}"
mkdir -p "$OUTPUT_DIR"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

# Recheck the architecture after assembly and immediately before signing.
PYTHONPATH="$ROOT" "$PYTHON" -c \
  'import sys; from pathlib import Path; from build_desktop import validate_macos_bundle_architectures; validate_macos_bundle_architectures(Path(sys.argv[1]), sys.argv[2])' \
  "$APP" "$TARGET_ARCH"

is_macho() {
  file -b "$1" | grep -q "Mach-O"
}

sign_nested_code() {
  codesign --force --timestamp --options runtime \
    --entitlements "$BACKEND_ENTITLEMENTS" --sign "$IDENTITY" "$1"
}

# Sign every nested Mach-O leaf throughout the app, not only the helper.
# The root app executable is deliberately deferred until all nested code.
while IFS= read -r -d '' item; do
  if ! is_macho "$item"; then
    # PyInstaller can preserve executable bits on data such as README files.
    # codesign then treats those files as unsigned nested code.
    chmod a-x "$item"
    continue
  fi
  if [[ "$item" != "$APP/Contents/MacOS/Vela" ]]; then
    sign_nested_code "$item"
  fi
done < <(find "$APP/Contents" -type f -print0)

# Sign nested code containers deepest-first after their Mach-O leaves.
while IFS= read -r -d '' bundle; do
  codesign --force --timestamp --options runtime \
    --entitlements "$BACKEND_ENTITLEMENTS" --sign "$IDENTITY" "$bundle"
done < <(
  find "$APP/Contents" -depth -type d \
    \( -name '*.framework' -o -name '*.bundle' -o -name '*.xpc' -o -name '*.appex' -o -name '*.plugin' -o -name '*.app' \) \
    -print0
)

codesign --force --timestamp --options runtime \
  --entitlements "$APP_ENTITLEMENTS" --sign "$IDENTITY" "$APP/Contents/MacOS/Vela"
codesign --force --timestamp --options runtime \
  --entitlements "$APP_ENTITLEMENTS" --sign "$IDENTITY" "$APP"

# --deep is verification-only; signing above remains explicit and inside-out.
codesign --verify --deep --strict --verbose=2 "$APP"

NOTARY_LOG_DIR="$OUTPUT_DIR/notarization"

json_value() {
  "$PYTHON" - "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.find("{")
if start < 0:
    raise SystemExit(1)
value, _ = json.JSONDecoder().raw_decode(text[start:])
result = value.get(sys.argv[2], "")
print(result if result is not None else "")
PY
}

notarize_artifact() {
  local artifact="$1"
  local label="$2"
  local submission_log="$NOTARY_LOG_DIR/$label-submission.json"
  local detail_log="$NOTARY_LOG_DIR/$label-notary-log.json"
  local command_status
  local request_id
  local notarization_status

  mkdir -p "$NOTARY_LOG_DIR"
  rm -f "$submission_log" "$detail_log"
  set +e
  xcrun notarytool submit "$artifact" \
    --keychain-profile "$VELA_NOTARY_PROFILE" \
    --wait --output-format json 2>&1 | tee "$submission_log"
  command_status=${PIPESTATUS[0]}
  set -e

  request_id="$(json_value "$submission_log" id 2>/dev/null || true)"
  notarization_status="$(json_value "$submission_log" status 2>/dev/null || true)"
  if [[ -n "$request_id" ]]; then
    xcrun notarytool log "$request_id" \
      --keychain-profile "$VELA_NOTARY_PROFILE" \
      "$detail_log" >/dev/null 2>&1 || true
  fi
  if [[ "$command_status" -ne 0 || "$notarization_status" != "Accepted" ]]; then
    echo "Notarization failed for $artifact; logs retained in $NOTARY_LOG_DIR" >&2
    return 1
  fi
}

if [[ "$NOTARIZE" == "1" ]]; then
  APP_ZIP="$TEMP_DIR/Vela-app.zip"
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$APP_ZIP"
  notarize_artifact "$APP_ZIP" "Vela-$VERSION-macOS-$PUBLIC_ARCH-app"
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"
  spctl --assess --type execute --verbose=2 "$APP"
fi

STAGE="$TEMP_DIR/dmg"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/Vela.app"
ln -s /Applications "$STAGE/Applications"
DMG="$OUTPUT_DIR/Vela-$VERSION-macOS-$PUBLIC_ARCH.dmg"
rm -f "$DMG"
hdiutil create -volname Vela -srcfolder "$STAGE" -ov -format UDZO "$DMG"
codesign --force --timestamp --sign "$IDENTITY" "$DMG"
codesign --verify --strict --verbose=2 "$DMG"

if [[ "$NOTARIZE" == "1" ]]; then
  notarize_artifact "$DMG" "Vela-$VERSION-macOS-$PUBLIC_ARCH-dmg"
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
fi

DMG_HASH="$(shasum -a 256 "$DMG" | awk '{print $1}')"
CHECKSUM_LINE="$DMG_HASH  $(basename "$DMG")"
printf '%s\n' "$CHECKSUM_LINE" > "$DMG.sha256"
printf '%s\n' "$CHECKSUM_LINE" > "$OUTPUT_DIR/SHA256SUMS"

echo "Created $DMG"
echo "Checksum $DMG.sha256"
if [[ "$NOTARIZE" == "0" ]]; then
  echo "WARNING: VELA_NOTARIZE=0; this artifact is signed but not notarized." >&2
fi
