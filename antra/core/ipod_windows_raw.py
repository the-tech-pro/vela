"""Bounded, read-only discovery for Windows iPods that are not mountable.

Windows does not include an HFS+ filesystem driver. A Mac-formatted iPod can
therefore have a drive letter and be visible to Apple Devices while remaining
absent from normal mounted-volume enumeration. This module only reads volume
metadata through pytsk3; it never extracts files, opens a writable handle, or
authorizes an iPod mutation.
"""

from __future__ import annotations

import ctypes
import hashlib
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable


DRIVE_REMOVABLE = 2
MAX_VOLUME_ENTRIES = 128
MAX_ROOT_ENTRIES = 512
MAX_SUPPORTED_DEVICE_BYTES = 16 * 1024**4
HFS_PARTITION_MARKER = "apple_hfs"
IPOD_ROOT_NAMES = frozenset({"ipod_control", "itunes_control"})


@dataclass(frozen=True, slots=True)
class WindowsRawIPod:
    """An attached iPod whose HFS+ volume is readable only as a raw device."""

    path: str
    raw_device_path: str
    display_name: str
    ipod_name: str
    disk_size_gb: float
    capacity: str
    hfs_partition_offset_bytes: int
    raw_volume_fingerprint: str = ""
    model_family: str = "iPod"
    generation: str = ""
    model_number: str = ""
    serial: str = ""
    firewire_guid: str = ""
    firmware: str = ""
    filesystem_type: str = "HFS+"
    reported_volume_format: str = "HFS+"
    volume_identity_key: str = ""
    free_space_gb: float = 0.0
    uses_sqlite_db: bool = False
    checksum_type: int = 99
    audio_codecs: tuple[str, ...] = ()
    podcasts_supported: bool = False
    voice_memos_supported: bool = False
    supports_sparse_artwork: bool = False
    filesystem_accessible: bool = False
    raw_read_only: bool = True
    access_state: str = "mac_formatted_read_only"
    access_message: str = (
        "Mac-formatted HFS+ iPod detected. Windows has not mounted this "
        "filesystem, so Vela is keeping the device read-only. Back it up on "
        "macOS or with a trusted read-only HFS+ tool before any reformat."
    )


def _mount_key(path: str) -> str:
    return str(path or "").strip().replace("/", "\\").rstrip("\\").casefold()


def _normalize_drive_letter(value: str) -> str | None:
    candidate = str(value or "").strip().rstrip(":\\/").upper()
    if len(candidate) != 1 or not "A" <= candidate <= "Z":
        return None
    return candidate


def _logical_removable_drive_letters() -> list[str]:
    if sys.platform != "win32":
        return []
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    mask = int(kernel32.GetLogicalDrives())
    if mask <= 0:
        return []
    letters: list[str] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        letter = chr(ord("A") + index)
        root = f"{letter}:\\"
        if int(kernel32.GetDriveTypeW(root)) == DRIVE_REMOVABLE:
            letters.append(letter)
    return letters


def _decoded_partition_description(partition: Any) -> str:
    value = getattr(partition, "desc", b"")
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace").strip().casefold()
    return str(value or "").strip().casefold()


def _bounded_root_names(filesystem: Any) -> set[str]:
    names: set[str] = set()
    directory = filesystem.open_dir(path="/")
    for index, entry in enumerate(directory):
        if index >= MAX_ROOT_ENTRIES:
            break
        name_info = getattr(getattr(entry, "info", None), "name", None)
        raw_name = getattr(name_info, "name", b"")
        if isinstance(raw_name, bytes):
            name = raw_name.decode("utf-8", errors="replace")
        else:
            name = str(raw_name or "")
        cleaned = name.strip().casefold()
        if cleaned and cleaned not in {".", ".."}:
            names.add(cleaned)
    return names


def _filesystem_fingerprint(filesystem: Any) -> str:
    """Hash TSK's filesystem identifier for drive-letter-independent UI identity."""

    info = getattr(filesystem, "info", None)
    values = getattr(info, "fs_id", ())
    try:
        used = max(0, min(32, int(getattr(info, "fs_id_used", 0))))
        identifier = bytes(int(values[index]) & 0xFF for index in range(used))
    except (IndexError, TypeError, ValueError):
        return ""
    if not identifier or not any(identifier):
        return ""
    return hashlib.sha256(b"vela-hfs-read-only\0" + identifier).hexdigest()[:32]


def _probe_hfs_ipod(
    mount_path: str,
    *,
    pytsk_module: Any | None = None,
) -> WindowsRawIPod | None:
    """Return a raw HFS+ iPod descriptor without reading file contents."""

    letter = _normalize_drive_letter(mount_path)
    if not letter:
        return None
    if pytsk_module is None:
        import pytsk3 as pytsk_module

    raw_path = rf"\\.\{letter}:"
    image = pytsk_module.Img_Info(raw_path)
    image_size = int(image.get_size())
    if image_size <= 0 or image_size > MAX_SUPPORTED_DEVICE_BYTES:
        return None

    volume = pytsk_module.Volume_Info(image)
    volume_block_size = int(getattr(getattr(volume, "info", None), "block_size", 0))
    if volume_block_size <= 0 or volume_block_size > 1024 * 1024:
        return None

    for index, partition in enumerate(volume):
        if index >= MAX_VOLUME_ENTRIES:
            break
        if HFS_PARTITION_MARKER not in _decoded_partition_description(partition):
            continue
        start_block = int(getattr(partition, "start", -1))
        length_blocks = int(getattr(partition, "len", 0))
        offset = start_block * volume_block_size
        partition_bytes = length_blocks * volume_block_size
        if (
            start_block < 0
            or length_blocks <= 0
            or offset < 0
            or offset >= image_size
            or partition_bytes <= 0
            or offset + partition_bytes > image_size
        ):
            continue
        filesystem = pytsk_module.FS_Info(image, offset=offset)
        if not (_bounded_root_names(filesystem) & IPOD_ROOT_NAMES):
            continue
        size_gb = image_size / 1_000_000_000
        capacity_gb = max(1, round(size_gb))
        return WindowsRawIPod(
            path=f"{letter}:\\",
            raw_device_path=raw_path,
            display_name=f"Mac-formatted iPod ({letter}:)",
            ipod_name=f"Mac-formatted iPod ({letter}:)",
            disk_size_gb=size_gb,
            capacity=f"{capacity_gb} GB",
            hfs_partition_offset_bytes=offset,
            raw_volume_fingerprint=_filesystem_fingerprint(filesystem),
        )
    return None


def scan_windows_raw_ipods(
    mounted_paths: Iterable[str] = (),
    *,
    drive_letters: Iterable[str] | None = None,
    probe: Callable[[str], WindowsRawIPod | None] | None = None,
) -> list[WindowsRawIPod]:
    """Find unmounted HFS+ iPods while failing closed on unreadable devices."""

    if drive_letters is None:
        if sys.platform != "win32":
            return []
        drive_letters = _logical_removable_drive_letters()
    mounted = {_mount_key(path) for path in mounted_paths}
    probe_device = probe or _probe_hfs_ipod
    devices: list[WindowsRawIPod] = []
    seen: set[str] = set()
    for value in drive_letters:
        letter = _normalize_drive_letter(value)
        if not letter:
            continue
        mount_path = f"{letter}:\\"
        key = _mount_key(mount_path)
        if key in mounted or key in seen:
            continue
        seen.add(key)
        try:
            device = probe_device(mount_path)
        except (AttributeError, ImportError, OSError, RuntimeError, ValueError):
            continue
        if device is not None:
            devices.append(device)
    return devices
