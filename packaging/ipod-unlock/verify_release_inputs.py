"""Fail-closed offline checks for capacity-unlock release metadata."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[1]
LOCK_PATH = HERE / "artifact-lock.json"
FORBIDDEN_BINARY_SUFFIXES = {
    ".7z",
    ".bin",
    ".exe",
    ".ipsw",
    ".rockbox",
    ".tar",
    ".tgz",
    ".zip",
}


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"capacity-unlock release gate failed: {message}")


try:
    from typing import NoReturn
except ImportError:  # pragma: no cover - Python 3.11 always provides it.
    NoReturn = Any  # type: ignore[misc,assignment]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{name} must be an object")
    return value


def require_equal(actual: Any, expected: Any, name: str) -> None:
    if actual != expected:
        fail(f"{name} is not synchronized with runtime metadata")


def main() -> int:
    try:
        lock = require_mapping(
            json.loads(LOCK_PATH.read_text(encoding="utf-8")), "artifact lock"
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"artifact-lock.json is unreadable: {exc}")

    sys.path.insert(0, str(REPOSITORY_ROOT))
    from antra.core.ipod_unlock_artifacts import (  # noqa: PLC0415
        APPLE_IPOD_CLASSIC_2_0_2_IPSW,
        OLSRO_GUIDE_REVISION,
        OLSRO_ROCKBOX_SYSCFG_PATCH,
        OLSRO_SYSCFG_EDITOR_SOURCE,
        ROCKBOX_UTILITY_SOURCE,
        ROCKBOX_UTILITY_VERSION,
        ROCKBOX_UTILITY_WINDOWS,
        ROCKBOX_HELPER_COMMIT,
        ROCKBOX_HELPER_REPOSITORY,
        VELA_READBACK_PATCH_SHA256,
    )

    require_equal(lock.get("schema_version"), 2, "lock schema")
    apple = require_mapping(lock.get("apple_firmware"), "apple_firmware")
    require_equal(apple.get("filename"), APPLE_IPOD_CLASSIC_2_0_2_IPSW.filename, "IPSW filename")
    require_equal(apple.get("url"), APPLE_IPOD_CLASSIC_2_0_2_IPSW.url, "IPSW URL")
    require_equal(apple.get("size"), APPLE_IPOD_CLASSIC_2_0_2_IPSW.expected_size, "IPSW size")
    require_equal(apple.get("sha1"), APPLE_IPOD_CLASSIC_2_0_2_IPSW.sha1, "IPSW SHA-1")
    require_equal(apple.get("sha256"), APPLE_IPOD_CLASSIC_2_0_2_IPSW.sha256, "IPSW SHA-256")
    require_equal(apple.get("redistributable"), False, "IPSW redistribution flag")

    utility = require_mapping(lock.get("rockbox_utility"), "rockbox_utility")
    require_equal(utility.get("version"), ROCKBOX_UTILITY_VERSION, "Rockbox Utility version")
    require_equal(utility.get("license"), "GPL-2.0-or-later", "Rockbox Utility license")
    utility_windows = require_mapping(utility.get("windows"), "Rockbox Utility Windows package")
    for key, expected in (
        ("filename", ROCKBOX_UTILITY_WINDOWS.filename),
        ("url", ROCKBOX_UTILITY_WINDOWS.url),
        ("size", ROCKBOX_UTILITY_WINDOWS.expected_size),
        ("sha256", ROCKBOX_UTILITY_WINDOWS.sha256),
    ):
        require_equal(utility_windows.get(key), expected, f"Rockbox Utility Windows {key}")
    utility_source = require_mapping(utility.get("source"), "Rockbox Utility source package")
    for key, expected in (
        ("filename", ROCKBOX_UTILITY_SOURCE.filename),
        ("url", ROCKBOX_UTILITY_SOURCE.url),
        ("size", ROCKBOX_UTILITY_SOURCE.expected_size),
        ("sha256", ROCKBOX_UTILITY_SOURCE.sha256),
    ):
        require_equal(utility_source.get(key), expected, f"Rockbox Utility source {key}")
    require_equal(
        utility_source.get("contains"),
        "utils/mks5lboot",
        "Rockbox Utility mks5lboot source path",
    )

    olsro = require_mapping(lock.get("olsro"), "olsro")
    require_equal(olsro.get("revision"), OLSRO_GUIDE_REVISION, "Olsro revision")
    editor = require_mapping(olsro.get("syscfg_editor_source"), "SysCfg editor source")
    require_equal(editor.get("size"), OLSRO_SYSCFG_EDITOR_SOURCE.expected_size, "editor size")
    require_equal(editor.get("sha256"), OLSRO_SYSCFG_EDITOR_SOURCE.sha256, "editor SHA-256")
    patch = require_mapping(olsro.get("rockbox_patch"), "Olsro Rockbox patch")
    require_equal(patch.get("size"), OLSRO_ROCKBOX_SYSCFG_PATCH.expected_size, "Olsro patch size")
    require_equal(patch.get("sha256"), OLSRO_ROCKBOX_SYSCFG_PATCH.sha256, "Olsro patch SHA-256")

    rockbox = require_mapping(lock.get("rockbox"), "rockbox")
    commit = str(rockbox.get("commit", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        fail("Rockbox commit is not a full SHA-1")
    require_equal(rockbox.get("repository"), ROCKBOX_HELPER_REPOSITORY, "Rockbox repository")
    require_equal(commit, ROCKBOX_HELPER_COMMIT, "Rockbox helper commit")
    require_equal(rockbox.get("target"), "ipod6g", "Rockbox target")
    require_equal(rockbox.get("license"), "GPL-2.0-or-later", "Rockbox license")

    overlay = require_mapping(lock.get("vela_readback_patch"), "Vela readback patch")
    overlay_path = HERE / str(overlay.get("filename", ""))
    if not overlay_path.is_file():
        fail("Vela readback patch is missing")
    require_equal(
        overlay.get("sha256"),
        VELA_READBACK_PATCH_SHA256,
        "Vela readback patch runtime hash",
    )
    require_equal(sha256(overlay_path), overlay.get("sha256"), "Vela patch SHA-256")
    overlay_text = overlay_path.read_text(encoding="utf-8")
    for required in (
        "syscfg_num_entries > SYSCFG_MAX_ENTRIES",
        "header_matches",
        "entries_matches",
        "bootflash_compare",
        "return header_matches && entries_matches",
    ):
        if required not in overlay_text:
            fail(f"Vela readback patch is missing guard: {required}")

    build_script = (HERE / "build-rockbox-helper.sh").read_text(encoding="utf-8")
    for expected in (
        commit,
        OLSRO_GUIDE_REVISION,
        OLSRO_ROCKBOX_SYSCFG_PATCH.sha256,
        str(overlay.get("sha256", "")),
    ):
        if expected not in build_script:
            fail(f"build script is missing locked value: {expected}")

    gpl = (HERE / "licenses" / "GPL-2.0-or-later.txt").read_text(encoding="utf-8")
    mit = (HERE / "licenses" / "Olsro-MIT.txt").read_text(encoding="utf-8")
    if "GNU GENERAL PUBLIC LICENSE" not in gpl or "END OF TERMS AND CONDITIONS" not in gpl:
        fail("complete GPL text is missing")
    if "Copyright (c) 2024 Olsro" not in mit or "MIT License" not in mit:
        fail("Olsro MIT notice is missing")

    forbidden = sorted(
        str(path.relative_to(HERE))
        for path in HERE.rglob("*")
        if path.is_file() and path.suffix.lower() in FORBIDDEN_BINARY_SUFFIXES
    )
    if forbidden:
        fail("opaque/binary artifacts are present: " + ", ".join(forbidden))

    print("capacity-unlock release inputs verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
