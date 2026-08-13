#!/bin/bash
set -euo pipefail

APP=""
TARGET_ARCH=""
REQUIRE_IPOD="${VELA_SMOKE_REQUIRE_IPOD:-0}"

usage() {
  echo "Usage: $0 --app /absolute/path/Vela.app --arch arm64|amd64 [--require-ipod]" >&2
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --app)
      APP="${2:-}"
      shift 2
      ;;
    --arch)
      TARGET_ARCH="${2:-}"
      shift 2
      ;;
    --require-ipod)
      REQUIRE_IPOD=1
      shift
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

[[ "$(uname -s)" == "Darwin" ]] || {
  echo "The macOS smoke harness must run on macOS." >&2
  exit 1
}
[[ "$APP" = /* && -d "$APP" ]] || {
  echo "--app must name an absolute Vela.app directory: $APP" >&2
  exit 1
}
case "$TARGET_ARCH" in
  arm64)
    MACHO_ARCH="arm64"
    ;;
  amd64)
    MACHO_ARCH="x86_64"
    ;;
  *)
    usage
    exit 2
    ;;
esac
[[ "$REQUIRE_IPOD" == "0" || "$REQUIRE_IPOD" == "1" ]] || {
  echo "VELA_SMOKE_REQUIRE_IPOD must be 0 or 1." >&2
  exit 1
}

MAIN="$APP/Contents/MacOS/Vela"
BACKEND_APP="$APP/Contents/Helpers/VelaBackend.app"
BACKEND="$BACKEND_APP/Contents/MacOS/VelaBackend"
FPCALC="$BACKEND_APP/Contents/Frameworks/tools/fpcalc"
INFO_PLIST="$APP/Contents/Info.plist"
for required in "$MAIN" "$BACKEND" "$FPCALC" "$INFO_PLIST"; do
  [[ -e "$required" ]] || {
    echo "Missing required app resource: $required" >&2
    exit 1
  }
done
[[ -x "$MAIN" && -x "$BACKEND" && -x "$FPCALC" ]] || {
  echo "Vela, VelaBackend, and bundled fpcalc must be executable." >&2
  exit 1
}

for executable in "$MAIN" "$BACKEND" "$FPCALC"; do
  slices="$(lipo -archs "$executable")"
  [[ " $slices " == *" $MACHO_ARCH "* ]] || {
    echo "Wrong architecture for $executable: expected $MACHO_ARCH, found $slices" >&2
    exit 1
  }
done
"$FPCALC" --help >/dev/null

TEMP_DIR="$(mktemp -d)"
APP_PID=""
cleanup() {
  status=$?
  if [[ "$status" -ne 0 && -s "$TEMP_DIR/app.log" ]]; then
    echo "--- Vela application log ---" >&2
    cat "$TEMP_DIR/app.log" >&2
    echo "--- end Vela application log ---" >&2
  fi
  if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" 2>/dev/null; then
    kill -TERM "$APP_PID" 2>/dev/null || true
    sleep 1
    kill -KILL "$APP_PID" 2>/dev/null || true
  fi
  rm -rf "$TEMP_DIR"
  return "$status"
}
trap cleanup EXIT
mkdir -p "$TEMP_DIR/home"

MEDIA_PATHS="$TEMP_DIR/media-paths.txt"
IPOD_SCAN="$TEMP_DIR/ipod-scan.json"
env -u VELA_TOOLS_DIR \
  -u VELA_TOOLS_CHECKSUMS \
  -u VELA_RELEASE_OWNER_METADATA \
  HOME="$TEMP_DIR/home" "$BACKEND" --get-ffmpeg-dir \
  >"$MEDIA_PATHS" 2>"$TEMP_DIR/media-stderr.log"
env -u VELA_TOOLS_DIR \
  -u VELA_TOOLS_CHECKSUMS \
  -u VELA_RELEASE_OWNER_METADATA \
  HOME="$TEMP_DIR/home" "$BACKEND" --ipod-devices \
  >"$IPOD_SCAN" 2>"$TEMP_DIR/ipod-stderr.log"

TOOLS_ENV="$TEMP_DIR/tools.env"
python3 - "$MEDIA_PATHS" "$IPOD_SCAN" "$APP" "$REQUIRE_IPOD" >"$TOOLS_ENV" <<'PY'
import json
import os
import shlex
import sys
from pathlib import Path

media_paths = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
if len(media_paths) != 2:
    raise SystemExit("media-tool IPC did not return exactly two paths")

app = Path(sys.argv[3]).resolve()
for name, raw_path in zip(("ffmpeg", "ffprobe"), media_paths, strict=True):
    tool = Path(raw_path).resolve()
    if not tool.is_file() or not os.access(tool, os.X_OK):
        raise SystemExit(f"backend returned an unusable {name}: {raw_path}")
    if app not in tool.parents:
        raise SystemExit(f"backend returned an unbundled {name}: {tool}")
    print(f"{name.upper()}={shlex.quote(str(tool))}")

scan = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if scan.get("type") == "error":
    raise SystemExit(f"read-only iPod discovery failed: {scan.get('message')}")
data = scan.get("data")
devices = data.get("devices") if isinstance(data, dict) else None
if not isinstance(devices, list):
    raise SystemExit("read-only iPod discovery did not return a device array")
if sys.argv[4] == "1" and not devices:
    raise SystemExit("this smoke lane requires a mounted fixture iPod")
print(f"IPOD_DEVICE_COUNT={len(devices)}")
PY

# The generated file contains only shell-quoted paths produced by this script.
# shellcheck disable=SC1090
source "$TOOLS_ENV"

python3 - "$BACKEND" "$TEMP_DIR/home" "$APP" "$REQUIRE_IPOD" <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

backend, home, app_raw, require_ipod = sys.argv[1:]
app = Path(app_raw).resolve()
environment = os.environ.copy()
for name in (
    "VELA_TOOLS_DIR",
    "VELA_TOOLS_CHECKSUMS",
    "VELA_RELEASE_OWNER_METADATA",
):
    environment.pop(name, None)
environment["HOME"] = home

requests = (
    {"protocol_version": 1, "id": "media", "command": "ffmpeg_paths", "params": {}},
    {"protocol_version": 1, "id": "ipod", "command": "ipod_scan", "params": {}},
)
completed = subprocess.run(
    [backend, "--read-only-helper"],
    input="".join(json.dumps(request) + "\n" for request in requests),
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=environment,
    timeout=60,
    check=False,
)
if completed.returncode != 0:
    raise SystemExit(
        f"read-only helper exited with status {completed.returncode}"
    )
lines = completed.stdout.splitlines()
if len(lines) != len(requests):
    raise SystemExit(
        f"read-only helper returned {len(lines)} responses; expected {len(requests)}"
    )
responses = [json.loads(line) for line in lines]
for expected_id, response in zip(("media", "ipod"), responses, strict=True):
    if response.get("id") != expected_id or response.get("ok") is not True:
        raise SystemExit(f"read-only helper failed request {expected_id}")

media = responses[0].get("result")
if not isinstance(media, dict):
    raise SystemExit("read-only helper returned invalid media-tool data")
for name in ("ffmpeg", "ffprobe"):
    tool = Path(str(media.get(name) or "")).resolve()
    if not tool.is_file() or not os.access(tool, os.X_OK) or app not in tool.parents:
        raise SystemExit(f"read-only helper returned an unusable bundled {name}")

scan = responses[1].get("result")
devices = scan.get("devices") if isinstance(scan, dict) else None
if not isinstance(devices, list):
    raise SystemExit("read-only helper iPod scan did not return a device array")
if require_ipod == "1" and not devices:
    raise SystemExit("read-only helper did not find the required mounted fixture iPod")
PY

python3 - "$TEMP_DIR/fixture.wav" <<'PY'
import math
import struct
import sys
import wave

sample_rate = 8_000
with wave.open(sys.argv[1], "wb") as output:
    output.setparams((1, 2, sample_rate, sample_rate, "NONE", "not compressed"))
    output.writeframes(
        b"".join(
            struct.pack("<h", int(8_000 * math.sin(2 * math.pi * 440 * i / sample_rate)))
            for i in range(sample_rate)
        )
    )
PY
"$FFMPEG" -hide_banner -loglevel error \
  -i "$TEMP_DIR/fixture.wav" -f null -
"$FFPROBE" -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 "$TEMP_DIR/fixture.wav" \
  >"$TEMP_DIR/duration.txt"
python3 - "$TEMP_DIR/duration.txt" <<'PY'
import sys
from pathlib import Path

duration = float(Path(sys.argv[1]).read_text(encoding="utf-8").strip())
if not 0.5 <= duration <= 2.0:
    raise SystemExit(f"unexpected fixture duration: {duration}")
PY

env -u VELA_TOOLS_DIR \
  -u VELA_TOOLS_CHECKSUMS \
  -u VELA_RELEASE_OWNER_METADATA \
  HOME="$TEMP_DIR/home" "$MAIN" >"$TEMP_DIR/app.log" 2>&1 &
APP_PID="$!"

ready=0
for _ in $(seq 1 240); do
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "Vela exited during startup." >&2
    exit 1
  fi
  if grep -Fq "Vela DOM ready" "$TEMP_DIR/app.log"; then
    ready=1
    break
  fi
  sleep 0.25
done
[[ "$ready" == "1" ]] || {
  echo "Timed out waiting for Vela startup." >&2
  exit 1
}

# Wails can return macOS userCanceledErr while it asynchronously accepts the
# quit event. Process termination below is the authoritative clean-quit check.
osascript -e "tell application \"$APP\" to quit" || true
for _ in $(seq 1 80); do
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    APP_PID=""
    break
  fi
  sleep 0.25
done
[[ -z "$APP_PID" ]] || {
  echo "Vela did not complete a clean application quit." >&2
  exit 1
}
if pgrep -f "$BACKEND_APP/Contents/MacOS/VelaBackend" >/dev/null 2>&1; then
  echo "A VelaBackend process survived application shutdown." >&2
  exit 1
fi

echo "macOS smoke passed: arch=$TARGET_ARCH ipod_devices=$IPOD_DEVICE_COUNT"
