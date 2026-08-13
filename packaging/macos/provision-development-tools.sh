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
import io
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
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


def download_fpcalc() -> tuple[Path, str]:
    version = "1.5.1"
    release_arch = {"arm64": "arm64", "x86_64": "x86_64"}[expected_arch]
    expected_sha256 = {
        "arm64": "9c5d9565d2396dbcf0e1d797e1ffdf1e19242f3bed88ac3200e144286b57ede6",
        "x86_64": "c6c2797c4f087cf139eedd71554bc59ef8f26a783dc00c7f3ad5ae71d3a616fe",
    }[expected_arch]
    url = (
        "https://github.com/acoustid/chromaprint/releases/download/"
        f"v{version}/chromaprint-fpcalc-{version}-macos-{release_arch}.tar.gz"
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read()
    actual_sha256 = hashlib.sha256(archive).hexdigest()
    if actual_sha256 != expected_sha256:
        raise SystemExit(
            "official fpcalc archive SHA-256 mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        members = [
            member
            for member in bundle.getmembers()
            if member.isfile() and Path(member.name).name == "fpcalc"
        ]
        if len(members) != 1:
            raise SystemExit("official fpcalc archive did not contain exactly one fpcalc")
        stream = bundle.extractfile(members[0])
        if stream is None:
            raise SystemExit("could not read fpcalc from the official archive")
        destination = bin_dir / "fpcalc"
        destination.write_bytes(stream.read())
        destination.chmod(0o755)

    architectures = subprocess.run(
        ["lipo", "-archs", str(destination)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    if expected_arch not in architectures:
        raise SystemExit(
            "official fpcalc has the wrong architecture: "
            f"expected {expected_arch}, found {', '.join(architectures)}"
        )
    subprocess.run(
        [str(destination), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination, version


tools = {name: copy_tool(name) for name in ("ffmpeg", "ffprobe")}
tools["fpcalc"], fpcalc_version = download_fpcalc()
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
                "Owner review must supply source and license evidence before release."
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
