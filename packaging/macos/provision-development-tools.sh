#!/bin/bash
set -euo pipefail

OUTPUT_ROOT="${1:?Pass the development tool output directory}"
TARGET_ARCH="${2:?Pass arm64 or amd64}"

case "$TARGET_ARCH" in
  arm64)
    MACHO_ARCH="arm64"
    ;;
  amd64)
    MACHO_ARCH="x86_64"
    ;;
  *)
    echo "Unsupported development tool architecture: $TARGET_ARCH" >&2
    exit 2
    ;;
esac

mkdir -p "$OUTPUT_ROOT/bin"

python3 - "$OUTPUT_ROOT" "$MACHO_ARCH" <<'PY'
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

output_root = Path(sys.argv[1]).resolve()
expected_arch = sys.argv[2]
bin_dir = output_root / "bin"

actual_arch = {
    "arm64": "arm64",
    "aarch64": "arm64",
    "x86_64": "x86_64",
    "amd64": "x86_64",
}.get(platform.machine().casefold())
if actual_arch != expected_arch:
    raise SystemExit(
        f"development tool host mismatch: expected {expected_arch}, got {actual_arch}"
    )


def command_path(name: str) -> Path:
    raw = shutil.which(name)
    if not raw:
        raise SystemExit(f"Homebrew did not provide required tool: {name}")
    return Path(raw).resolve()


def copy_tool(name: str) -> Path:
    source = command_path(name)
    destination = bin_dir / name
    shutil.copy2(source, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    architectures = subprocess.run(
        ["lipo", "-archs", str(destination)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    if expected_arch not in architectures:
        raise SystemExit(
            f"{name} is not native for this runner: expected {expected_arch}, "
            f"found {', '.join(architectures)}"
        )
    return destination


tools = {name: copy_tool(name) for name in ("ffmpeg", "ffprobe", "fpcalc")}
hashes = {
    name: hashlib.sha256(path.read_bytes()).hexdigest()
    for name, path in tools.items()
}
(output_root / "SHA256SUMS").write_text(
    "".join(f"{hashes[name]}  {name}\n" for name in sorted(hashes)),
    encoding="utf-8",
)


def output(command: list[str]) -> str:
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return (result.stdout or result.stderr).strip()


ffmpeg_version_line = output([str(tools["ffmpeg"]), "-version"]).splitlines()[0]
ffmpeg_parts = ffmpeg_version_line.split()
ffmpeg_version = ffmpeg_parts[2] if len(ffmpeg_parts) >= 3 else ffmpeg_version_line
ffmpeg_configuration = output([str(tools["ffmpeg"]), "-buildconf"])

fpcalc_version_text = output([str(tools["fpcalc"]), "-version"])
fpcalc_version = fpcalc_version_text.split()[-1]

metadata = {
    "media_tools": [
        {
            "name": "FFmpeg",
            "version": ffmpeg_version,
            "upstream_url": "https://ffmpeg.org/",
            "source_url": "https://ffmpeg.org/releases/",
            "declared_license": "GPL-2.0-or-later",
            "build_configuration": (
                "Development-only GitHub-hosted Homebrew build:\n"
                + ffmpeg_configuration
            ),
            "source_offer": (
                "Development CI input only; not approved for public release. "
                "Homebrew formula and source references must be replaced by "
                "owner-reviewed release evidence."
            ),
            "files": [
                {"path": "ffmpeg", "sha256": hashes["ffmpeg"]},
                {"path": "ffprobe", "sha256": hashes["ffprobe"]},
            ],
        },
        {
            "name": "Chromaprint",
            "version": fpcalc_version,
            "upstream_url": "https://acoustid.org/chromaprint",
            "source_url": "https://github.com/acoustid/chromaprint/releases",
            "declared_license": "LGPL-2.1-or-later",
            "build_configuration": (
                "Development-only GitHub-hosted Homebrew build for "
                f"{expected_arch}."
            ),
            "source_offer": (
                "Development CI input only; not approved for public release. "
                "Replace with owner-reviewed source and license evidence."
            ),
            "files": [{"path": "fpcalc", "sha256": hashes["fpcalc"]}],
        },
    ]
}
(output_root / "owner-metadata.json").write_text(
    json.dumps(metadata, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

print(f"Provisioned development media tools in {bin_dir}")
PY
