"""Safety-gated, headless Vela boundary to iOpenPod 1.67.1.

Discovery and browsing are strictly read-only.  Every mutation is tied to a
short-lived reviewed plan, revalidates the exact mounted volume and database,
creates a verified backup outside the device, and then delegates to SyncEngine.
No GUI/iopenpod.gui module is imported here.
"""

from __future__ import annotations

import dataclasses
import errno
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .ipod_operation_journal import OperationJournal, OperationJournalError
from .ipod_migration import (
    MIGRATION_BUNDLE_SCHEMA_VERSION,
    MigrationBundleError,
    build_migration_bundle,
    compute_iopenpod_migration_plan,
    load_migration_bundle,
    migration_source_files,
)
from .ipod_capacity_unlock import (
    CapacityUnlockError,
    CapacityUnlockStateMachine,
    PostflightEvidence,
    UnlockAcknowledgements,
    UnlockDeviceEvidence,
    evaluate_capacity_unlock_eligibility,
)
from .ipod_syscfg import (
    SysCfgError,
    audited_olsro_202_preset,
    build_capacity_unlock_candidate,
)
from .ipod_unlock_artifacts import (
    PINNED_UNLOCK_ARTIFACTS,
    REQUIRED_UNLOCK_ARTIFACT_IDS,
    ArtifactDownloader,
    ArtifactError,
    validate_artifact_file,
    validate_rockbox_helper_build,
)

PROTOCOL_VERSION = 1
PLAN_SCHEMA_VERSION = 2
RESTORE_PLAN_SCHEMA_VERSION = 1
MIGRATION_PLAN_SCHEMA_VERSION = 1
DEFAULT_PLAN_TTL_SECONDS = 15 * 60
MAX_PAGE_SIZE = 250
MAX_PLAN_DETAIL_PAGE_SIZE = 100
MAX_BACKUP_PAGE_SIZE = 100
MAX_BACKUP_DEVICES = 1_000
PLAN_PREVIEW_SIZE = 5
MAX_SNAPSHOT_NOTE_LENGTH = 4_000
ARCHIVE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
UNLOCK_ACTIONS = frozenset({
    "status",
    "list",
    "backup",
    "artifacts",
    "bootloader-await",
    "bootloader-installed",
    "syscfg-original",
    "syscfg-candidate",
    "syscfg-stage",
    "nor-await",
    "nor-attested",
    "dfu-await",
    "dfu-detected",
    "itunes-handoff",
    "restore-finished",
    "postflight",
    "recovery",
    "resume",
    "cancel",
})
UNLOCK_ACKNOWLEDGEMENT_FIELDS = (
    "destructive_restore_erases_device",
    "nor_flash_can_make_device_unbootable",
    "manual_rockbox_nor_dfu_steps_required",
    "hardware_recovery_may_be_required",
    "itunes_restore_is_user_controlled",
    "cancellation_ends_after_nor_commit",
)
SUPPORTED_FAMILIES = frozenset({"ipod", "ipod classic", "ipod mini", "ipod nano"})
MEDIA_EXTENSIONS = frozenset({
    ".aac", ".aif", ".aiff", ".flac", ".m4a", ".m4b", ".m4p",
    ".mp3", ".ogg", ".opus", ".wav", ".mp4", ".m4v", ".mov",
})


class IPodServiceError(RuntimeError):
    """A safe, user-displayable iPod operation failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = _bounded_json(details or {})


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value") and isinstance(value.value, (str, int)):
        return value.value
    return str(value)


def _bounded_json(
    value: Any,
    *,
    depth: int = 0,
    max_items: int = 100,
    max_string: int = 2_000,
) -> Any:
    """Convert third-party values to a bounded JSON-safe display payload."""
    if depth >= 6:
        return str(value)[:max_string]
    if dataclasses.is_dataclass(value):
        value = {
            field.name: getattr(value, field.name)
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                break
            result[str(key)[:128]] = _bounded_json(
                item,
                depth=depth + 1,
                max_items=max_items,
                max_string=max_string,
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            _bounded_json(
                item,
                depth=depth + 1,
                max_items=max_items,
                max_string=max_string,
            )
            for item in list(value)[:max_items]
        ]
    if isinstance(value, str):
        return value[:max_string]
    if isinstance(value, Path):
        return str(value)[:max_string]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (int, bool)) or value is None:
        return value
    if hasattr(value, "value") and isinstance(value.value, (str, int)):
        return _bounded_json(
            value.value,
            depth=depth + 1,
            max_items=max_items,
            max_string=max_string,
        )
    return str(value)[:max_string]


def _generation_dict(generation: Any) -> dict[str, Any]:
    return _jsonable(generation)


def _canonical_path(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(str(path)))))


def _discovery_path_key(path: str | Path) -> str:
    """Compare attached paths even when Windows cannot mount their filesystem."""

    try:
        return _canonical_path(path)
    except OSError:
        return os.path.normcase(
            os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))
        )


_BROWSE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "ID"),
    "dbid": ("dbid", "DBID"),
    "persistent_id": ("persistent_id", "Persistent ID", "PersistentID"),
    "track_id": ("track_id", "Track ID", "TrackID"),
    "db_track_id": ("db_track_id", "DB Track ID", "DBTrackID"),
    "album_id": ("album_id", "Album ID", "AlbumID"),
    "artist_id": ("artist_id", "Artist ID", "ArtistID"),
    "sql_id": ("sql_id", "SQL ID", "SQLID"),
    "title": ("title", "Title"),
    "artist": (
        "artist",
        "Artist",
        "Artist (Used by Album Item)",
        "Artist (Used by Artist Item)",
    ),
    "album": ("album", "Album", "Album (Used by Album Item)"),
    "album_artist": (
        "album_artist",
        "Album Artist",
        "AlbumArtist",
    ),
    "genre": ("genre", "Genre"),
    "composer": ("composer", "Composer"),
    "location": ("location", "Location"),
    "duration": ("duration", "Duration", "track_length", "length"),
    "track_number": ("track_number", "Track Number", "TrackNumber"),
    "track_count": (
        "track_count",
        "Track Count",
        "TrackCount",
        "total_tracks",
    ),
    "disc_number": ("disc_number", "Disc Number", "DiscNumber"),
    "disc_count": ("disc_count", "Disc Count", "DiscCount", "total_discs"),
    "year": ("year", "Year"),
    "rating": ("rating", "Rating"),
    "play_count": ("play_count", "Play Count", "PlayCount", "play_count_1"),
    "skip_count": ("skip_count", "Skip Count", "SkipCount"),
    "date_added": ("date_added", "Date Added", "DateAdded"),
    "filetype": ("filetype", "Filetype", "File Type"),
    "mediatype": ("mediatype", "Media Type", "MediaType"),
    "podcast": ("podcast", "Podcast"),
    "video": ("video", "Video"),
}
_BROWSE_ITEM_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "ID"),
    "dbid": ("dbid", "DBID"),
    "persistent_id": ("persistent_id", "Persistent ID", "PersistentID"),
    "track_id": ("track_id", "Track ID", "TrackID"),
    "db_track_id": ("db_track_id", "DB Track ID", "DBTrackID"),
    "group_id": ("group_id", "Group ID", "GroupID"),
    "position": ("position", "Position"),
}


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, str):
        return value[:2_000]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (int, bool)) or value is None:
        return value
    raise TypeError


def _safe_alias_fields(
    row: Mapping[str, Any],
    aliases: Mapping[str, tuple[str, ...]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for output_key, accepted_keys in aliases.items():
        for input_key in accepted_keys:
            if input_key not in row:
                continue
            try:
                result[output_key] = _safe_scalar(row[input_key])
            except TypeError:
                pass
            break
    return result


def _safe_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize only approved parser fields into Vela's bounded browse schema."""
    result = _safe_alias_fields(row, _BROWSE_FIELD_ALIASES)
    items = row.get("items")
    if isinstance(items, list):
        result["items"] = [
            _safe_alias_fields(item, _BROWSE_ITEM_FIELD_ALIASES)
            for item in items[:500]
            if isinstance(item, Mapping)
        ]
    return result


def _translate_write_safety_error(exc: BaseException) -> IPodServiceError:
    """Map expected removable-volume failures to stable public error codes."""
    text = str(exc).strip()
    lowered = text.casefold()
    error_number = getattr(exc, "errno", None)
    if (
        isinstance(exc, PermissionError)
        or error_number in {errno.EACCES, errno.EPERM}
        or "permission denied" in lowered
        or "operation not permitted" in lowered
        or "access is denied" in lowered
    ):
        return IPodServiceError(
            "removable_volume_permission_denied",
            "Vela does not have permission to write to this removable volume. "
            "On macOS, allow Removable Volumes access for Vela, then reconnect "
            "the iPod and try again.",
        )
    if "read-only" in lowered or "read only" in lowered or error_number == errno.EROFS:
        return IPodServiceError(
            "volume_read_only",
            "The iPod volume is mounted read-only. Remount it read/write or repair "
            "the filesystem before syncing.",
        )
    if "unsupported filesystem" in lowered:
        return IPodServiceError(
            "unsupported_filesystem",
            "The mounted iPod filesystem is not supported for safe writes. Vela "
            "supports mounted FAT32 and trusted mounted HFS+ iPod volumes only.",
        )
    if (
        "identity" in lowered
        or "not mounted as its own volume" in lowered
        or "unrecognized volume" in lowered
    ):
        return IPodServiceError(
            "incomplete_volume_identity",
            "Vela could not verify the mounted iPod volume identity. Reconnect the "
            "device and ensure its filesystem is mounted before writing.",
        )
    return IPodServiceError(
        "write_safety_failed",
        text or "The iPod failed Vela's write-safety inspection.",
    )


class IOpenPodAdapter:
    """Thin import adapter, isolated so safety behavior is unit-testable."""

    def __init__(self) -> None:
        import iopenpod

        version = str(getattr(iopenpod, "__version__", ""))
        if version != "1.67.1":
            raise IPodServiceError(
                "iopenpod_version_mismatch",
                f"Vela 2.0 requires iOpenPod 1.67.1; found {version or 'unknown'}.",
            )

    def scan_read_only(self) -> list[Any]:
        from iopenpod.device import scanner
        from iopenpod.device.virtual import has_virtual_ipod_info, load_virtual_ipod_info
        from antra.core.ipod_windows_raw import scan_windows_raw_ipods

        devices: list[Any] = []
        candidates = scanner._find_ipod_volumes()  # discovery only
        try:
            for mount_path, display_name in candidates:
                if has_virtual_ipod_info(mount_path):
                    devices.append(load_virtual_ipod_info(mount_path))
                    continue
                devices.append(self._identify_physical_read_only(scanner, mount_path, display_name))
        finally:
            scanner._clear_macos_usb_cache()
        mounted_devices = scanner._deduplicate_ipods(devices)
        raw_devices = scan_windows_raw_ipods(
            str(device.path) for device in mounted_devices
        )
        return [*mounted_devices, *raw_devices]

    @staticmethod
    def _identify_physical_read_only(scanner: Any, mount_path: str, display_name: str) -> Any:
        """Mirror scanner phases 1-3 but intentionally skip VPD writes/enrich."""
        from iopenpod.device.capabilities import capabilities_for_family_gen
        from iopenpod.device.info import DeviceInfo

        info = DeviceInfo(path=mount_path, mount_name=display_name)
        info.disk_size_gb, info.free_space_gb = scanner._get_disk_info(mount_path)
        hardware = scanner._probe_hardware(mount_path, display_name)
        filesystem = scanner._probe_filesystem(mount_path)
        resolved = scanner._resolve_model(hardware, filesystem, info.disk_size_gb)
        for field in dataclasses.fields(info):
            if field.name.startswith("_"):
                continue
            value = resolved.get(field.name)
            if value not in (None, "", b"", {}, []):
                setattr(info, field.name, value)
        info.raw_identity_evidence = {
            "hardware": [hardware] if hardware else [],
            "filesystem": [filesystem] if filesystem else [],
        }
        info.identity_conflicts = list(resolved.get("_conflicts", []))
        info._field_sources.update(resolved.get("_sources", {}))
        info.ipod_name = scanner._extract_ipod_name(mount_path)
        if not info.capacity and info.disk_size_gb > 0:
            info.capacity = scanner._estimate_capacity_from_disk_size(info.disk_size_gb)
        caps = capabilities_for_family_gen(info.model_family, info.generation)
        if caps is not None:
            info.db_version = info.db_version or caps.db_version
            info.shadow_db_version = info.shadow_db_version or caps.shadow_db_version
            info.uses_sqlite_db = bool(info.uses_sqlite_db or caps.uses_sqlite_db)
            info.checksum_type = int(caps.checksum)
            info.podcasts_supported = bool(info.podcasts_supported or caps.supports_podcast)
            info.supports_sparse_artwork = bool(
                info.supports_sparse_artwork or caps.supports_sparse_artwork
            )
            info.audio_codecs = {
                "mp3": True,
                "aac": True,
                "alac": bool(caps.supports_alac),
            }
        return info

    def identify_read_only(self, mount_path: str) -> Any:
        wanted = _discovery_path_key(mount_path)
        for device in self.scan_read_only():
            if _discovery_path_key(device.path) == wanted:
                if getattr(device, "filesystem_accessible", True) is False:
                    raise IPodServiceError(
                        "filesystem_unavailable",
                        str(
                            getattr(device, "access_message", "")
                            or "The attached iPod filesystem is not mounted."
                        ),
                    )
                return device
        raise IPodServiceError("device_not_found", "The selected iPod is no longer connected.")

    @staticmethod
    def capture_database_generation(mount_path: str):
        from iopenpod.device.write_guard import capture_database_generation
        return capture_database_generation(mount_path)

    @staticmethod
    def load_library(mount_path: str) -> dict[str, Any]:
        from iopenpod.device.info import resolve_itdb_path
        from iopenpod.itunesdb_parser.ipod_library import load_ipod_library

        database_path = resolve_itdb_path(mount_path)
        if not database_path:
            return {"mhlt": [], "mhla": [], "mhlp": []}
        result = load_ipod_library(database_path)
        if result is None:
            raise IPodServiceError("database_unreadable", "The iPod database could not be read.")
        return result

    @staticmethod
    def inspect_write_readiness(device: Any):
        from iopenpod.device.write_readiness import inspect_device_write_readiness
        return inspect_device_write_readiness(
            device.path,
            reported_volume_format=str(getattr(device, "reported_volume_format", "") or ""),
        )

    @staticmethod
    def volume_key(profile: Any) -> str:
        from iopenpod.device.write_readiness import volume_lock_key
        return volume_lock_key(profile)

    @staticmethod
    def backup_archive_id(device: Any) -> str:
        """Return iOpenPod's serial-first, GUID-fallback archive key."""
        from iopenpod.sync.backup_manager import BackupManager, get_device_identifier

        identifier = get_device_identifier(
            device.path,
            device,
            volume_identity_key=str(getattr(device, "volume_identity_key", "") or ""),
        )
        return BackupManager(identifier).device_id

    @staticmethod
    def _backup_device_meta(device: Any) -> dict[str, Any]:
        serial = str(getattr(device, "serial", "") or "").strip()
        guid = str(getattr(device, "firewire_guid", "") or "").strip()
        identity_material = f"{serial.casefold()}|{guid.casefold()}"
        return {
            "model_family": str(getattr(device, "model_family", "") or "")[:100],
            "generation": str(getattr(device, "generation", "") or "")[:100],
            "model_number": str(getattr(device, "model_number", "") or "")[:100],
            "firmware": str(getattr(device, "firmware", "") or "")[:100],
            "filesystem_type": str(
                getattr(device, "filesystem_type", "") or ""
            )[:100],
            "reported_volume_format": str(
                getattr(device, "reported_volume_format", "") or ""
            )[:100],
            "capacity": str(getattr(device, "capacity", "") or "")[:100],
            "disk_size_gb": float(getattr(device, "disk_size_gb", 0) or 0),
            "free_space_gb": float(getattr(device, "free_space_gb", 0) or 0),
            "uses_sqlite_db": bool(getattr(device, "uses_sqlite_db", False)),
            "db_version": int(getattr(device, "db_version", 0) or 0),
            "shadow_db_version": int(
                getattr(device, "shadow_db_version", 0) or 0
            ),
            "hashing_scheme": int(getattr(device, "hashing_scheme", -1) or -1),
            "checksum_type": int(getattr(device, "checksum_type", 99) or 99),
            "audio_codecs": _bounded_json(
                getattr(device, "audio_codecs", {}) or {},
                max_items=30,
                max_string=100,
            ),
            "podcasts_supported": bool(
                getattr(device, "podcasts_supported", False)
            ),
            "voice_memos_supported": bool(
                getattr(device, "voice_memos_supported", False)
            ),
            "supports_sparse_artwork": bool(
                getattr(device, "supports_sparse_artwork", False)
            ),
            "photos_supported": bool(getattr(device, "photos_supported", False)),
            "videos_supported": bool(getattr(device, "videos_supported", False)),
            "stable_device_id": hashlib.sha256(
                identity_material.encode("utf-8", errors="surrogatepass")
            ).hexdigest()[:32],
        }

    @classmethod
    def backup_manager(
        cls,
        archive_id: str,
        backup_root: str,
        *,
        device: Any | None = None,
    ) -> Any:
        from iopenpod.sync.backup_manager import BackupManager

        return BackupManager(
            archive_id,
            backup_dir=backup_root,
            device_name=(
                str(
                    getattr(device, "ipod_name", "")
                    or getattr(device, "display_name", "")
                    or "iPod"
                )
                if device is not None
                else "iPod"
            ),
            device_meta=cls._backup_device_meta(device) if device is not None else {},
            identity_is_stable=bool(
                device is not None
                and str(getattr(device, "serial", "") or "").strip()
                and str(getattr(device, "firewire_guid", "") or "").strip()
            ),
        )

    @staticmethod
    def list_backup_devices(backup_root: str) -> list[dict[str, Any]]:
        from iopenpod.sync.backup_manager import BackupManager

        return BackupManager.list_all_devices(backup_root)

    @classmethod
    def list_backup_snapshots(cls, archive_id: str, backup_root: str) -> list[Any]:
        return cls.backup_manager(archive_id, backup_root).list_snapshots()

    @classmethod
    def backup_repository_size(cls, archive_id: str, backup_root: str) -> int:
        return int(cls.backup_manager(archive_id, backup_root).get_backup_size())

    @classmethod
    def load_backup_manifest(
        cls,
        archive_id: str,
        snapshot_id: str,
        backup_root: str,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
        """Load and validate a pinned iOpenPod manifest under its repository lock."""
        from iopenpod.device.write_guard import DeviceWriteSafetyError
        from iopenpod.sync.backup_manager import (
            _locked_backup_repository,
            _manifest_digest,
            _validated_manifest_entries,
        )

        manager = cls.backup_manager(archive_id, backup_root)
        with _locked_backup_repository(manager.backup_root):
            manifest = manager._load_manifest(snapshot_id)
            if manifest is None:
                raise DeviceWriteSafetyError(
                    f"The backup snapshot {snapshot_id!r} could not be found."
                )
            entries = _validated_manifest_entries(
                manifest,
                expected_snapshot_id=snapshot_id,
                expected_device_id=manager.device_id,
            )
            fingerprint = str(
                manifest.get("manifest_sha256") or _manifest_digest(manifest)
            ).casefold()
            return dict(manifest), dict(entries), fingerprint

    @classmethod
    def update_backup_note(
        cls,
        archive_id: str,
        snapshot_id: str,
        note: str,
        backup_root: str,
    ) -> bool:
        return bool(
            cls.backup_manager(archive_id, backup_root).update_snapshot_note(
                snapshot_id,
                note,
            )
        )

    @classmethod
    def export_backup_snapshot(
        cls,
        archive_id: str,
        snapshot_id: str,
        destination_dir: str,
        backup_root: str,
        progress: Callable[[Any], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> Any:
        return cls.backup_manager(archive_id, backup_root).export_snapshot(
            snapshot_id,
            destination_dir,
            progress_callback=progress,
            is_cancelled=cancelled,
        )

    @classmethod
    def delete_backup_snapshot(
        cls,
        archive_id: str,
        snapshot_id: str,
        backup_root: str,
    ) -> bool:
        return bool(
            cls.backup_manager(archive_id, backup_root).delete_snapshot(snapshot_id)
        )

    @classmethod
    def deep_verify_backup_snapshot(
        cls,
        archive_id: str,
        snapshot_id: str,
        backup_root: str,
        progress: Callable[[Any], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> dict[str, Any] | None:
        """Hash every referenced content-addressed blob without exporting it."""
        from iopenpod.device.write_guard import DeviceWriteSafetyError
        from iopenpod.sync.backup_manager import (
            BackupProgress,
            _locked_backup_repository,
            _validated_manifest_entries,
        )

        manager = cls.backup_manager(archive_id, backup_root)
        with _locked_backup_repository(manager.backup_root):
            manifest = manager._load_manifest(snapshot_id)
            if manifest is None:
                raise DeviceWriteSafetyError(
                    f"The backup snapshot {snapshot_id!r} could not be found."
                )
            entries = _validated_manifest_entries(
                manifest,
                expected_snapshot_id=snapshot_id,
                expected_device_id=manager.device_id,
            )
            unique: dict[str, int] = {}
            for file_info in entries.values():
                file_hash = str(file_info["hash"]).casefold()
                file_size = int(file_info["size"])
                previous_size = unique.setdefault(file_hash, file_size)
                if previous_size != file_size:
                    raise DeviceWriteSafetyError(
                        "The backup manifest assigns conflicting sizes to one blob."
                    )
            total = len(unique)
            if progress:
                progress(BackupProgress(
                    "verifying",
                    0,
                    total,
                    message="Deep-verifying every referenced backup blob…",
                ))
            verified_bytes = 0
            for index, (file_hash, expected_size) in enumerate(
                sorted(unique.items()),
                start=1,
            ):
                if cancelled and cancelled():
                    return None
                blob_path = manager._blob_path(file_hash)
                try:
                    stat_result = blob_path.stat()
                except OSError as exc:
                    raise DeviceWriteSafetyError(
                        "A referenced backup blob is missing or unreadable."
                    ) from exc
                if (
                    blob_path.is_symlink()
                    or not blob_path.is_file()
                    or stat_result.st_size != expected_size
                    or manager._hash_file(blob_path).casefold() != file_hash
                ):
                    raise DeviceWriteSafetyError(
                        "A referenced backup blob failed deep SHA-256 verification."
                    )
                verified_bytes += expected_size
                if progress and (index == total or index % 10 == 0):
                    progress(BackupProgress(
                        "verifying",
                        index,
                        total,
                        message=f"Verified {index:,}/{total:,} backup blobs",
                    ))
            return {
                "snapshot_id": snapshot_id,
                "file_count": len(entries),
                "unique_blobs_verified": total,
                "verified_bytes": verified_bytes,
                "verification": "full_sha256",
                "ok": True,
            }

    @classmethod
    def preflight_restore_snapshot(
        cls,
        device: Any,
        archive_id: str,
        snapshot_id: str,
        backup_root: str,
        profile: Any,
        progress: Callable[[Any], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> dict[str, Any] | None:
        """Verify every restore input without changing the target volume."""
        from iopenpod.device.storage_safety import allocated_size
        from iopenpod.device.write_guard import DeviceWriteSafetyError
        from iopenpod.sync.backup_manager import (
            _BackupOperationCancelled,
            _RestoreWriteSession,
            _locked_backup_repository,
            _validate_restore_target_names,
            _validated_manifest_entries,
        )

        manager = cls.backup_manager(
            archive_id,
            backup_root,
            device=device,
        )
        ipod_root = Path(os.path.realpath(device.path))
        with _locked_backup_repository(manager.backup_root):
            manifest = manager._load_manifest(snapshot_id)
            if manifest is None:
                raise DeviceWriteSafetyError(
                    f"The backup snapshot {snapshot_id!r} could not be found."
                )
            entries = _validated_manifest_entries(
                manifest,
                expected_snapshot_id=snapshot_id,
                expected_device_id=manager.device_id,
            )
            try:
                target_files = manager._validated_restore_files(
                    ipod_root,
                    entries,
                    filesystem_profile=profile,
                    progress_callback=progress,
                    is_cancelled=cancelled,
                )
            except _BackupOperationCancelled:
                return None

            # This deliberately avoids the optional case-sensitivity probe because
            # review must remain read-only. An unknown case policy is treated as
            # case-insensitive, which is the conservative collision rule.
            session = _RestoreWriteSession(ipod_root, profile)
            for relative_path in sorted(target_files):
                restore_file = target_files[relative_path]
                session.validate_target(relative_path, restore_file.size)
            _validate_restore_target_names(target_files, profile)

            try:
                volume = shutil.disk_usage(ipod_root)
            except OSError as exc:
                raise DeviceWriteSafetyError(
                    "The iPod capacity could not be verified during restore review."
                ) from exc
            allocation_unit = int(
                getattr(profile, "allocation_unit_size", 0) or 1
            )
            final_allocated_bytes = sum(
                allocated_size(item.size, allocation_unit)
                for item in target_files.values()
            )
            if final_allocated_bytes > int(volume.total):
                raise DeviceWriteSafetyError(
                    "The verified snapshot cannot fit on this iPod filesystem."
                )

            unique_blobs = {
                item.file_hash: item.size
                for item in target_files.values()
            }
            return {
                "snapshot_id": snapshot_id,
                "file_count": len(target_files),
                "unique_blobs_verified": len(unique_blobs),
                "verified_bytes": sum(unique_blobs.values()),
                "verification": "full_sha256",
                "filesystem_names_valid": True,
                "final_allocated_bytes": final_allocated_bytes,
                "volume_total_bytes": int(volume.total),
                "volume_free_bytes": int(volume.free),
                "final_state_fits": True,
                "atomic_temp_capacity_rechecked_on_execute": True,
                "ok": True,
            }

    @classmethod
    def restore_backup_snapshot(
        cls,
        device: Any,
        archive_id: str,
        snapshot_id: str,
        backup_root: str,
        volume_key: str,
        progress: Callable[[Any], None] | None,
        safety_progress: Callable[[Any], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        manager = cls.backup_manager(
            archive_id,
            backup_root,
            device=device,
        )
        before = {item.id for item in manager.list_snapshots()}
        safety_holder: list[Any] = []

        def on_safety_progress(event: Any) -> None:
            if str(getattr(event, "stage", "") or "").casefold() == "complete":
                safety = next(
                    (
                        item
                        for item in manager.list_snapshots()
                        if (
                            item.id not in before
                            and item.reason == "pre_restore_safety"
                        )
                    ),
                    None,
                )
                if safety is not None:
                    safety_holder[:] = [safety]
                    setattr(event, "safety_snapshot_id", safety.id)
            if safety_progress:
                safety_progress(event)

        restored = manager.restore_with_safety_checkpoint(
            snapshot_id,
            device.path,
            progress_callback=progress,
            safety_progress_callback=on_safety_progress,
            is_cancelled=cancelled,
            expected_volume_identity_key=volume_key,
            reported_volume_format=str(
                getattr(device, "reported_volume_format", "") or ""
            ),
        )
        safety = safety_holder[0] if safety_holder else next(
            (
                item
                for item in manager.list_snapshots()
                if item.id not in before and item.reason == "pre_restore_safety"
            ),
            None,
        )
        return {"restored": bool(restored), "safety_snapshot": safety}

    @staticmethod
    def restore_error_info(exc: Exception) -> dict[str, Any] | None:
        from iopenpod.sync.backup_manager import (
            RestoreDurabilityPendingError,
            RestoreIncompleteError,
        )

        if isinstance(exc, RestoreDurabilityPendingError):
            return {
                "code": "restore_durability_pending",
                "device_dirty": True,
                "content_verified": True,
                "requires_safe_eject": True,
                "safety_snapshot_id": str(
                    getattr(exc, "safety_snapshot_id", "") or ""
                ),
            }
        if isinstance(exc, RestoreIncompleteError):
            return {
                "code": "restore_incomplete",
                "device_dirty": True,
                "content_verified": False,
                "requires_safe_eject": False,
                "safety_snapshot_id": str(
                    getattr(exc, "safety_snapshot_id", "") or ""
                ),
            }
        return None

    @staticmethod
    def create_backup(
        device: Any,
        backup_root: str,
        volume_key: str,
        progress: Callable[[Any], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> Any:
        archive_id = IOpenPodAdapter.backup_archive_id(device)
        manager = IOpenPodAdapter.backup_manager(
            archive_id,
            backup_root,
            device=device,
        )
        return manager.create_backup(
            device.path,
            progress_callback=progress,
            is_cancelled=cancelled,
            expected_volume_identity_key=volume_key,
            reported_volume_format=str(getattr(device, "reported_volume_format", "") or ""),
            reason="pre-sync",
            _force_snapshot=True,
        )

    @classmethod
    def create_manual_backup(
        cls,
        device: Any,
        backup_root: str,
        volume_key: str,
        progress: Callable[[Any], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> Any:
        archive_id = cls.backup_archive_id(device)
        manager = cls.backup_manager(
            archive_id,
            backup_root,
            device=device,
        )
        return manager.create_backup(
            device.path,
            progress_callback=progress,
            is_cancelled=cancelled,
            expected_volume_identity_key=volume_key,
            reported_volume_format=str(
                getattr(device, "reported_volume_format", "") or ""
            ),
            reason="manual",
        )

    @staticmethod
    def compute_plan(
        device: Any,
        source_files: tuple[str, ...],
        ipod_tracks: tuple[dict[str, Any], ...],
        playlists: tuple[dict[str, Any], ...],
        progress: Callable[[Any], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> Any:
        from iopenpod.sync.core.engine import SyncEngine
        from iopenpod.sync.core.models import EngineOperation, EngineOptions, EngineRequest

        roots = tuple(sorted({str(Path(path).parent) for path in source_files}))
        request = EngineRequest(
            operation=EngineOperation.PLAN,
            ipod_path=device.path,
            pc_folders=roots,
            ipod_tracks=ipod_tracks,
            existing_playlists=playlists,
            options=EngineOptions(allowed_paths=frozenset(source_files)),
            device_info=device,
            progress_callback=progress,
            is_cancelled=cancelled,
        )
        outcome = SyncEngine().run(request)
        if not outcome.success:
            detail = "; ".join(item.message for item in outcome.diagnostics)
            raise IPodServiceError("plan_failed", detail or "iOpenPod could not build a sync plan.")
        return outcome.result

    @staticmethod
    def build_migration_bundle(
        entries: Mapping[str, Mapping[str, Any]],
        backup_root: str,
        bundle_dir: str,
        snapshot_fingerprint: str,
        progress: Callable[[str, int, int, str], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        return build_migration_bundle(
            entries,
            backup_root,
            bundle_dir,
            snapshot_fingerprint=snapshot_fingerprint,
            progress=progress,
            cancelled=cancelled,
        )

    @staticmethod
    def compute_migration_plan(
        device: Any,
        bundle_dir: str,
        bundle: Mapping[str, Any],
        ipod_tracks: tuple[dict[str, Any], ...],
        playlists: tuple[dict[str, Any], ...],
        progress: Callable[[Any], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> Any:
        return compute_iopenpod_migration_plan(
            device,
            bundle_dir,
            bundle,
            ipod_tracks,
            playlists,
            progress=progress,
            cancelled=cancelled,
        )

    @staticmethod
    def execute_plan(
        device: Any,
        plan: Any,
        database_generation: Any,
        profile: Any,
        progress: Callable[[Any], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> Any:
        from iopenpod.sync.core.engine import SyncEngine
        from iopenpod.sync.core.models import (
            EngineOperation, EngineOptions, EngineRequest, EngineTransactionPolicy,
        )

        request = EngineRequest(
            operation=EngineOperation.EXECUTE,
            ipod_path=device.path,
            plan=plan,
            options=EngineOptions(
                transaction_policy=EngineTransactionPolicy.CONSISTENT_PARTIALS,
            ),
            device_info=device,
            device_storage=profile,
            expected_database_generation=database_generation,
            progress_callback=progress,
            is_cancelled=cancelled,
            on_cancel_with_partial=lambda _done, _total: True,
        )
        outcome = SyncEngine().run(request)
        if not outcome.success:
            detail = "; ".join(item.message for item in outcome.diagnostics)
            raise IPodServiceError("execute_failed", detail or "The reviewed sync did not complete.")
        return outcome.result

    @staticmethod
    def eject(device: Any) -> tuple[bool, str]:
        from iopenpod.device.eject import eject_ipod
        return eject_ipod(
            device.path,
            reported_volume_format=str(getattr(device, "reported_volume_format", "") or ""),
            expected_volume_identity_key=str(getattr(device, "volume_identity_key", "") or ""),
        )


class IPodService:
    def __init__(
        self,
        app_data_dir: str | Path,
        *,
        adapter: IOpenPodAdapter | None = None,
        artifact_downloader: ArtifactDownloader | None = None,
        plan_ttl_seconds: int = DEFAULT_PLAN_TTL_SECONDS,
        restore_plan_ttl_seconds: int | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.app_data_dir = Path(app_data_dir)
        self.plan_dir = self.app_data_dir / "ipod-plans"
        self.restore_plan_dir = self.app_data_dir / "ipod-restore-plans"
        self.migration_plan_dir = self.app_data_dir / "ipod-migration-plans"
        self.migration_bundle_dir = self.app_data_dir / "ipod-migration-bundles"
        self.backup_dir = self.app_data_dir / "ipod-backups"
        self.staging_dir = self.app_data_dir / "ipod-staging"
        self.operation_journal_dir = self.app_data_dir / "ipod-operation-journal"
        self.capacity_unlock_dir = self.app_data_dir / "ipod-capacity-unlock"
        self.capacity_unlock_artifact_dir = self.capacity_unlock_dir / "artifacts"
        self.capacity_unlock_session_dir = self.capacity_unlock_dir / "sessions"
        self.adapter = adapter or IOpenPodAdapter()
        self.artifact_downloader = artifact_downloader or ArtifactDownloader()
        self.capacity_unlock_machine = CapacityUnlockStateMachine(
            self.capacity_unlock_dir / "sessions.json"
        )
        self.plan_ttl_seconds = max(60, int(plan_ttl_seconds))
        self.restore_plan_ttl_seconds = max(
            60,
            int(
                restore_plan_ttl_seconds
                if restore_plan_ttl_seconds is not None
                else plan_ttl_seconds
            ),
        )
        self.clock = clock
        self.operation_journal = OperationJournal(
            self.operation_journal_dir,
            clock=clock,
        )
        self._mutation_lock = threading.Lock()
        self._cancel = threading.Event()
        self._operation_state_lock = threading.RLock()
        self._active_operation_id = ""
        self._active_operation_kind = ""
        self._active_operation_phase = ""
        self._active_operation_can_cancel = False

    def scan(self) -> dict[str, Any]:
        devices = [self._device_summary(device) for device in self.adapter.scan_read_only()]
        return {"protocol_version": PROTOCOL_VERSION, "devices": devices}

    def _inspect_write_readiness(self, device: Any) -> Any:
        try:
            return self.adapter.inspect_write_readiness(device)
        except IPodServiceError:
            raise
        except Exception as exc:
            if (
                exc.__class__.__name__ == "DeviceWriteSafetyError"
                or isinstance(exc, PermissionError)
                or isinstance(exc, OSError)
            ):
                raise _translate_write_safety_error(exc) from exc
            raise

    def watch(
        self,
        emit: Callable[[dict[str, Any]], None],
        *,
        interval_seconds: float = 2,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        previous: dict[str, dict[str, Any]] = {}
        while not (cancelled and cancelled()):
            current_rows = self.scan()["devices"]
            current = {row["device_id"]: row for row in current_rows}
            for device_id, row in current.items():
                if device_id not in previous:
                    emit({"type": "ipod_connected", "protocol_version": PROTOCOL_VERSION, "data": row})
                elif row != previous[device_id]:
                    emit({"type": "ipod_changed", "protocol_version": PROTOCOL_VERSION, "data": row})
            for device_id, row in previous.items():
                if device_id not in current:
                    emit({"type": "ipod_disconnected", "protocol_version": PROTOCOL_VERSION, "data": row})
            previous = current
            time.sleep(max(0.2, interval_seconds))

    def browse(self, mount_path: str, resource: str, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(MAX_PAGE_SIZE, int(page_size)))
        device = self.adapter.identify_read_only(mount_path)
        before = self.adapter.capture_database_generation(device.path)
        library = self.adapter.load_library(device.path)
        after = self.adapter.capture_database_generation(device.path)
        if before != after:
            raise IPodServiceError("database_changed", "The iPod database changed while it was being read.")
        key = {
            "tracks": "mhlt", "albums": "mhla", "playlists": "mhlp",
            "podcasts": "mhlp_podcast", "smart_playlists": "mhlp_smart",
            "artists": "mhsd_type_8",
        }.get(resource)
        if key is None:
            raise IPodServiceError("invalid_resource", f"Unsupported iPod library resource: {resource}")
        rows = list(library.get(key, []) or [])
        start = (page - 1) * page_size
        return {
            "protocol_version": PROTOCOL_VERSION,
            "resource": resource,
            "page": page,
            "page_size": page_size,
            "total": len(rows),
            "database_generation": _generation_dict(after),
            "items": [_safe_row(row) for row in rows[start:start + page_size]],
        }

    def capacity_unlock_eligibility(self, mount_path: str) -> dict[str, Any]:
        """Return read-only, fail-closed evidence for the experimental workflow."""

        device = self.adapter.identify_read_only(mount_path)
        evidence = self._capacity_unlock_evidence(device)
        eligibility = evaluate_capacity_unlock_eligibility(evidence)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "experimental": True,
            "eligibility": eligibility.to_redacted_dto(),
            "evidence": evidence.to_redacted_dto(),
            "artifacts": [
                PINNED_UNLOCK_ARTIFACTS[artifact_id].to_dto()
                for artifact_id in sorted(PINNED_UNLOCK_ARTIFACTS)
            ],
            "acknowledgement_fields": list(UNLOCK_ACKNOWLEDGEMENT_FIELDS),
            "actions": sorted(UNLOCK_ACTIONS),
        }

    def start_capacity_unlock(
        self,
        mount_path: str,
        confirmed: bool,
        acknowledgements: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist an acknowledged session without touching the device."""

        if confirmed is not True:
            raise IPodServiceError(
                "confirmation_required",
                "Starting the experimental capacity-unlock workflow requires confirmation.",
            )
        acknowledgement_value = self._unlock_acknowledgements(acknowledgements)
        device = self.adapter.identify_read_only(mount_path)
        evidence = self._capacity_unlock_evidence(device)
        eligibility = evaluate_capacity_unlock_eligibility(evidence)
        if not eligibility.eligible:
            raise IPodServiceError(
                "device_ineligible",
                "The connected iPod does not meet every capacity-unlock safety gate.",
                details={
                    "issues": [
                        dataclasses.asdict(issue) for issue in eligibility.issues
                    ]
                },
            )
        try:
            session = self.capacity_unlock_machine.start_session(evidence)
            session = self.capacity_unlock_machine.acknowledge_environment(
                session.session_id,
                acknowledgement_value,
                expected_revision=session.revision,
            )
        except CapacityUnlockError as exc:
            raise IPodServiceError(exc.code, str(exc)) from exc
        (self.capacity_unlock_session_dir / session.session_id / "staging").mkdir(
            parents=True,
            exist_ok=False,
        )
        return {
            "protocol_version": PROTOCOL_VERSION,
            "experimental": True,
            "session": session.to_redacted_dto(),
            "actions": sorted(UNLOCK_ACTIONS),
        }

    def advance_capacity_unlock(
        self,
        session_id: str,
        action: str,
        confirmed: bool,
        data: Mapping[str, Any] | None,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Dispatch one bounded state-machine action; this method never writes NOR."""

        action_name = str(action or "").strip().lower()
        if action_name not in UNLOCK_ACTIONS:
            raise IPodServiceError(
                "invalid_unlock_action",
                "The requested capacity-unlock action is not supported.",
                details={"allowed_actions": sorted(UNLOCK_ACTIONS)},
            )
        payload = data or {}
        if not isinstance(payload, Mapping):
            raise IPodServiceError(
                "invalid_unlock_data",
                "Capacity-unlock action data must be an object.",
            )
        try:
            if action_name == "list":
                return self._unlock_result(
                    sessions=[
                        item.to_redacted_dto()
                        for item in self.capacity_unlock_machine.list_sessions()
                    ]
                )
            session = self.capacity_unlock_machine.get_session(session_id)
            if action_name == "status":
                return self._unlock_result(session=session.to_redacted_dto())
            if confirmed is not True:
                raise IPodServiceError(
                    "confirmation_required",
                    "Advancing a capacity-unlock session requires confirmation.",
                )
            if cancelled and cancelled():
                raise IPodServiceError(
                    "cancelled", "The capacity-unlock action was cancelled."
                )
            expected_revision = self._unlock_expected_revision(payload)
            if expected_revision != session.revision:
                raise IPodServiceError(
                    "stale_session",
                    "The capacity-unlock session changed; refresh its status.",
                )

            if action_name == "backup":
                updated = self._unlock_backup(
                    session,
                    payload,
                    expected_revision,
                    progress,
                    cancelled,
                )
            elif action_name == "artifacts":
                receipts = self._unlock_artifacts(payload, progress, cancelled)
                updated = self.capacity_unlock_machine.record_artifacts_verified(
                    session_id,
                    receipts,
                    expected_revision=expected_revision,
                )
            elif action_name == "bootloader-await":
                updated = self.capacity_unlock_machine.await_bootloader_install(
                    session_id, expected_revision=expected_revision
                )
            elif action_name == "bootloader-installed":
                if payload.get("user_attested") is not True:
                    raise IPodServiceError(
                        "bootloader_not_attested",
                        "The manual Rockbox bootloader step must be explicitly "
                        "attested.",
                    )
                helper_build = validate_rockbox_helper_build(
                    self._unlock_selected_file(
                        payload.get("helper_path"),
                        payload,
                        session_id,
                    ),
                    self._unlock_selected_file(
                        payload.get("source_path"),
                        payload,
                        session_id,
                    ),
                    self._unlock_selected_file(
                        payload.get("manifest_path"),
                        payload,
                        session_id,
                    ),
                    cancelled=cancelled,
                )
                updated = self.capacity_unlock_machine.record_bootloader_installed(
                    session_id,
                    user_attested=True,
                    helper_build=helper_build,
                    expected_revision=expected_revision,
                )
            elif action_name == "syscfg-original":
                original = self._unlock_read_selected_file(
                    payload.get("source_path"),
                    payload,
                    session_id,
                )
                backup_paths = payload.get("backup_paths")
                if not isinstance(backup_paths, list):
                    raise IPodServiceError(
                        "invalid_syscfg_copies",
                        "Choose the required independent SysCfg backup copies.",
                    )
                checked_paths = [
                    str(
                        self._unlock_selected_file(
                            path, payload, session_id
                        )
                    )
                    for path in backup_paths
                ]
                updated = self.capacity_unlock_machine.record_original_syscfg(
                    session_id,
                    original,
                    checked_paths,
                    expected_revision=expected_revision,
                )
            elif action_name == "syscfg-candidate":
                original = self._unlock_read_selected_file(
                    payload.get("original_path"),
                    payload,
                    session_id,
                )
                preset = audited_olsro_202_preset(
                    original,
                    source_model_number=session.source_model_number,
                )
                candidate = build_capacity_unlock_candidate(
                    original,
                    source_model_number=session.source_model_number,
                    preset=preset,
                )
                candidate_path = (
                    self.capacity_unlock_session_dir
                    / session_id
                    / "staging"
                    / "SysCfg-2.0.2.candidate.bin"
                )
                self._atomic_bytes(candidate_path, candidate.candidate_bytes)
                updated = self.capacity_unlock_machine.record_candidate_syscfg(
                    session_id,
                    candidate,
                    original_data=original,
                    preset=preset,
                    expected_revision=expected_revision,
                )
                return self._unlock_result(
                    session=updated.to_redacted_dto(),
                    candidate={
                        **candidate.to_redacted_dto(),
                        "path": str(candidate_path.resolve()),
                        "preset": preset.to_redacted_dto(),
                    },
                )
            elif action_name == "syscfg-stage":
                staged = self._unlock_read_selected_file(
                    payload.get("staged_path"),
                    payload,
                    session_id,
                )
                updated = self.capacity_unlock_machine.record_candidate_staged(
                    session_id,
                    staged,
                    expected_revision=expected_revision,
                )
            elif action_name == "nor-await":
                updated = self.capacity_unlock_machine.await_manual_nor_flash(
                    session_id, expected_revision=expected_revision
                )
            elif action_name == "nor-attested":
                readback = self._unlock_read_selected_file(
                    payload.get("readback_path"),
                    payload,
                    session_id,
                )
                updated = self.capacity_unlock_machine.attest_nor_flash(
                    session_id,
                    user_attested=payload.get("user_attested") is True,
                    reread_nor_data=readback,
                    expected_revision=expected_revision,
                )
            elif action_name == "dfu-await":
                updated = self.capacity_unlock_machine.await_dfu(
                    session_id, expected_revision=expected_revision
                )
            elif action_name == "dfu-detected":
                updated = self.capacity_unlock_machine.record_dfu_detected(
                    session_id,
                    usb_vendor_id=int(payload.get("usb_vendor_id", 0) or 0),
                    usb_product_id=int(payload.get("usb_product_id", 0) or 0),
                    expected_revision=expected_revision,
                )
            elif action_name == "itunes-handoff":
                updated = self.capacity_unlock_machine.record_itunes_handoff(
                    session_id,
                    user_attested=payload.get("user_attested") is True,
                    firmware_sha256=str(payload.get("firmware_sha256", "")),
                    expected_revision=expected_revision,
                )
            elif action_name == "restore-finished":
                updated = self.capacity_unlock_machine.attest_restore_finished(
                    session_id,
                    user_attested=payload.get("user_attested") is True,
                    expected_revision=expected_revision,
                )
            elif action_name == "postflight":
                updated = self._unlock_postflight(
                    session, payload, expected_revision
                )
            elif action_name == "recovery":
                updated = self.capacity_unlock_machine.require_recovery(
                    session_id,
                    reason_code=str(payload.get("reason_code", "")),
                    expected_revision=expected_revision,
                )
            elif action_name == "resume":
                updated = self.capacity_unlock_machine.resume_recovery(
                    session_id, expected_revision=expected_revision
                )
            else:
                updated = self.capacity_unlock_machine.cancel(
                    session_id, expected_revision=expected_revision
                )
            return self._unlock_result(session=updated.to_redacted_dto())
        except IPodServiceError:
            raise
        except (CapacityUnlockError, SysCfgError, ArtifactError) as exc:
            raise IPodServiceError(exc.code, str(exc)) from exc
        except (OSError, ValueError, TypeError) as exc:
            raise IPodServiceError(
                "invalid_unlock_input",
                "The capacity-unlock action input could not be validated.",
            ) from exc

    def list_backup_devices(self) -> dict[str, Any]:
        """List bounded archive summaries without requiring a connected iPod."""
        try:
            rows = list(self.adapter.list_backup_devices(str(self.backup_dir)))
        except Exception as exc:
            self._raise_backup_error("backup_repository_unavailable", exc)
        devices: list[dict[str, Any]] = []
        repository_size = 0
        seen_archives: set[str] = set()
        for raw in rows[:MAX_BACKUP_DEVICES]:
            if not isinstance(raw, Mapping):
                continue
            archive_id = str(raw.get("device_id", "") or "")
            if (
                not ARCHIVE_ID_RE.fullmatch(archive_id)
                or archive_id in seen_archives
            ):
                continue
            seen_archives.add(archive_id)
            try:
                archive_size = max(
                    0,
                    int(
                        self.adapter.backup_repository_size(
                            archive_id,
                            str(self.backup_dir),
                        )
                    ),
                )
            except Exception as exc:
                self._raise_backup_error("backup_repository_unavailable", exc)
            repository_size += archive_size
            devices.append({
                "archive_id": archive_id,
                "device_name": str(raw.get("device_name", "") or "iPod")[:300],
                "snapshot_count": max(0, int(raw.get("snapshot_count", 0) or 0)),
                "identity_is_stable": bool(raw.get("identity_is_stable", False)),
                "repository_size_bytes": archive_size,
                "device_meta": _bounded_json(
                    raw.get("device_meta", {}),
                    max_items=30,
                    max_string=500,
                ),
            })
        return {
            "protocol_version": PROTOCOL_VERSION,
            "total": len(rows),
            "truncated": len(rows) > MAX_BACKUP_DEVICES,
            "repository_size_bytes": repository_size,
            "devices": devices,
        }

    def list_backup_snapshots(
        self,
        archive_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        archive_id = self._validate_archive_id(archive_id)
        page = max(1, int(page))
        page_size = max(1, min(MAX_BACKUP_PAGE_SIZE, int(page_size)))
        try:
            snapshots = list(
                self.adapter.list_backup_snapshots(
                    archive_id,
                    str(self.backup_dir),
                )
            )
            repository_size = int(
                self.adapter.backup_repository_size(
                    archive_id,
                    str(self.backup_dir),
                )
            )
        except Exception as exc:
            self._raise_backup_error("backup_repository_unavailable", exc)
        start = (page - 1) * page_size
        return {
            "protocol_version": PROTOCOL_VERSION,
            "archive_id": archive_id,
            "page": page,
            "page_size": page_size,
            "total": len(snapshots),
            "repository_size_bytes": max(0, repository_size),
            "items": [
                self._snapshot_dto(item)
                for item in snapshots[start:start + page_size]
            ],
        }

    def backup_repository_size(self, archive_id: str) -> dict[str, Any]:
        archive_id = self._validate_archive_id(archive_id)
        try:
            size = int(
                self.adapter.backup_repository_size(
                    archive_id,
                    str(self.backup_dir),
                )
            )
        except Exception as exc:
            self._raise_backup_error("backup_repository_unavailable", exc)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "archive_id": archive_id,
            "repository_size_bytes": max(0, size),
        }

    def backup_snapshot_details(
        self,
        archive_id: str,
        snapshot_id: str,
    ) -> dict[str, Any]:
        archive_id = self._validate_archive_id(archive_id)
        snapshot_id = self._validate_snapshot_id(snapshot_id)
        manifest, _entries, fingerprint = self._load_backup_manifest(
            archive_id,
            snapshot_id,
        )
        try:
            repository_size = int(
                self.adapter.backup_repository_size(
                    archive_id,
                    str(self.backup_dir),
                )
            )
        except Exception as exc:
            self._raise_backup_error("backup_repository_unavailable", exc)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "archive_id": archive_id,
            "snapshot": self._manifest_snapshot_dto(manifest, fingerprint),
            "scope": self._backup_scope(manifest),
            "exclusions": self._backup_exclusions(),
            "repository_size_bytes": max(0, repository_size),
        }

    def update_backup_note(
        self,
        archive_id: str,
        snapshot_id: str,
        note: str,
    ) -> dict[str, Any]:
        archive_id = self._validate_archive_id(archive_id)
        snapshot_id = self._validate_snapshot_id(snapshot_id)
        if not isinstance(note, str):
            raise IPodServiceError("invalid_note", "A backup note must be text.")
        normalized = note.strip()
        if len(normalized) > MAX_SNAPSHOT_NOTE_LENGTH:
            raise IPodServiceError(
                "invalid_note",
                f"A backup note cannot exceed {MAX_SNAPSHOT_NOTE_LENGTH:,} characters.",
            )
        if not self._mutation_lock.acquire(blocking=False):
            raise IPodServiceError(
                "mutation_busy",
                "Another iPod or backup mutation is already running.",
            )
        try:
            try:
                updated = self.adapter.update_backup_note(
                    archive_id,
                    snapshot_id,
                    normalized,
                    str(self.backup_dir),
                )
            except Exception as exc:
                self._raise_backup_error("backup_note_failed", exc)
            if not updated:
                raise IPodServiceError(
                    "backup_note_failed",
                    "The backup note could not be updated.",
                )
        finally:
            self._mutation_lock.release()
        details = self.backup_snapshot_details(archive_id, snapshot_id)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "ok": True,
            "archive_id": archive_id,
            "snapshot": details["snapshot"],
        }

    def manual_backup(
        self,
        mount_path: str,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        device = self.adapter.identify_read_only(mount_path)
        self._require_supported_identity(device)
        profile = self._inspect_write_readiness(device)
        volume_key = self.adapter.volume_key(profile)
        archive_id = self._backup_archive_id(device)
        if not self._mutation_lock.acquire(blocking=False):
            raise IPodServiceError(
                "mutation_busy",
                "Another iPod or backup mutation is already running.",
            )
        operation_id = ""
        try:
            operation_id = self._begin_operation(
                "manual_backup",
                phase="backing_up",
                can_cancel=True,
                target_id=self._stable_device_id(device),
                target_archive_id=archive_id,
                metadata={"mount_path": _canonical_path(device.path)},
            )
            combined_cancel = self._combined_cancel(operation_id, cancelled)
            try:
                creator = getattr(
                    self.adapter,
                    "create_manual_backup",
                    self.adapter.create_backup,
                )
                snapshot = creator(
                    device,
                    str(self.backup_dir),
                    volume_key,
                    self._operation_progress_adapter(
                        "backup",
                        progress,
                        operation_id,
                    ),
                    combined_cancel,
                )
            except Exception as exc:
                self._finish_failed_operation(operation_id, exc)
                self._raise_backup_error("backup_failed", exc)
            if snapshot is None and combined_cancel():
                self._finish_operation(
                    operation_id,
                    "cancelled",
                    phase="cancelled",
                )
                raise IPodServiceError(
                    "cancelled",
                    "The manual iPod backup was cancelled.",
                    details={"operation_id": operation_id},
                )
            self._finish_operation(operation_id, "succeeded")
            return {
                "protocol_version": PROTOCOL_VERSION,
                "ok": True,
                "created": snapshot is not None,
                "reason": "created" if snapshot is not None else "no_changes",
                "operation_id": operation_id,
                "archive_id": archive_id,
                "snapshot": (
                    self._snapshot_dto(snapshot) if snapshot is not None else None
                ),
            }
        finally:
            self._clear_active_operation(operation_id)
            self._mutation_lock.release()

    def verify_backup_snapshot(
        self,
        archive_id: str,
        snapshot_id: str,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        archive_id = self._validate_archive_id(archive_id)
        snapshot_id = self._validate_snapshot_id(snapshot_id)
        if not self._mutation_lock.acquire(blocking=False):
            raise IPodServiceError(
                "mutation_busy",
                "Another iPod or backup mutation is already running.",
            )
        operation_id = ""
        try:
            operation_id = self._begin_operation(
                "backup_verify",
                phase="verifying",
                can_cancel=True,
                source_archive_id=archive_id,
                snapshot_id=snapshot_id,
            )
            combined_cancel = self._combined_cancel(operation_id, cancelled)
            try:
                result = self.adapter.deep_verify_backup_snapshot(
                    archive_id,
                    snapshot_id,
                    str(self.backup_dir),
                    self._operation_progress_adapter(
                        "backup-verify",
                        progress,
                        operation_id,
                    ),
                    combined_cancel,
                )
            except Exception as exc:
                self._finish_failed_operation(operation_id, exc)
                self._raise_backup_error("backup_verification_failed", exc)
            if result is None:
                self._finish_operation(
                    operation_id,
                    "cancelled",
                    phase="cancelled",
                )
                raise IPodServiceError(
                    "cancelled",
                    "Backup verification was cancelled.",
                    details={"operation_id": operation_id},
                )
            self._finish_operation(operation_id, "succeeded")
            return {
                "protocol_version": PROTOCOL_VERSION,
                "operation_id": operation_id,
                "archive_id": archive_id,
                **_bounded_json(result, max_items=30, max_string=500),
            }
        finally:
            self._clear_active_operation(operation_id)
            self._mutation_lock.release()

    def export_backup_snapshot(
        self,
        archive_id: str,
        snapshot_id: str,
        destination_dir: str | Path,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        archive_id = self._validate_archive_id(archive_id)
        snapshot_id = self._validate_snapshot_id(snapshot_id)
        destination = str(destination_dir).strip()
        if not destination:
            raise IPodServiceError(
                "invalid_export_destination",
                "Choose a parent folder for the backup export.",
            )
        if not self._mutation_lock.acquire(blocking=False):
            raise IPodServiceError(
                "mutation_busy",
                "Another iPod or backup mutation is already running.",
            )
        operation_id = ""
        try:
            operation_id = self._begin_operation(
                "backup_export",
                phase="exporting",
                can_cancel=True,
                source_archive_id=archive_id,
                snapshot_id=snapshot_id,
            )
            combined_cancel = self._combined_cancel(operation_id, cancelled)
            try:
                result = self.adapter.export_backup_snapshot(
                    archive_id,
                    snapshot_id,
                    destination,
                    str(self.backup_dir),
                    self._operation_progress_adapter(
                        "backup-export",
                        progress,
                        operation_id,
                    ),
                    combined_cancel,
                )
            except Exception as exc:
                self._finish_failed_operation(operation_id, exc)
                self._raise_backup_error("backup_export_failed", exc)
            if result is None:
                self._finish_operation(
                    operation_id,
                    "cancelled",
                    phase="cancelled",
                )
                raise IPodServiceError(
                    "cancelled",
                    "The backup export was cancelled.",
                    details={"operation_id": operation_id},
                )
            self._finish_operation(operation_id, "succeeded")
            return {
                "protocol_version": PROTOCOL_VERSION,
                "ok": True,
                "operation_id": operation_id,
                "archive_id": archive_id,
                "snapshot_id": snapshot_id,
                "export": _bounded_json(result, max_items=20, max_string=2_000),
            }
        finally:
            self._clear_active_operation(operation_id)
            self._mutation_lock.release()

    def delete_backup_snapshot(
        self,
        archive_id: str,
        snapshot_id: str,
        *,
        confirmed: bool,
    ) -> dict[str, Any]:
        archive_id = self._validate_archive_id(archive_id)
        snapshot_id = self._validate_snapshot_id(snapshot_id)
        if not confirmed:
            raise IPodServiceError(
                "confirmation_required",
                "Confirm deletion of the selected backup snapshot.",
            )
        if not self._mutation_lock.acquire(blocking=False):
            raise IPodServiceError(
                "mutation_busy",
                "Another iPod or backup mutation is already running.",
            )
        operation_id = ""
        try:
            operation_id = self._begin_operation(
                "backup_delete",
                phase="committing",
                can_cancel=False,
                source_archive_id=archive_id,
                snapshot_id=snapshot_id,
            )
            try:
                deleted = self.adapter.delete_backup_snapshot(
                    archive_id,
                    snapshot_id,
                    str(self.backup_dir),
                )
            except Exception as exc:
                self._finish_failed_operation(operation_id, exc)
                self._raise_backup_error("backup_delete_failed", exc)
            if not deleted:
                error = IPodServiceError(
                    "backup_delete_failed",
                    "The selected backup snapshot was not deleted.",
                )
                self._finish_failed_operation(operation_id, error)
                raise error
            self._finish_operation(operation_id, "succeeded")
            return {
                "protocol_version": PROTOCOL_VERSION,
                "ok": True,
                "operation_id": operation_id,
                "archive_id": archive_id,
                "snapshot_id": snapshot_id,
            }
        finally:
            self._clear_active_operation(operation_id)
            self._mutation_lock.release()

    def restore_preflight(
        self,
        mount_path: str,
        archive_id: str,
        snapshot_id: str,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Bind an immutable, short-lived restore plan to this exact iPod."""
        archive_id = self._validate_archive_id(archive_id)
        snapshot_id = self._validate_snapshot_id(snapshot_id)
        device = self.adapter.identify_read_only(mount_path)
        self._require_supported_identity(device)
        stable_device_id = self._stable_device_id(device)
        target_archive_id = self._backup_archive_id(device)
        if target_archive_id != archive_id:
            raise IPodServiceError(
                "wrong_restore_target",
                "This backup archive belongs to a different iPod. Raw restore to "
                "a replacement device is blocked.",
            )
        profile = self._inspect_write_readiness(device)
        volume_key = self.adapter.volume_key(profile)
        generation = self.adapter.capture_database_generation(device.path)
        manifest, _entries, fingerprint = self._load_backup_manifest(
            archive_id,
            snapshot_id,
        )
        if not bool(manifest.get("identity_is_stable", False)):
            raise IPodServiceError(
                "unstable_backup_identity",
                "This snapshot lacks stable hardware identity and cannot authorize "
                "a destructive restore.",
            )
        source_stable_id = str(
            (manifest.get("device_meta") or {}).get("stable_device_id", "") or ""
        )
        if source_stable_id and source_stable_id != stable_device_id:
            raise IPodServiceError(
                "wrong_restore_target",
                "The selected snapshot's hardware identity does not match this iPod.",
            )
        try:
            verification = self.adapter.preflight_restore_snapshot(
                device,
                archive_id,
                snapshot_id,
                str(self.backup_dir),
                profile,
                self._progress_adapter("restore-preflight", progress),
                cancelled,
            )
        except Exception as exc:
            self._raise_backup_error("restore_preflight_failed", exc)
        if verification is None:
            raise IPodServiceError(
                "cancelled",
                "Restore preflight was cancelled before a plan was created.",
            )
        if (
            verification.get("ok") is not True
            or verification.get("verification") != "full_sha256"
            or int(verification.get("file_count", -1))
            != int(manifest.get("file_count", -2))
        ):
            raise IPodServiceError(
                "restore_preflight_failed",
                "The restore snapshot did not pass complete preflight verification.",
            )
        restore_plan_id = f"restore_{secrets.token_urlsafe(24)}"
        now = float(self.clock())
        record = {
            "schema_version": RESTORE_PLAN_SCHEMA_VERSION,
            "restore_plan_id": restore_plan_id,
            "created_at": now,
            "expires_at": now + self.restore_plan_ttl_seconds,
            "source_archive_id": archive_id,
            "source_snapshot_id": snapshot_id,
            "snapshot_binding": self._snapshot_binding(manifest, fingerprint),
            "mount_path": _canonical_path(device.path),
            "target_device_id": stable_device_id,
            "target_archive_id": target_archive_id,
            "volume_identity_key": volume_key,
            "database_generation": _generation_dict(generation),
            "preflight_verification": _bounded_json(
                verification,
                max_items=30,
                max_string=500,
            ),
        }
        self._write_restore_plan(record)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "restore_plan_id": restore_plan_id,
            "source_archive_id": archive_id,
            "source_snapshot_id": snapshot_id,
            "snapshot": self._manifest_snapshot_dto(manifest, fingerprint),
            "scope": self._backup_scope(manifest),
            "exclusions": self._backup_exclusions(),
            "target": {
                "device_id": stable_device_id,
                "archive_id": target_archive_id,
                "name": str(
                    getattr(device, "ipod_name", "")
                    or getattr(device, "display_name", "")
                    or "iPod"
                )[:300],
                "model_family": str(
                    getattr(device, "model_family", "") or ""
                )[:100],
                "database_generation": _generation_dict(generation),
            },
            "verification": {
                "ok": True,
                "method": str(verification.get("verification", "")),
                "file_count": int(verification.get("file_count", 0)),
                "unique_blobs_verified": int(
                    verification.get("unique_blobs_verified", 0)
                ),
                "verified_bytes": int(verification.get("verified_bytes", 0)),
                "filesystem_names_valid": bool(
                    verification.get("filesystem_names_valid", False)
                ),
            },
            "storage": {
                "final_allocated_bytes": int(
                    verification.get("final_allocated_bytes", 0)
                ),
                "volume_total_bytes": int(
                    verification.get("volume_total_bytes", 0)
                ),
                "volume_free_bytes": int(
                    verification.get("volume_free_bytes", 0)
                ),
                "final_state_fits": bool(
                    verification.get("final_state_fits", False)
                ),
                "atomic_temp_capacity_rechecked_on_execute": bool(
                    verification.get(
                        "atomic_temp_capacity_rechecked_on_execute",
                        False,
                    )
                ),
            },
            "expires_at": record["expires_at"],
            "confirmation_required": True,
            "raw_replacement_restore_allowed": False,
        }

    def create_restore_preflight(
        self,
        mount_path: str,
        archive_id: str,
        snapshot_id: str,
    ) -> dict[str, Any]:
        return self.restore_preflight(mount_path, archive_id, snapshot_id)

    def restore(
        self,
        restore_plan_id: str,
        *,
        confirmed: bool,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if not confirmed:
            raise IPodServiceError(
                "confirmation_required",
                "Confirm the reviewed same-device restore before continuing.",
            )
        record = self._read_restore_plan(restore_plan_id)
        if not self._mutation_lock.acquire(blocking=False):
            raise IPodServiceError(
                "mutation_busy",
                "Another iPod or backup mutation is already running.",
            )
        operation_id = ""
        try:
            device, profile, manifest, fingerprint = (
                self._revalidate_restore_record(record)
            )
            operation_id = self._begin_operation(
                "restore",
                phase="revalidating",
                can_cancel=True,
                target_id=str(record["target_device_id"]),
                source_id=str(
                    (manifest.get("device_meta") or {}).get(
                        "stable_device_id",
                        record["target_device_id"],
                    )
                    or record["target_device_id"]
                ),
                target_archive_id=str(record["target_archive_id"]),
                source_archive_id=str(record["source_archive_id"]),
                snapshot_id=str(record["source_snapshot_id"]),
                reconnect=self._restore_reconnect_info(device, record),
                metadata={
                    "restore_plan_id": restore_plan_id,
                    "snapshot_fingerprint": fingerprint,
                },
            )
            combined_cancel = self._combined_cancel(operation_id, cancelled)
            self._transition_operation(
                operation_id,
                "safety_checkpoint",
                can_cancel=True,
            )
            try:
                result = self.adapter.restore_backup_snapshot(
                    device,
                    str(record["source_archive_id"]),
                    str(record["source_snapshot_id"]),
                    str(self.backup_dir),
                    self.adapter.volume_key(profile),
                    self._operation_progress_adapter(
                        "restore",
                        progress,
                        operation_id,
                    ),
                    self._operation_progress_adapter(
                        "restore-safety",
                        progress,
                        operation_id,
                    ),
                    combined_cancel,
                )
            except Exception as exc:
                restore_error = self._restore_error_info(exc)
                if restore_error is not None:
                    self._record_restore_recovery(
                        operation_id,
                        device,
                        record,
                        exc,
                        restore_error,
                    )
                    raise IPodServiceError(
                        str(restore_error["code"]),
                        str(exc)[:2_000],
                        details={
                            "operation_id": operation_id,
                            "recovery": self.operation_journal.recovery_state(),
                        },
                    ) from exc
                self._finish_failed_operation(operation_id, exc)
                self._raise_backup_error("restore_failed", exc)
            restored = bool(
                result.get("restored", False)
                if isinstance(result, Mapping)
                else result
            )
            safety_snapshot = (
                result.get("safety_snapshot")
                if isinstance(result, Mapping)
                else None
            )
            safety_snapshot_id = self._snapshot_id_from_value(safety_snapshot)
            if safety_snapshot_id:
                self._transition_operation(
                    operation_id,
                    self._active_operation_phase or "finalizing",
                    can_cancel=self._active_operation_can_cancel,
                    safety_snapshot_id=safety_snapshot_id,
                )
            if not restored:
                self._finish_operation(
                    operation_id,
                    "cancelled",
                    phase="cancelled",
                    safety_snapshot_id=safety_snapshot_id,
                )
                raise IPodServiceError(
                    "cancelled",
                    "The restore was cancelled before its commit boundary.",
                    details={
                        "operation_id": operation_id,
                        "safety_snapshot_id": safety_snapshot_id,
                    },
                )
            self._transition_operation(
                operation_id,
                "finalizing",
                can_cancel=False,
                safety_snapshot_id=safety_snapshot_id,
            )
            self._finish_operation(
                operation_id,
                "succeeded",
                safety_snapshot_id=safety_snapshot_id,
            )
            self._delete_restore_plan(restore_plan_id)
            return {
                "protocol_version": PROTOCOL_VERSION,
                "ok": True,
                "operation_id": operation_id,
                "source_archive_id": record["source_archive_id"],
                "source_snapshot_id": record["source_snapshot_id"],
                "target_device_id": record["target_device_id"],
                "safety_snapshot": (
                    self._snapshot_dto(safety_snapshot)
                    if safety_snapshot is not None
                    else None
                ),
            }
        finally:
            self._clear_active_operation(operation_id)
            self._mutation_lock.release()

    def recovery_state(self) -> dict[str, Any]:
        try:
            state = self.operation_journal.recovery_state()
        except OperationJournalError as exc:
            raise IPodServiceError(
                "operation_journal_invalid",
                str(exc),
            ) from exc
        return {
            "protocol_version": PROTOCOL_VERSION,
            **_bounded_json(state, max_items=100, max_string=2_000),
        }

    def migration_preflight(
        self,
        mount_path: str,
        archive_id: str,
        snapshot_id: str,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Build an immutable reviewed media-sync plan for a replacement iPod."""
        archive_id = self._validate_archive_id(archive_id)
        snapshot_id = self._validate_snapshot_id(snapshot_id)
        device = self.adapter.identify_read_only(mount_path)
        self._require_supported_identity(device)
        profile = self._inspect_write_readiness(device)
        manifest, entries, fingerprint = self._load_backup_manifest(
            archive_id,
            snapshot_id,
        )
        target_device_id = self._stable_device_id(device)
        target_archive_id = self._backup_archive_id(device)
        source_meta = (
            manifest.get("device_meta")
            if isinstance(manifest.get("device_meta"), Mapping)
            else {}
        )
        source_device_id = str(source_meta.get("stable_device_id", "") or "")
        same_device = bool(
            source_device_id == target_device_id
            or target_archive_id == archive_id
        )
        target_meta = {
            **self._backup_device_meta(device),
            "archive_id": target_archive_id,
        }
        if same_device:
            return self._migration_block(
                "same_device_use_restore",
                "This is the original iPod. Use same-device restore preflight; "
                "replacement migration is not applicable.",
                archive_id=archive_id,
                snapshot_id=snapshot_id,
                fingerprint=fingerprint,
                source_meta=source_meta,
                target_meta=target_meta,
                same_device=True,
            )
        compatibility_issues = self._migration_compatibility_issues(
            manifest,
            source_meta,
            target_meta,
        )
        generation, target_tracks, target_playlists = self._load_for_plan(device)
        generation_payload = _generation_dict(generation)
        if not bool(generation_payload.get("exists")) or int(
            generation_payload.get("size", 0) or 0
        ) <= 0:
            compatibility_issues.append({
                "field": "target_database",
                "source": "initialized snapshot",
                "target": generation_payload,
                "message": "The replacement iPod must have an initialized database.",
            })
        if compatibility_issues:
            return self._migration_block(
                "migration_profile_incompatible",
                "The replacement iPod does not match the source database profile.",
                archive_id=archive_id,
                snapshot_id=snapshot_id,
                fingerprint=fingerprint,
                source_meta=source_meta,
                target_meta=target_meta,
                issues=compatibility_issues,
            )
        if not self._mutation_lock.acquire(blocking=False):
            raise IPodServiceError(
                "mutation_busy",
                "Another iPod or backup mutation is already running.",
            )
        migration_plan_id = f"migration_{secrets.token_urlsafe(24)}"
        bundle_path = self.migration_bundle_dir / migration_plan_id
        try:
            verified = self.adapter.deep_verify_backup_snapshot(
                archive_id,
                snapshot_id,
                str(self.backup_dir),
                self._progress_adapter("migration-verify", progress),
                cancelled,
            )
            if verified is None:
                raise IPodServiceError(
                    "cancelled",
                    "The replacement migration was cancelled during verification.",
                )

            def staging_progress(
                stage: str,
                current: int,
                total: int,
                message: str,
            ) -> None:
                self._emit(
                    progress,
                    f"migration:{stage}",
                    current,
                    total,
                    message,
                )

            try:
                bundle = self.adapter.build_migration_bundle(
                    entries,
                    str(self.backup_dir),
                    str(bundle_path),
                    fingerprint,
                    staging_progress,
                    cancelled,
                )
            except MigrationBundleError as exc:
                raise IPodServiceError(
                    exc.code,
                    str(exc),
                    details=exc.details,
                ) from exc
            source_files = migration_source_files(bundle_path, bundle)
            plan = self.adapter.compute_migration_plan(
                device,
                str(bundle_path),
                bundle,
                target_tracks,
                target_playlists,
                self._progress_adapter("migration-plan", progress),
                cancelled,
            )
            if cancelled and cancelled():
                raise IPodServiceError(
                    "cancelled",
                    "The replacement migration was cancelled during planning.",
                )
            details = self._plan_details(plan)
            summary = self._plan_summary(plan, source_files, details, device)
            unsafe_groups = self._migration_plan_unsafe_counts(
                plan,
                details,
            )
            if any(unsafe_groups.values()):
                shutil.rmtree(bundle_path, ignore_errors=True)
                return self._migration_block(
                    "migration_plan_not_safe",
                    "iOpenPod could not produce an addition-only, fully resolved "
                    "replacement migration plan.",
                    archive_id=archive_id,
                    snapshot_id=snapshot_id,
                    fingerprint=fingerprint,
                    source_meta=source_meta,
                    target_meta=target_meta,
                    issues=[
                        {
                            "field": group,
                            "count": count,
                            "message": (
                                "Replacement migration requires zero planned "
                                f"{group}."
                            ),
                        }
                        for group, count in unsafe_groups.items()
                        if count
                    ],
                )
            required_bytes = int(summary["required_bytes"])
            free_bytes = int(
                float(getattr(device, "free_space_gb", 0) or 0)
                * 1_000_000_000
            )
            if required_bytes > free_bytes:
                shutil.rmtree(bundle_path, ignore_errors=True)
                return self._migration_block(
                    "migration_insufficient_space",
                    "The replacement iPod does not have enough free space for "
                    "the reviewed migration.",
                    archive_id=archive_id,
                    snapshot_id=snapshot_id,
                    fingerprint=fingerprint,
                    source_meta=source_meta,
                    target_meta=target_meta,
                    issues=[{
                        "field": "free_space",
                        "required_bytes": required_bytes,
                        "available_bytes": free_bytes,
                    }],
                )
            now = float(self.clock())
            record = {
                "schema_version": MIGRATION_PLAN_SCHEMA_VERSION,
                "migration_plan_id": migration_plan_id,
                "created_at": now,
                "expires_at": now + self.restore_plan_ttl_seconds,
                "source_archive_id": archive_id,
                "source_snapshot_id": snapshot_id,
                "source_device_id": source_device_id,
                "snapshot_binding": self._snapshot_binding(
                    manifest,
                    fingerprint,
                ),
                "mount_path": _canonical_path(device.path),
                "target_device_id": target_device_id,
                "target_archive_id": target_archive_id,
                "volume_identity_key": self.adapter.volume_key(profile),
                "database_generation": generation_payload,
                "bundle_path": str(bundle_path.resolve()),
                "bundle_fingerprint": bundle["bundle_fingerprint"],
                "required_bytes": required_bytes,
                "summary": summary,
                "details": details,
            }
            self._write_migration_plan(record)
            return {
                "protocol_version": PROTOCOL_VERSION,
                "blocked": False,
                "compatible": True,
                "safe_migration_available": True,
                "raw_restore_allowed": False,
                "migration_plan_id": migration_plan_id,
                "confirmation_required": True,
                "target_safety_backup_required": True,
                "source": {
                    "archive_id": archive_id,
                    "snapshot_id": snapshot_id,
                    "device_id": source_device_id,
                    "snapshot_fingerprint": fingerprint,
                },
                "target": {
                    "archive_id": target_archive_id,
                    "device_id": target_device_id,
                    "model_family": target_meta.get("model_family", ""),
                    "generation": target_meta.get("generation", ""),
                    "database_generation": generation_payload,
                },
                "staging_bundle": {
                    "schema_version": MIGRATION_BUNDLE_SCHEMA_VERSION,
                    "path": str(bundle_path.resolve()),
                    "fingerprint": bundle["bundle_fingerprint"],
                    "media_file_count": bundle["media_file_count"],
                    "playlist_count": bundle["playlist_count"],
                    "total_media_bytes": bundle["total_media_bytes"],
                },
                "metadata": bundle["limitations"],
                **summary,
                "groups": self._detail_group_descriptors(details),
                "group_previews": {
                    group: items[:PLAN_PREVIEW_SIZE]
                    for group, items in details.items()
                    if items
                },
                "expires_at": record["expires_at"],
            }
        except MigrationBundleError as exc:
            if not (
                self.migration_plan_dir / f"{migration_plan_id}.json"
            ).exists():
                shutil.rmtree(bundle_path, ignore_errors=True)
            raise IPodServiceError(
                exc.code,
                str(exc),
                details=exc.details,
            ) from exc
        except Exception:
            if not (
                self.migration_plan_dir / f"{migration_plan_id}.json"
            ).exists():
                shutil.rmtree(bundle_path, ignore_errors=True)
            raise
        finally:
            self._mutation_lock.release()

    def migration(
        self,
        migration_plan_id: str,
        *,
        confirmed: bool,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Execute a reviewed replacement migration through normal SyncEngine."""

        if confirmed is not True:
            raise IPodServiceError(
                "confirmation_required",
                "Confirm the reviewed replacement migration before continuing.",
            )
        record = self._read_migration_plan(migration_plan_id)
        if not self._mutation_lock.acquire(blocking=False):
            raise IPodServiceError(
                "mutation_busy",
                "Another iPod or backup mutation is already running.",
            )
        operation_id = ""
        safety_snapshot_id = ""
        try:
            (
                device,
                generation,
                profile,
                manifest,
                bundle,
                source_files,
                target_tracks,
                target_playlists,
            ) = self._revalidate_migration_record(record, cancelled=cancelled)
            operation_id = self._begin_operation(
                "migration",
                phase="revalidating",
                can_cancel=True,
                target_id=str(record["target_device_id"]),
                source_id=str(record["source_device_id"]),
                target_archive_id=str(record["target_archive_id"]),
                source_archive_id=str(record["source_archive_id"]),
                snapshot_id=str(record["source_snapshot_id"]),
                reconnect={
                    "required_device_id": record["target_device_id"],
                    "mount_path": record["mount_path"],
                },
                metadata={
                    "migration_plan_id": migration_plan_id,
                    "snapshot_fingerprint": record["snapshot_binding"][
                        "fingerprint"
                    ],
                    "bundle_fingerprint": record["bundle_fingerprint"],
                },
            )
            combined_cancel = self._combined_cancel(operation_id, cancelled)
            plan = self.adapter.compute_migration_plan(
                device,
                str(record["bundle_path"]),
                bundle,
                target_tracks,
                target_playlists,
                self._operation_progress_adapter(
                    "migration-plan",
                    progress,
                    operation_id,
                ),
                combined_cancel,
            )
            if combined_cancel():
                self._finish_operation(
                    operation_id,
                    "cancelled",
                    phase="cancelled",
                )
                raise IPodServiceError(
                    "cancelled",
                    "The replacement migration was cancelled before writing.",
                    details={"operation_id": operation_id},
                )
            details = self._plan_details(plan)
            summary = self._plan_summary(plan, source_files, details, device)
            if (
                summary != record["summary"]
                or details != record["details"]
                or any(
                    self._migration_plan_unsafe_counts(
                        plan,
                        details,
                    ).values()
                )
            ):
                raise IPodServiceError(
                    "stale_migration_plan",
                    "The replacement migration result changed after review.",
                )
            self._transition_operation(
                operation_id,
                "backing_up",
                can_cancel=True,
            )
            snapshot = self.adapter.create_backup(
                device,
                str(self.backup_dir),
                str(record["volume_identity_key"]),
                self._operation_progress_adapter(
                    "migration-backup",
                    progress,
                    operation_id,
                ),
                combined_cancel,
            )
            if combined_cancel():
                self._finish_operation(
                    operation_id,
                    "cancelled",
                    phase="cancelled",
                )
                raise IPodServiceError(
                    "cancelled",
                    "The replacement migration was cancelled before writing.",
                    details={"operation_id": operation_id},
                )
            if snapshot is None:
                raise IPodServiceError(
                    "backup_failed",
                    "A verified target safety backup was not created.",
                )
            safety_snapshot_id = self._snapshot_id_from_value(snapshot)
            self._transition_operation(
                operation_id,
                "revalidating",
                can_cancel=True,
                safety_snapshot_id=safety_snapshot_id,
            )
            (
                device_after_backup,
                generation_after_backup,
                profile_after_backup,
                _manifest,
                _bundle,
                _source_files,
                _target_tracks,
                _target_playlists,
            ) = self._revalidate_migration_record(
                record,
                cancelled=combined_cancel,
            )
            if generation_after_backup != generation:
                raise IPodServiceError(
                    "stale_migration_plan",
                    "The target database changed during its safety backup.",
                )
            result = self.adapter.execute_plan(
                device_after_backup,
                plan,
                generation_after_backup,
                profile_after_backup,
                self._operation_progress_adapter(
                    "migration-execute",
                    progress,
                    operation_id,
                ),
                combined_cancel,
            )
            self._transition_operation(
                operation_id,
                "finalizing",
                can_cancel=False,
                safety_snapshot_id=safety_snapshot_id,
            )
            self._finish_operation(
                operation_id,
                "succeeded",
                safety_snapshot_id=safety_snapshot_id,
            )
            self._delete_migration_plan(migration_plan_id)
            shutil.rmtree(str(record["bundle_path"]), ignore_errors=True)
            return {
                "protocol_version": PROTOCOL_VERSION,
                "ok": True,
                "operation_id": operation_id,
                "migration_plan_id": migration_plan_id,
                "source_archive_id": record["source_archive_id"],
                "source_snapshot_id": record["source_snapshot_id"],
                "target_device_id": record["target_device_id"],
                "target_archive_id": record["target_archive_id"],
                "backup": _jsonable(snapshot),
                "result": _jsonable(result),
                "metadata_limitations": bundle["limitations"],
            }
        except MigrationBundleError as exc:
            error = IPodServiceError(exc.code, str(exc), details=exc.details)
            self._finish_migration_failure(
                operation_id,
                error,
                record,
                safety_snapshot_id,
            )
            raise error from exc
        except IPodServiceError as exc:
            self._finish_migration_failure(
                operation_id,
                exc,
                record,
                safety_snapshot_id,
            )
            raise
        except Exception as exc:
            self._finish_migration_failure(
                operation_id,
                exc,
                record,
                safety_snapshot_id,
            )
            raise
        finally:
            self._clear_active_operation(operation_id)
            self._mutation_lock.release()

    def create_plan(
        self,
        mount_path: str,
        source_files: Iterable[str | Path],
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        staging_id: str = "",
    ) -> dict[str, Any]:
        device = self.adapter.identify_read_only(mount_path)
        self._require_supported_identity(device)
        files = self._validate_source_files(source_files)
        if staging_id:
            staging = self._read_staging(staging_id)
            if staging.get("device_id") != self._stable_device_id(device):
                raise IPodServiceError("staging_device_changed", "The staged download set belongs to a different iPod.")
            if staging.get("mount_path") != _canonical_path(device.path):
                raise IPodServiceError("staging_device_changed", "The staged iPod mount path changed.")
            if tuple(staging.get("completed_files") or ()) != files:
                raise IPodServiceError("staging_changed", "The staged download set changed; stage it again.")
            if staging.get("source_fingerprint") != self._source_fingerprint(files):
                raise IPodServiceError("staging_changed", "A staged download changed; stage it again.")
        generation, tracks, playlists = self._load_for_plan(device)
        callback = self._progress_adapter("plan", progress)
        plan = self.adapter.compute_plan(device, files, tracks, playlists, callback, cancelled)
        details = self._plan_details(plan)
        summary = self._plan_summary(plan, files, details, device)
        estimated = int(summary["required_bytes"])
        free_bytes = int(float(getattr(device, "free_space_gb", 0) or 0) * 1_000_000_000)
        if estimated > free_bytes:
            raise IPodServiceError("insufficient_space", "The reviewed additions exceed the iPod's free space.")
        profile = self._inspect_write_readiness(device)
        volume_key = self.adapter.volume_key(profile)
        plan_id = secrets.token_urlsafe(24)
        record = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "created_at": self.clock(),
            "expires_at": self.clock() + self.plan_ttl_seconds,
            "plan_id": plan_id,
            "mount_path": _canonical_path(device.path),
            "device_id": self._stable_device_id(device),
            "volume_identity_key": volume_key,
            "database_generation": _generation_dict(generation),
            "source_files": list(files),
            "source_fingerprint": self._source_fingerprint(files),
            "required_bytes": estimated,
            "staging_id": staging_id,
            "summary": summary,
            "details": details,
        }
        self._write_plan(record)
        return {
            "protocol_version": PROTOCOL_VERSION,
            "plan_id": plan_id,
            **summary,
            "groups": self._detail_group_descriptors(details),
            "group_previews": {
                group: items[:PLAN_PREVIEW_SIZE]
                for group, items in details.items()
                if items
            },
            "expires_at": record["expires_at"],
        }

    def plan_details(
        self,
        plan_id: str,
        group: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Return one bounded page from the immutable reviewed-plan snapshot."""
        record = self._read_plan(plan_id)
        if self.clock() > float(record["expires_at"]):
            raise IPodServiceError("stale_plan", "The reviewed sync plan expired; review a new plan.")
        details = record.get("details")
        if not isinstance(details, dict) or group not in details:
            raise IPodServiceError("invalid_plan_group", f"Unknown reviewed-plan group: {group}")
        items = details[group]
        if not isinstance(items, list):
            raise IPodServiceError("invalid_plan", "The reviewed sync plan details are invalid.")
        page = max(1, int(page))
        page_size = max(1, min(MAX_PLAN_DETAIL_PAGE_SIZE, int(page_size)))
        start = (page - 1) * page_size
        return {
            "protocol_version": PROTOCOL_VERSION,
            "plan_id": plan_id,
            "group": group,
            "page": page,
            "page_size": page_size,
            "total": len(items),
            "items": items[start:start + page_size],
            "expires_at": record["expires_at"],
        }

    def execute(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if not confirmed:
            raise IPodServiceError("confirmation_required", "Confirm the reviewed plan before syncing.")
        if not self._mutation_lock.acquire(blocking=False):
            raise IPodServiceError("mutation_busy", "Another iPod mutation is already running.")
        operation_id = ""
        try:
            record = self._read_plan(plan_id)
            device, generation, profile = self._revalidate_record(record)
            operation_id = self._begin_operation(
                "sync",
                phase="revalidating",
                can_cancel=True,
                target_id=str(record["device_id"]),
                target_archive_id=self._backup_archive_id(device),
                metadata={"plan_id": plan_id},
            )
            combined_cancel = self._combined_cancel(operation_id, cancelled)
            files = tuple(record["source_files"])
            current_generation, tracks, playlists = self._load_for_plan(device)
            if current_generation != generation:
                raise IPodServiceError("stale_plan", "The iPod database changed after this plan was reviewed.")
            plan = self.adapter.compute_plan(
                device, files, tracks, playlists,
                self._operation_progress_adapter(
                    "revalidate",
                    progress,
                    operation_id,
                ),
                combined_cancel,
            )
            details = self._plan_details(plan)
            summary = self._plan_summary(plan, files, details, device)
            if summary != record["summary"] or details != record.get("details"):
                raise IPodServiceError("stale_plan", "The sync result changed after review; create a new plan.")
            self._transition_operation(
                operation_id,
                "backing_up",
                can_cancel=True,
            )
            self._emit(
                progress,
                "backup",
                0,
                1,
                "Creating mandatory pre-sync backup",
                operation_id=operation_id,
                operation_kind="sync",
                phase="backing_up",
                can_cancel=True,
            )
            snapshot = self.adapter.create_backup(
                device, str(self.backup_dir), record["volume_identity_key"],
                self._operation_progress_adapter(
                    "backup",
                    progress,
                    operation_id,
                ),
                combined_cancel,
            )
            if combined_cancel():
                self._finish_operation(
                    operation_id,
                    "cancelled",
                    phase="cancelled",
                )
                raise IPodServiceError(
                    "cancelled",
                    "The iPod operation was cancelled before writing.",
                    details={"operation_id": operation_id},
                )
            if snapshot is None:
                raise IPodServiceError("backup_failed", "A verified pre-sync backup was not created.")
            safety_snapshot_id = self._snapshot_id_from_value(snapshot)
            self._transition_operation(
                operation_id,
                "revalidating",
                can_cancel=True,
                safety_snapshot_id=safety_snapshot_id,
            )
            # Revalidate a second time after the potentially long backup.
            device, generation_after_backup, profile = self._revalidate_record(record)
            if generation_after_backup != generation:
                raise IPodServiceError("stale_plan", "The iPod database changed during backup.")
            result = self.adapter.execute_plan(
                device, plan, generation, profile,
                self._operation_progress_adapter(
                    "execute",
                    progress,
                    operation_id,
                ),
                combined_cancel,
            )
            self._delete_plan(plan_id)
            self._finish_operation(
                operation_id,
                "succeeded",
                safety_snapshot_id=safety_snapshot_id,
            )
            return {
                "protocol_version": PROTOCOL_VERSION,
                "ok": True,
                "operation_id": operation_id,
                "backup": _jsonable(snapshot),
                "result": _jsonable(result),
            }
        except IPodServiceError as exc:
            if operation_id:
                try:
                    current = self.operation_journal.get(operation_id)
                except OperationJournalError:
                    current = {}
                if current.get("status") == "running":
                    self._finish_failed_operation(operation_id, exc)
            raise
        except Exception as exc:
            self._finish_failed_operation(operation_id, exc)
            raise
        finally:
            self._clear_active_operation(operation_id)
            self._mutation_lock.release()

    def backup(
        self,
        plan_id: str,
        *,
        progress: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Create a verified reviewed-plan backup without applying the plan."""
        if not self._mutation_lock.acquire(blocking=False):
            raise IPodServiceError("mutation_busy", "Another iPod mutation is already running.")
        operation_id = ""
        try:
            record = self._read_plan(plan_id)
            device, _generation, _profile = self._revalidate_record(record)
            operation_id = self._begin_operation(
                "backup",
                phase="backing_up",
                can_cancel=True,
                target_id=str(record["device_id"]),
                target_archive_id=self._backup_archive_id(device),
                metadata={"plan_id": plan_id},
            )
            combined_cancel = self._combined_cancel(operation_id, cancelled)
            snapshot = self.adapter.create_backup(
                device,
                str(self.backup_dir),
                record["volume_identity_key"],
                self._operation_progress_adapter(
                    "backup",
                    progress,
                    operation_id,
                ),
                combined_cancel,
            )
            if snapshot is None:
                if combined_cancel():
                    self._finish_operation(
                        operation_id,
                        "cancelled",
                        phase="cancelled",
                    )
                    raise IPodServiceError(
                        "cancelled",
                        "The iPod backup was cancelled.",
                        details={"operation_id": operation_id},
                    )
                raise IPodServiceError("backup_failed", "A verified iPod backup was not created.")
            self._finish_operation(
                operation_id,
                "succeeded",
                safety_snapshot_id=self._snapshot_id_from_value(snapshot),
            )
            return {
                "protocol_version": PROTOCOL_VERSION,
                "ok": True,
                "operation_id": operation_id,
                "backup": _jsonable(snapshot),
            }
        except IPodServiceError as exc:
            if operation_id:
                try:
                    current = self.operation_journal.get(operation_id)
                except OperationJournalError:
                    current = {}
                if current.get("status") == "running":
                    self._finish_failed_operation(operation_id, exc)
            raise
        except Exception as exc:
            self._finish_failed_operation(operation_id, exc)
            raise
        finally:
            self._clear_active_operation(operation_id)
            self._mutation_lock.release()

    def cancel(self, operation_id: str = "") -> dict[str, Any]:
        """Request cancellation only while the journaled phase is cancellable."""
        with self._operation_state_lock:
            active_id = self._active_operation_id
            local_record = {
                "operation_id": active_id,
                "kind": self._active_operation_kind,
                "phase": self._active_operation_phase,
                "can_cancel": self._active_operation_can_cancel,
                "status": "running",
            } if active_id else None
        record: Mapping[str, Any] | None = local_record
        if record is None or (operation_id and operation_id != active_id):
            try:
                record = (
                    self.operation_journal.get(operation_id)
                    if operation_id
                    else self.operation_journal.latest()
                )
            except OperationJournalError as exc:
                raise IPodServiceError(
                    "operation_not_found",
                    str(exc),
                ) from exc
        if record is None or record.get("status") != "running":
            return {
                "protocol_version": PROTOCOL_VERSION,
                "cancel_requested": False,
                "reason": "no_active_operation",
            }
        selected_id = str(record.get("operation_id", "") or "")
        if operation_id and selected_id != operation_id:
            return {
                "protocol_version": PROTOCOL_VERSION,
                "cancel_requested": False,
                "reason": "operation_not_active",
                "operation_id": selected_id,
            }
        phase = str(record.get("phase", "") or "")
        can_cancel = bool(record.get("can_cancel", False))
        if not can_cancel or phase in {"committing", "finalizing"}:
            return {
                "protocol_version": PROTOCOL_VERSION,
                "cancel_requested": False,
                "reason": "commit_in_progress",
                "operation_id": selected_id,
                "phase": phase,
                "can_cancel": False,
            }
        if selected_id == active_id:
            self._cancel.set()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "cancel_requested": True,
            "operation_id": selected_id,
            "phase": phase,
            "can_cancel": True,
        }

    def eject(self, mount_path: str) -> dict[str, Any]:
        if self._mutation_lock.locked():
            raise IPodServiceError("mutation_busy", "Wait for backup or sync to finish before ejecting.")
        device = self.adapter.identify_read_only(mount_path)
        self._require_supported_identity(device, require_writable=False)
        ok, message = self.adapter.eject(device)
        if not ok:
            raise IPodServiceError("eject_failed", message)
        return {"protocol_version": PROTOCOL_VERSION, "ok": True, "message": message}

    def create_staging_contract(
        self,
        mount_path: str,
        completed_files: Iterable[str | Path],
        library_root: str | Path,
    ) -> dict[str, Any]:
        """Record provider outputs locally; this never writes iPod_Control."""
        device = self.adapter.identify_read_only(mount_path)
        self._require_supported_identity(device, require_writable=False)
        files = self._validate_library_completed_files(completed_files, library_root)
        staging_id = secrets.token_urlsafe(18)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "staging_id": staging_id,
            "device_id": self._stable_device_id(device, allow_incomplete=True),
            "mount_path": _canonical_path(device.path),
            "library_root": _canonical_path(library_root),
            "completed_files": list(files),
            "source_fingerprint": self._source_fingerprint(files),
            "created_at": self.clock(),
        }
        self._atomic_json(self.staging_dir / f"{staging_id}.json", payload)
        return {"protocol_version": PROTOCOL_VERSION, **payload}

    def _capacity_unlock_evidence(self, device: Any) -> UnlockDeviceEvidence:
        sources = getattr(device, "_field_sources", {})
        if not isinstance(sources, Mapping):
            sources = {}

        def source_is_stable(field: str) -> bool:
            source = str(sources.get(field, "") or "").strip().casefold()
            blocked = (
                "unknown",
                "inferred",
                "serial_suffix",
                "model_table",
                "disk_size",
            )
            return bool(source) and not any(item in source for item in blocked)

        profile = None
        try:
            profile = self.adapter.inspect_write_readiness(device)
        except Exception:
            profile = None
        writable = bool(profile is not None and getattr(profile, "safe_for_writes", False))
        filesystem = (
            str(getattr(profile, "filesystem_type", "") or "") if profile else ""
        )
        writable_evidence = ""
        if writable:
            profile_digest = hashlib.sha256(
                json.dumps(
                    _bounded_json(profile, max_items=50, max_string=200),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            writable_evidence = f"filesystem-profile:{profile_digest}"

        storage_healthy = False
        health_evidence = ""
        try:
            before = _generation_dict(
                self.adapter.capture_database_generation(device.path)
            )
            after = _generation_dict(
                self.adapter.capture_database_generation(device.path)
            )
            digest = str(after.get("digest", "") or "").casefold()
            storage_healthy = bool(
                before == after
                and after.get("exists") is True
                and re.fullmatch(r"[0-9a-f]{64}", digest)
            )
            if storage_healthy:
                health_evidence = f"stable-database-generation:{digest}"
        except Exception:
            storage_healthy = False

        raw_conflicts = getattr(device, "identity_conflicts", ()) or ()
        identity_conflicts = tuple(
            f"conflict_{index}"
            for index, conflict in enumerate(raw_conflicts)
            if conflict
        )
        operating_system = str(
            getattr(getattr(profile, "identity", None), "operating_system", "")
            or ""
        ).casefold()
        return UnlockDeviceEvidence(
            platform=sys.platform,
            model_family=str(getattr(device, "model_family", "") or ""),
            generation=str(getattr(device, "generation", "") or ""),
            model_number=(
                str(getattr(device, "model_number", "") or "")
                if source_is_stable("model_number")
                else ""
            ),
            firmware_version=(
                str(getattr(device, "firmware", "") or "")
                if source_is_stable("firmware")
                else ""
            ),
            filesystem=filesystem,
            serial_number=str(getattr(device, "serial", "") or ""),
            firewire_guid=str(getattr(device, "firewire_guid", "") or ""),
            serial_is_stable=source_is_stable("serial"),
            firewire_is_stable=source_is_stable("firewire_guid"),
            identity_conflicts=identity_conflicts,
            writable=writable,
            writable_evidence=writable_evidence,
            storage_healthy=storage_healthy,
            health_evidence=health_evidence,
            usb_vendor_id=(
                int(getattr(device, "usb_vid", 0) or 0)
                if source_is_stable("usb_vid")
                else 0
            ),
            usb_product_id=(
                int(getattr(device, "usb_pid", 0) or 0)
                if source_is_stable("usb_pid")
                else 0
            ),
            is_virtual=operating_system == "virtual",
            active_device_mutation=self._mutation_lock.locked(),
        )

    @staticmethod
    def _unlock_acknowledgements(
        value: Mapping[str, Any],
    ) -> UnlockAcknowledgements:
        if not isinstance(value, Mapping):
            raise IPodServiceError(
                "invalid_acknowledgements",
                "Every capacity-unlock acknowledgement must be supplied.",
            )
        if set(value) != set(UNLOCK_ACKNOWLEDGEMENT_FIELDS) or any(
            value.get(field) is not True for field in UNLOCK_ACKNOWLEDGEMENT_FIELDS
        ):
            raise IPodServiceError(
                "acknowledgements_incomplete",
                "Every destructive-workflow acknowledgement must be explicitly true.",
            )
        return UnlockAcknowledgements(
            **{field: True for field in UNLOCK_ACKNOWLEDGEMENT_FIELDS}
        )

    @staticmethod
    def _unlock_expected_revision(payload: Mapping[str, Any]) -> int:
        value = payload.get("expected_revision")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise IPodServiceError(
                "missing_expected_revision",
                "Supply the current capacity-unlock session revision.",
            )
        return value

    def _unlock_backup(
        self,
        session: Any,
        payload: Mapping[str, Any],
        expected_revision: int,
        progress: Callable[[dict[str, Any]], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> Any:
        mount_path = str(payload.get("mount_path", "") or "")
        device = self.adapter.identify_read_only(mount_path)
        evidence = self._capacity_unlock_evidence(device)
        eligibility = evaluate_capacity_unlock_eligibility(evidence)
        if (
            not eligibility.eligible
            or eligibility.identity_fingerprint != session.identity_fingerprint
        ):
            raise IPodServiceError(
                "unlock_device_changed",
                "The connected iPod no longer matches the eligible unlock device.",
            )
        result = self.manual_backup(
            mount_path,
            progress=progress,
            cancelled=cancelled,
        )
        snapshot = result.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise IPodServiceError(
                "unlock_backup_missing",
                "Capacity unlock requires a newly created Vela backup snapshot.",
            )
        archive_id = self._validate_archive_id(str(result.get("archive_id", "")))
        snapshot_id = self._validate_snapshot_id(
            str(snapshot.get("snapshot_id", ""))
        )
        verification = self.verify_backup_snapshot(
            archive_id,
            snapshot_id,
            progress=progress,
            cancelled=cancelled,
        )
        if verification.get("ok") is not True:
            raise IPodServiceError(
                "unlock_backup_not_verified",
                "The capacity-unlock backup did not pass deep SHA-256 verification.",
            )
        _manifest, _entries, fingerprint = self._load_backup_manifest(
            archive_id, snapshot_id
        )
        reference = (
            "unlock-backup:"
            + hashlib.sha256(
                f"{archive_id}\x00{snapshot_id}".encode("utf-8")
            ).hexdigest()[:32]
        )
        return self.capacity_unlock_machine.record_filesystem_backup(
            session.session_id,
            backup_reference=reference,
            verification_sha256=fingerprint,
            verified=True,
            expected_revision=expected_revision,
        )

    def _unlock_artifacts(
        self,
        payload: Mapping[str, Any],
        progress: Callable[[dict[str, Any]], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> list[Any]:
        requested = payload.get("artifacts")
        if not isinstance(requested, Mapping) or set(requested) != set(
            REQUIRED_UNLOCK_ARTIFACT_IDS
        ):
            raise IPodServiceError(
                "artifact_set_incomplete",
                "Every pinned capacity-unlock artifact must be selected.",
            )
        self.capacity_unlock_artifact_dir.mkdir(parents=True, exist_ok=True)
        receipts = []

        def on_progress(event: Any) -> None:
            self._emit(
                progress,
                f"capacity-unlock:{event.stage}",
                int(event.current_bytes),
                int(event.total_bytes),
                f"{event.artifact_id}: {event.stage}",
            )

        for artifact_id in sorted(REQUIRED_UNLOCK_ARTIFACT_IDS):
            choice = requested[artifact_id]
            if not isinstance(choice, Mapping):
                raise IPodServiceError(
                    "invalid_artifact_action",
                    "Each artifact requires a validate or download action.",
                )
            spec = PINNED_UNLOCK_ARTIFACTS[artifact_id]
            mode = str(choice.get("mode", "") or "").strip().lower()
            if mode == "download":
                destination = self.capacity_unlock_artifact_dir / spec.filename
                receipt = self.artifact_downloader.download(
                    spec,
                    destination,
                    explicit_user_action=True,
                    progress=on_progress,
                    cancelled=cancelled,
                )
            elif mode == "validate":
                path = self._unlock_selected_file(
                    choice.get("path"),
                    payload,
                    "",
                )
                receipt = validate_artifact_file(
                    path,
                    spec,
                    progress=on_progress,
                    cancelled=cancelled,
                )
            else:
                raise IPodServiceError(
                    "invalid_artifact_action",
                    "Each artifact action must be validate or download.",
                )
            receipts.append(receipt)
        return receipts

    def _unlock_selected_roots(
        self,
        payload: Mapping[str, Any],
        session_id: str,
    ) -> tuple[Path, ...]:
        raw_roots = payload.get("selected_directories", [])
        if not isinstance(raw_roots, list) or len(raw_roots) > 8:
            raise IPodServiceError(
                "invalid_selected_directories",
                "Selected capacity-unlock directories are invalid.",
            )
        if payload.get("selected_directory"):
            raw_roots = [*raw_roots, payload.get("selected_directory")]
        roots = [self.capacity_unlock_artifact_dir]
        if session_id:
            roots.append(self.capacity_unlock_session_dir / session_id)
        for raw_root in raw_roots:
            root = Path(str(raw_root or ""))
            try:
                status = root.lstat()
            except OSError as exc:
                raise IPodServiceError(
                    "selected_directory_unavailable",
                    "A selected capacity-unlock directory is unavailable.",
                ) from exc
            if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
                raise IPodServiceError(
                    "invalid_selected_directory",
                    "Selected capacity-unlock roots must be real directories.",
                )
            roots.append(root)
        resolved: list[Path] = []
        for root in roots:
            if root.is_dir() and not root.is_symlink():
                resolved.append(root.resolve(strict=True))
        return tuple(resolved)

    def _unlock_selected_file(
        self,
        raw_path: Any,
        payload: Mapping[str, Any],
        session_id: str,
    ) -> Path:
        path = Path(str(raw_path or ""))
        try:
            status = path.lstat()
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise IPodServiceError(
                "unlock_file_unavailable",
                "A selected capacity-unlock file is unavailable.",
            ) from exc
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
            raise IPodServiceError(
                "invalid_unlock_file",
                "Capacity-unlock inputs must be regular, non-symlink files.",
            )
        roots = self._unlock_selected_roots(payload, session_id)
        if not any(self._path_is_within(root, resolved) for root in roots):
            raise IPodServiceError(
                "unlock_path_outside_selection",
                "The capacity-unlock file is outside the explicit selected directories.",
            )
        return resolved

    def _unlock_read_selected_file(
        self,
        raw_path: Any,
        payload: Mapping[str, Any],
        session_id: str,
    ) -> bytes:
        path = self._unlock_selected_file(raw_path, payload, session_id)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise IPodServiceError(
                "unlock_file_too_large",
                "The selected SysCfg input exceeds the safe size bound.",
            )
        return path.read_bytes()

    @staticmethod
    def _path_is_within(root: Path, path: Path) -> bool:
        try:
            return os.path.commonpath((str(root), str(path))) == str(root)
        except ValueError:
            return False

    def _unlock_postflight(
        self,
        session: Any,
        payload: Mapping[str, Any],
        expected_revision: int,
    ) -> Any:
        device = self.adapter.identify_read_only(
            str(payload.get("mount_path", "") or "")
        )
        base = self._capacity_unlock_evidence(device)
        postflight = PostflightEvidence(
            firmware_version=str(getattr(device, "firmware", "") or ""),
            model_number=str(getattr(device, "model_number", "") or ""),
            filesystem=base.filesystem,
            firewire_guid=str(getattr(device, "firewire_guid", "") or ""),
            identity_conflicts=base.identity_conflicts,
            writable=base.writable,
            writable_evidence=base.writable_evidence,
            storage_healthy=base.storage_healthy,
            health_evidence=base.health_evidence,
        )
        return self.capacity_unlock_machine.record_postflight(
            session.session_id,
            postflight,
            expected_revision=expected_revision,
        )

    @staticmethod
    def _unlock_result(**values: Any) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "experimental": True,
            **values,
        }

    def _device_summary(self, device: Any) -> dict[str, Any]:
        family = str(getattr(device, "model_family", "") or "iPod")
        stable = self._stable_device_id(device, allow_incomplete=True)
        filesystem_accessible = bool(
            getattr(device, "filesystem_accessible", True)
        )
        complete = bool(
            getattr(device, "serial", "")
            and getattr(device, "firewire_guid", "")
        )
        supported = family.casefold() in SUPPORTED_FAMILIES
        write_ready = False
        filesystem_read_only = bool(
            getattr(device, "raw_read_only", False)
            or getattr(device, "filesystem_read_only", False)
        )
        write_block_code = ""
        reason = ""
        if not filesystem_accessible:
            write_block_code = "filesystem_unavailable"
            reason = str(
                getattr(device, "access_message", "")
                or "The attached iPod filesystem is not mounted."
            )
        elif not supported:
            write_block_code = "unsupported_device"
            reason = "iPod Touch and Shuffle are not supported for Vela sync."
        elif not complete:
            write_block_code = "incomplete_volume_identity"
            reason = "Device identity is incomplete; preparation is required before writes."
        else:
            try:
                profile = self._inspect_write_readiness(device)
                filesystem_read_only = bool(getattr(profile, "read_only", False))
                write_ready = bool(getattr(profile, "safe_for_writes", False))
                if not write_ready:
                    write_block_code = "write_safety_failed"
                    reason = "The mounted iPod did not pass write-safety inspection."
            except IPodServiceError as exc:
                write_block_code = exc.code
                reason = str(exc)
                filesystem_read_only = (
                    filesystem_read_only or exc.code == "volume_read_only"
                )
        browse_only = not (supported and write_ready)
        return {
            "device_id": stable,
            "path": str(device.path),
            "name": str(getattr(device, "ipod_name", "") or getattr(device, "display_name", "") or "iPod"),
            "model_family": family,
            "generation": str(getattr(device, "generation", "") or ""),
            "model_number": str(getattr(device, "model_number", "") or ""),
            "capacity": str(getattr(device, "capacity", "") or ""),
            "serial": str(getattr(device, "serial", "") or ""),
            "firewire_guid": str(getattr(device, "firewire_guid", "") or ""),
            "firmware": str(getattr(device, "firmware", "") or ""),
            "filesystem_type": str(getattr(device, "filesystem_type", "") or ""),
            "volume_identity_key": str(getattr(device, "volume_identity_key", "") or ""),
            "disk_size_gb": float(getattr(device, "disk_size_gb", 0) or 0),
            "free_space_gb": float(getattr(device, "free_space_gb", 0) or 0),
            "uses_sqlite_db": bool(getattr(device, "uses_sqlite_db", False)),
            "checksum_type": int(getattr(device, "checksum_type", 99)),
            "audio_codecs": _jsonable(getattr(device, "audio_codecs", {}) or {}),
            "podcasts_supported": bool(getattr(device, "podcasts_supported", False)),
            "voice_memos_supported": bool(getattr(device, "voice_memos_supported", False)),
            "supports_sparse_artwork": bool(getattr(device, "supports_sparse_artwork", False)),
            "browse_only": browse_only,
            "needs_preparation": (
                not filesystem_accessible
                or complete is False
                or (supported and not write_ready)
            ),
            "write_ready": write_ready,
            "filesystem_read_only": filesystem_read_only,
            "write_block_code": write_block_code,
            "write_block_reason": reason,
            "filesystem_accessible": filesystem_accessible,
            "raw_read_only": bool(getattr(device, "raw_read_only", False)),
            "access_state": str(
                getattr(device, "access_state", "mounted") or "mounted"
            ),
            "access_message": str(
                getattr(device, "access_message", "") or ""
            ),
            "raw_device_path": str(
                getattr(device, "raw_device_path", "") or ""
            ),
        }

    def _stable_device_id(self, device: Any, allow_incomplete: bool = False) -> str:
        serial = str(getattr(device, "serial", "") or "").strip()
        guid = str(getattr(device, "firewire_guid", "") or "").strip()
        raw_volume_fingerprint = str(
            getattr(device, "raw_volume_fingerprint", "") or ""
        ).strip()
        if not allow_incomplete and not (serial and guid):
            raise IPodServiceError(
                "incomplete_volume_identity",
                "Both the iPod serial and FireWire identity are required; writes are disabled.",
            )
        if serial and guid:
            material = f"{serial.casefold()}|{guid.casefold()}"
        elif raw_volume_fingerprint:
            # This is only a stable UI/watch identity for an inaccessible
            # read-only volume. It can never satisfy write identity checks.
            material = f"raw-read-only|{raw_volume_fingerprint.casefold()}"
        else:
            # Browse-only identities intentionally have session/path scope. They
            # must never authorize a write or merge a durable backup archive.
            material = (
                f"browse-only|{serial.casefold()}|{guid.casefold()}|"
                f"{_discovery_path_key(device.path)}"
            )
        return hashlib.sha256(
            material.encode("utf-8", errors="surrogatepass")
        ).hexdigest()[:32]

    def _backup_archive_id(self, device: Any) -> str:
        resolver = getattr(self.adapter, "backup_archive_id", None)
        if callable(resolver):
            archive_id = str(resolver(device) or "")
        else:
            raw = str(
                getattr(device, "serial", "")
                or getattr(device, "firewire_guid", "")
                or ""
            ).strip()
            archive_id = "".join(
                char if char.isalnum() or char in "-_" else "_"
                for char in raw
            )
        return self._validate_archive_id(archive_id)

    def _backup_device_meta(self, device: Any) -> dict[str, Any]:
        resolver = getattr(self.adapter, "_backup_device_meta", None)
        if callable(resolver):
            value = resolver(device)
            if isinstance(value, Mapping):
                return dict(value)
        return {
            "model_family": str(getattr(device, "model_family", "") or "")[:100],
            "generation": str(getattr(device, "generation", "") or "")[:100],
            "model_number": str(getattr(device, "model_number", "") or "")[:100],
            "firmware": str(getattr(device, "firmware", "") or "")[:100],
            "filesystem_type": str(
                getattr(device, "filesystem_type", "") or ""
            )[:100],
            "reported_volume_format": str(
                getattr(device, "reported_volume_format", "") or ""
            )[:100],
            "capacity": str(getattr(device, "capacity", "") or "")[:100],
            "disk_size_gb": float(getattr(device, "disk_size_gb", 0) or 0),
            "free_space_gb": float(getattr(device, "free_space_gb", 0) or 0),
            "uses_sqlite_db": bool(getattr(device, "uses_sqlite_db", False)),
            "db_version": int(getattr(device, "db_version", 0) or 0),
            "shadow_db_version": int(
                getattr(device, "shadow_db_version", 0) or 0
            ),
            "hashing_scheme": int(getattr(device, "hashing_scheme", -1) or -1),
            "checksum_type": int(getattr(device, "checksum_type", 99) or 99),
            "audio_codecs": _bounded_json(
                getattr(device, "audio_codecs", {}) or {},
                max_items=30,
                max_string=100,
            ),
            "podcasts_supported": bool(
                getattr(device, "podcasts_supported", False)
            ),
            "voice_memos_supported": bool(
                getattr(device, "voice_memos_supported", False)
            ),
            "supports_sparse_artwork": bool(
                getattr(device, "supports_sparse_artwork", False)
            ),
            "photos_supported": bool(getattr(device, "photos_supported", False)),
            "videos_supported": bool(getattr(device, "videos_supported", False)),
            "stable_device_id": self._stable_device_id(device),
        }

    def _require_supported_identity(self, device: Any, require_writable: bool = True) -> None:
        family = str(getattr(device, "model_family", "") or "").casefold()
        if "touch" in family or "shuffle" in family or family not in SUPPORTED_FAMILIES:
            raise IPodServiceError("unsupported_device", "Vela sync supports iPod Classic, Mini, and Nano only.")
        if require_writable:
            self._stable_device_id(device)
            self._inspect_write_readiness(device)
        else:
            self._stable_device_id(device, allow_incomplete=True)

    @staticmethod
    def _validate_archive_id(archive_id: str) -> str:
        if not isinstance(archive_id, str) or not ARCHIVE_ID_RE.fullmatch(archive_id):
            raise IPodServiceError(
                "invalid_archive_id",
                "The backup archive ID is invalid.",
            )
        return archive_id

    @staticmethod
    def _validate_snapshot_id(snapshot_id: str) -> str:
        if not isinstance(snapshot_id, str) or not SNAPSHOT_ID_RE.fullmatch(snapshot_id):
            raise IPodServiceError(
                "invalid_snapshot_id",
                "The backup snapshot ID is invalid.",
            )
        return snapshot_id

    @staticmethod
    def _snapshot_field(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(name, default)
        return getattr(value, name, default)

    @classmethod
    def _snapshot_id_from_value(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(
            cls._snapshot_field(
                value,
                "id",
                cls._snapshot_field(value, "snapshot_id", ""),
            )
            or ""
        )[:128]

    @classmethod
    def _snapshot_dto(cls, snapshot: Any) -> dict[str, Any]:
        snapshot_id = cls._snapshot_id_from_value(snapshot)
        return {
            "snapshot_id": snapshot_id,
            "timestamp": str(
                cls._snapshot_field(snapshot, "timestamp", "") or ""
            )[:100],
            "archive_id": str(
                cls._snapshot_field(
                    snapshot,
                    "device_id",
                    cls._snapshot_field(snapshot, "archive_id", ""),
                )
                or ""
            )[:128],
            "device_name": str(
                cls._snapshot_field(snapshot, "device_name", "iPod") or "iPod"
            )[:300],
            "file_count": max(
                0,
                int(cls._snapshot_field(snapshot, "file_count", 0) or 0),
            ),
            "total_size_bytes": max(
                0,
                int(
                    cls._snapshot_field(
                        snapshot,
                        "total_size",
                        cls._snapshot_field(snapshot, "total_size_bytes", 0),
                    )
                    or 0
                ),
            ),
            "reason": str(
                cls._snapshot_field(snapshot, "reason", "manual") or "manual"
            )[:100],
            "note": str(
                cls._snapshot_field(snapshot, "note", "") or ""
            )[:MAX_SNAPSHOT_NOTE_LENGTH],
            "files_added": max(
                0,
                int(cls._snapshot_field(snapshot, "files_added", 0) or 0),
            ),
            "files_removed": max(
                0,
                int(cls._snapshot_field(snapshot, "files_removed", 0) or 0),
            ),
            "files_changed": max(
                0,
                int(cls._snapshot_field(snapshot, "files_changed", 0) or 0),
            ),
            "device_meta": _bounded_json(
                cls._snapshot_field(snapshot, "device_meta", {}) or {},
                max_items=30,
                max_string=500,
            ),
            "is_valid": bool(
                cls._snapshot_field(snapshot, "is_valid", True)
            ),
            "validation_error": str(
                cls._snapshot_field(snapshot, "validation_error", "") or ""
            )[:1_000],
        }

    @staticmethod
    def _manifest_snapshot_dto(
        manifest: Mapping[str, Any],
        fingerprint: str,
    ) -> dict[str, Any]:
        return {
            "snapshot_id": str(manifest.get("id", "") or "")[:128],
            "timestamp": str(manifest.get("timestamp", "") or "")[:100],
            "archive_id": str(manifest.get("device_id", "") or "")[:128],
            "device_name": str(
                manifest.get("device_name", "") or "iPod"
            )[:300],
            "file_count": max(0, int(manifest.get("file_count", 0) or 0)),
            "total_size_bytes": max(
                0,
                int(manifest.get("total_size", 0) or 0),
            ),
            "reason": str(manifest.get("reason", "manual") or "manual")[:100],
            "note": str(
                manifest.get("note", "") or ""
            )[:MAX_SNAPSHOT_NOTE_LENGTH],
            "identity_is_stable": bool(
                manifest.get("identity_is_stable", False)
            ),
            "source_verification": str(
                manifest.get("source_verification", "") or ""
            )[:100],
            "manifest_version": int(manifest.get("version", 0) or 0),
            "snapshot_fingerprint": fingerprint[:64],
            "device_meta": _bounded_json(
                manifest.get("device_meta", {}),
                max_items=30,
                max_string=500,
            ),
        }

    @staticmethod
    def _backup_scope(manifest: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "kind": "full_regular_file_tree",
            "functional_backup": True,
            "raw_disk_image": False,
            "included_file_count": max(
                0,
                int(manifest.get("file_count", 0) or 0),
            ),
            "included_bytes": max(
                0,
                int(manifest.get("total_size", 0) or 0),
            ),
            "content_verification": str(
                manifest.get("source_verification", "") or "full_sha256"
            )[:100],
            "restores_included_tree_exactly": True,
        }

    @staticmethod
    def _backup_exclusions() -> list[dict[str, str]]:
        return [
            {
                "category": "host_metadata",
                "description": (
                    "Permissions, ACLs, extended attributes, resource forks, "
                    "and sparse allocation are not captured."
                ),
            },
            {
                "category": "filesystem_structure",
                "description": (
                    "Empty directories, partition state, and firmware are not captured."
                ),
            },
            {
                "category": "operating_system_files",
                "description": (
                    "Known host-managed recycle, indexing, metadata, and AppleDouble "
                    "entries are excluded."
                ),
            },
            {
                "category": "links",
                "description": "Symbolic links are never followed or archived.",
            },
        ]

    def _load_backup_manifest(
        self,
        archive_id: str,
        snapshot_id: str,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
        try:
            result = self.adapter.load_backup_manifest(
                archive_id,
                snapshot_id,
                str(self.backup_dir),
            )
        except Exception as exc:
            self._raise_backup_error("backup_snapshot_unavailable", exc)
        if (
            not isinstance(result, tuple)
            or len(result) != 3
            or not isinstance(result[0], Mapping)
            or not isinstance(result[1], Mapping)
        ):
            raise IPodServiceError(
                "invalid_backup_snapshot",
                "The backup snapshot metadata is invalid.",
            )
        manifest = dict(result[0])
        entries = {
            str(path): dict(info)
            for path, info in result[1].items()
            if isinstance(path, str) and isinstance(info, Mapping)
        }
        fingerprint = str(result[2] or "").casefold()
        if (
            manifest.get("id") != snapshot_id
            or manifest.get("device_id") != archive_id
            or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        ):
            raise IPodServiceError(
                "invalid_backup_snapshot",
                "The backup snapshot identity or fingerprint is invalid.",
            )
        return manifest, entries, fingerprint

    @staticmethod
    def _snapshot_binding(
        manifest: Mapping[str, Any],
        fingerprint: str,
    ) -> dict[str, Any]:
        return {
            "snapshot_id": str(manifest.get("id", "") or ""),
            "archive_id": str(manifest.get("device_id", "") or ""),
            "timestamp": str(manifest.get("timestamp", "") or ""),
            "sequence": manifest.get("sequence"),
            "manifest_version": int(manifest.get("version", 0) or 0),
            "file_count": int(manifest.get("file_count", 0) or 0),
            "total_size": int(manifest.get("total_size", 0) or 0),
            "identity_is_stable": bool(
                manifest.get("identity_is_stable", False)
            ),
            "source_volume_identity_key": str(
                manifest.get("source_volume_identity_key", "") or ""
            ),
            "device_meta": _bounded_json(
                manifest.get("device_meta", {}),
                max_items=30,
                max_string=500,
            ),
            "fingerprint": fingerprint,
        }

    @staticmethod
    def _raise_backup_error(code: str, exc: Exception) -> None:
        if isinstance(exc, IPodServiceError):
            raise exc
        raise IPodServiceError(
            code,
            str(exc)[:2_000] or "The iPod backup operation failed.",
        ) from exc

    def _write_restore_plan(self, record: Mapping[str, Any]) -> None:
        self.restore_plan_dir.mkdir(parents=True, exist_ok=True)
        self._purge_expired_restore_plans()
        self._atomic_json(
            self.restore_plan_dir / f"{record['restore_plan_id']}.json",
            record,
        )

    def _read_restore_plan(self, restore_plan_id: str) -> dict[str, Any]:
        if (
            not isinstance(restore_plan_id, str)
            or not ARCHIVE_ID_RE.fullmatch(restore_plan_id)
        ):
            raise IPodServiceError(
                "invalid_restore_plan",
                "The reviewed restore plan ID is invalid.",
            )
        try:
            payload = json.loads(
                (
                    self.restore_plan_dir / f"{restore_plan_id}.json"
                ).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise IPodServiceError(
                "restore_plan_not_found",
                "The reviewed restore plan was not found.",
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != RESTORE_PLAN_SCHEMA_VERSION
            or payload.get("restore_plan_id") != restore_plan_id
        ):
            raise IPodServiceError(
                "invalid_restore_plan",
                "The reviewed restore plan is invalid.",
            )
        if self.clock() > float(payload.get("expires_at", 0)):
            raise IPodServiceError(
                "stale_restore_plan",
                "The reviewed restore plan expired; run restore preflight again.",
            )
        self._validate_archive_id(str(payload.get("source_archive_id", "")))
        self._validate_archive_id(str(payload.get("target_archive_id", "")))
        self._validate_snapshot_id(str(payload.get("source_snapshot_id", "")))
        if not isinstance(payload.get("snapshot_binding"), Mapping):
            raise IPodServiceError(
                "invalid_restore_plan",
                "The reviewed restore plan is invalid.",
            )
        return payload

    def _delete_restore_plan(self, restore_plan_id: str) -> None:
        try:
            (
                self.restore_plan_dir / f"{restore_plan_id}.json"
            ).unlink()
        except FileNotFoundError:
            pass

    def _purge_expired_restore_plans(self) -> None:
        for path in self.restore_plan_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if self.clock() > float(payload.get("expires_at", 0)):
                    path.unlink()
            except (OSError, ValueError, TypeError):
                try:
                    path.unlink()
                except OSError:
                    pass

    def _migration_block(
        self,
        code: str,
        message: str,
        *,
        archive_id: str,
        snapshot_id: str,
        fingerprint: str,
        source_meta: Mapping[str, Any],
        target_meta: Mapping[str, Any],
        same_device: bool = False,
        issues: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "blocked": True,
            "compatible": False,
            "code": code,
            "message": message,
            "raw_restore_allowed": False,
            "safe_migration_available": False,
            "same_device": same_device,
            "issues": _bounded_json(
                list(issues),
                max_items=100,
                max_string=1_000,
            ),
            "source": {
                "archive_id": archive_id,
                "snapshot_id": snapshot_id,
                "device_id": str(
                    source_meta.get("stable_device_id", "") or ""
                ),
                "snapshot_fingerprint": fingerprint,
                "model_family": str(
                    source_meta.get("model_family", "") or ""
                )[:100],
                "generation": str(
                    source_meta.get("generation", "") or ""
                )[:100],
                "uses_sqlite_db": bool(
                    source_meta.get("uses_sqlite_db", False)
                ),
            },
            "target": {
                "archive_id": str(
                    target_meta.get("archive_id", "") or ""
                ),
                "device_id": str(
                    target_meta.get("stable_device_id", "") or ""
                ),
                "model_family": str(
                    target_meta.get("model_family", "") or ""
                )[:100],
                "generation": str(
                    target_meta.get("generation", "") or ""
                )[:100],
                "uses_sqlite_db": bool(
                    target_meta.get("uses_sqlite_db", False)
                ),
            },
            "requirements": [
                "The source snapshot remains immutable and is never raw-restored.",
                "The target keeps its own serial, FireWire GUID, volume identity, "
                "and device files.",
                "Only SHA-256 verified media enters an app-data staging bundle.",
                "iOpenPod must produce a reviewed addition-only sync plan.",
                "A verified target safety backup is mandatory before writing.",
            ],
        }

    @staticmethod
    def _migration_compatibility_issues(
        manifest: Mapping[str, Any],
        source_meta: Mapping[str, Any],
        target_meta: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        if not bool(manifest.get("identity_is_stable", False)) or not str(
            source_meta.get("stable_device_id", "") or ""
        ):
            issues.append({
                "field": "source_identity",
                "message": (
                    "The source snapshot lacks stable serial and FireWire identity."
                ),
            })
        required_fields = (
            "model_family",
            "generation",
            "uses_sqlite_db",
            "db_version",
            "checksum_type",
        )
        for field in required_fields:
            if field not in source_meta or field not in target_meta:
                issues.append({
                    "field": field,
                    "source": source_meta.get(field),
                    "target": target_meta.get(field),
                    "message": (
                        "The backup and target must both expose this pinned "
                        "compatibility field."
                    ),
                })
                continue
            source_value = source_meta.get(field)
            target_value = target_meta.get(field)
            if field in {"model_family", "generation"}:
                equal = (
                    str(source_value or "").strip().casefold()
                    == str(target_value or "").strip().casefold()
                    and bool(str(source_value or "").strip())
                )
            elif field == "uses_sqlite_db":
                equal = bool(source_value) == bool(target_value)
            else:
                try:
                    equal = int(source_value) == int(target_value)
                except (TypeError, ValueError):
                    equal = False
            if not equal:
                issues.append({
                    "field": field,
                    "source": source_value,
                    "target": target_value,
                    "message": (
                        "The source and replacement iPod compatibility profiles "
                        "do not match."
                    ),
                })
        for field in ("shadow_db_version", "hashing_scheme"):
            if field in source_meta and field in target_meta:
                try:
                    equal = int(source_meta[field]) == int(target_meta[field])
                except (TypeError, ValueError):
                    equal = False
                if not equal:
                    issues.append({
                        "field": field,
                        "source": source_meta.get(field),
                        "target": target_meta.get(field),
                        "message": (
                            "The source and target database/checksum profiles "
                            "do not match."
                        ),
                    })
        return issues

    @staticmethod
    def _migration_plan_unsafe_counts(
        plan: Any,
        details: Mapping[str, list[dict[str, Any]]],
    ) -> dict[str, int]:
        """Reject target-destructive changes; replacement migration only adds."""

        return {
            "track_removals": len(list(getattr(plan, "to_remove", ()) or ())),
            "metadata_overwrites": len(
                list(getattr(plan, "to_update_metadata", ()) or ())
            ),
            "file_overwrites": len(
                list(getattr(plan, "to_update_file", ()) or ())
            ),
            "artwork_overwrites": len(
                list(getattr(plan, "to_update_artwork", ()) or ())
            ),
            "playlist_edits": len(
                list(getattr(plan, "playlists_to_edit", ()) or ())
            ),
            "playlist_removals": len(
                list(getattr(plan, "playlists_to_remove", ()) or ())
            ),
            "warnings": len(details["warnings"]),
            "unsupported": len(details["unsupported"]),
        }

    def _write_migration_plan(self, record: Mapping[str, Any]) -> None:
        self.migration_plan_dir.mkdir(parents=True, exist_ok=True)
        self._purge_expired_migration_plans()
        self._atomic_json(
            self.migration_plan_dir
            / f"{record['migration_plan_id']}.json",
            record,
        )

    def _read_migration_plan(self, migration_plan_id: str) -> dict[str, Any]:
        if (
            not isinstance(migration_plan_id, str)
            or not ARCHIVE_ID_RE.fullmatch(migration_plan_id)
        ):
            raise IPodServiceError(
                "invalid_migration_plan",
                "The reviewed migration plan ID is invalid.",
            )
        try:
            payload = json.loads(
                (
                    self.migration_plan_dir
                    / f"{migration_plan_id}.json"
                ).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise IPodServiceError(
                "migration_plan_not_found",
                "The reviewed replacement migration plan was not found.",
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != MIGRATION_PLAN_SCHEMA_VERSION
            or payload.get("migration_plan_id") != migration_plan_id
            or not isinstance(payload.get("snapshot_binding"), Mapping)
        ):
            raise IPodServiceError(
                "invalid_migration_plan",
                "The reviewed replacement migration plan is invalid.",
            )
        if self.clock() > float(payload.get("expires_at", 0)):
            raise IPodServiceError(
                "stale_migration_plan",
                "The reviewed migration plan expired; run preflight again.",
            )
        self._validate_archive_id(
            str(payload.get("source_archive_id", ""))
        )
        self._validate_archive_id(
            str(payload.get("target_archive_id", ""))
        )
        self._validate_snapshot_id(
            str(payload.get("source_snapshot_id", ""))
        )
        expected_bundle = (
            self.migration_bundle_dir / migration_plan_id
        ).resolve()
        try:
            recorded_bundle = Path(str(payload["bundle_path"])).resolve()
        except (KeyError, OSError, RuntimeError) as exc:
            raise IPodServiceError(
                "invalid_migration_plan",
                "The reviewed migration staging path is invalid.",
            ) from exc
        if recorded_bundle != expected_bundle:
            raise IPodServiceError(
                "invalid_migration_plan",
                "The reviewed migration staging path is invalid.",
            )
        return payload

    def _delete_migration_plan(self, migration_plan_id: str) -> None:
        try:
            (
                self.migration_plan_dir / f"{migration_plan_id}.json"
            ).unlink()
        except FileNotFoundError:
            pass

    def _purge_expired_migration_plans(self) -> None:
        for path in self.migration_plan_dir.glob("*.json"):
            migration_plan_id = path.stem
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if self.clock() <= float(payload.get("expires_at", 0)):
                    continue
            except (OSError, ValueError, TypeError):
                pass
            try:
                path.unlink()
            except OSError:
                continue
            if ARCHIVE_ID_RE.fullmatch(migration_plan_id):
                shutil.rmtree(
                    self.migration_bundle_dir / migration_plan_id,
                    ignore_errors=True,
                )

    def _revalidate_migration_record(
        self,
        record: Mapping[str, Any],
        *,
        cancelled: Callable[[], bool] | None,
    ) -> tuple[
        Any,
        Any,
        Any,
        dict[str, Any],
        dict[str, Any],
        tuple[str, ...],
        tuple[dict[str, Any], ...],
        tuple[dict[str, Any], ...],
    ]:
        if self.clock() > float(record["expires_at"]):
            raise IPodServiceError(
                "stale_migration_plan",
                "The reviewed migration plan expired; run preflight again.",
            )
        device = self.adapter.identify_read_only(str(record["mount_path"]))
        self._require_supported_identity(device)
        if (
            self._stable_device_id(device) != record["target_device_id"]
            or self._backup_archive_id(device) != record["target_archive_id"]
        ):
            raise IPodServiceError(
                "wrong_migration_target",
                "A different iPod is mounted at the reviewed migration path.",
            )
        profile = self._inspect_write_readiness(device)
        if self.adapter.volume_key(profile) != record["volume_identity_key"]:
            raise IPodServiceError(
                "wrong_migration_target",
                "The target volume identity changed after migration preflight.",
            )
        generation, tracks, playlists = self._load_for_plan(device)
        if _generation_dict(generation) != record["database_generation"]:
            raise IPodServiceError(
                "stale_migration_plan",
                "The target database changed after migration preflight.",
            )
        manifest, _entries, fingerprint = self._load_backup_manifest(
            str(record["source_archive_id"]),
            str(record["source_snapshot_id"]),
        )
        if (
            self._snapshot_binding(manifest, fingerprint)
            != record["snapshot_binding"]
        ):
            raise IPodServiceError(
                "stale_migration_plan",
                "The selected source snapshot changed after migration preflight.",
            )
        bundle = load_migration_bundle(
            str(record["bundle_path"]),
            verify_media=True,
            cancelled=cancelled,
        )
        if (
            bundle.get("bundle_fingerprint")
            != record["bundle_fingerprint"]
            or bundle.get("snapshot_fingerprint")
            != record["snapshot_binding"]["fingerprint"]
        ):
            raise IPodServiceError(
                "stale_migration_plan",
                "The migration staging bundle changed after review.",
            )
        source_files = migration_source_files(
            str(record["bundle_path"]),
            bundle,
        )
        free_bytes = int(
            float(getattr(device, "free_space_gb", 0) or 0)
            * 1_000_000_000
        )
        if int(record["required_bytes"]) > free_bytes:
            raise IPodServiceError(
                "migration_insufficient_space",
                "The target no longer has enough free space for the migration.",
            )
        return (
            device,
            generation,
            profile,
            manifest,
            bundle,
            source_files,
            tracks,
            playlists,
        )

    def _finish_migration_failure(
        self,
        operation_id: str,
        exc: Exception,
        record: Mapping[str, Any],
        safety_snapshot_id: str,
    ) -> None:
        if not operation_id:
            return
        try:
            current = self.operation_journal.get(operation_id)
        except OperationJournalError:
            return
        if current.get("status") != "running":
            return
        phase = str(current.get("phase", "") or "")
        recovery_required = phase in {"committing", "finalizing"}
        code = (
            str(exc.code)
            if isinstance(exc, IPodServiceError)
            else "migration_failed"
        )
        try:
            self.operation_journal.finish(
                operation_id,
                "failed",
                phase=(
                    "recovery_required"
                    if recovery_required
                    else "failed"
                ),
                safety_snapshot_id=safety_snapshot_id,
                reconnect={
                    "required_device_id": record.get("target_device_id", ""),
                    "mount_path": record.get("mount_path", ""),
                },
                recovery={
                    "required": recovery_required,
                    "code": code,
                    "safety_snapshot_id": safety_snapshot_id,
                    "next_action": (
                        "Reconnect the exact replacement iPod, inspect the "
                        "migration recovery state, and use the target safety "
                        "snapshot if validation fails."
                        if recovery_required
                        else ""
                    ),
                },
                error_code=code,
                error_message=str(exc),
            )
        except OperationJournalError:
            pass
        with self._operation_state_lock:
            if self._active_operation_id == operation_id:
                self._active_operation_phase = (
                    "recovery_required"
                    if recovery_required
                    else "failed"
                )
                self._active_operation_can_cancel = False

    def _revalidate_restore_record(
        self,
        record: Mapping[str, Any],
    ) -> tuple[Any, Any, dict[str, Any], str]:
        if self.clock() > float(record["expires_at"]):
            raise IPodServiceError(
                "stale_restore_plan",
                "The reviewed restore plan expired; run restore preflight again.",
            )
        device = self.adapter.identify_read_only(str(record["mount_path"]))
        self._require_supported_identity(device)
        if self._stable_device_id(device) != record["target_device_id"]:
            raise IPodServiceError(
                "wrong_restore_target",
                "A different iPod is mounted at the reviewed restore path.",
            )
        if self._backup_archive_id(device) != record["target_archive_id"]:
            raise IPodServiceError(
                "wrong_restore_target",
                "The mounted iPod no longer matches the reviewed backup archive.",
            )
        profile = self._inspect_write_readiness(device)
        if self.adapter.volume_key(profile) != record["volume_identity_key"]:
            raise IPodServiceError(
                "wrong_restore_target",
                "The mounted volume identity changed after restore preflight.",
            )
        generation = self.adapter.capture_database_generation(device.path)
        if _generation_dict(generation) != record["database_generation"]:
            raise IPodServiceError(
                "stale_restore_plan",
                "The iPod database changed after restore preflight.",
            )
        manifest, _entries, fingerprint = self._load_backup_manifest(
            str(record["source_archive_id"]),
            str(record["source_snapshot_id"]),
        )
        if (
            self._snapshot_binding(manifest, fingerprint)
            != record["snapshot_binding"]
        ):
            raise IPodServiceError(
                "stale_restore_plan",
                "The selected backup snapshot changed after restore preflight.",
            )
        return device, profile, manifest, fingerprint

    def _restore_reconnect_info(
        self,
        device: Any,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "required_device_id": str(record["target_device_id"]),
            "required_archive_id": str(record["target_archive_id"]),
            "device_name": str(
                getattr(device, "ipod_name", "")
                or getattr(device, "display_name", "")
                or "iPod"
            )[:300],
            "model_family": str(
                getattr(device, "model_family", "") or ""
            )[:100],
            "mount_path": str(record["mount_path"])[:2_000],
        }

    def _restore_error_info(self, exc: Exception) -> dict[str, Any] | None:
        mapper = getattr(self.adapter, "restore_error_info", None)
        if callable(mapper):
            info = mapper(exc)
            if isinstance(info, Mapping):
                return dict(info)
        class_name = type(exc).__name__
        if class_name == "RestoreDurabilityPendingError":
            return {
                "code": "restore_durability_pending",
                "device_dirty": True,
                "content_verified": True,
                "requires_safe_eject": True,
                "safety_snapshot_id": str(
                    getattr(exc, "safety_snapshot_id", "") or ""
                ),
            }
        if class_name == "RestoreIncompleteError":
            return {
                "code": "restore_incomplete",
                "device_dirty": True,
                "content_verified": False,
                "requires_safe_eject": False,
                "safety_snapshot_id": str(
                    getattr(exc, "safety_snapshot_id", "") or ""
                ),
            }
        return None

    def _record_restore_recovery(
        self,
        operation_id: str,
        device: Any,
        record: Mapping[str, Any],
        exc: Exception,
        info: Mapping[str, Any],
    ) -> None:
        code = str(info.get("code", "restore_incomplete"))
        safety_snapshot_id = str(
            info.get("safety_snapshot_id", "") or ""
        )[:128]
        requires_safe_eject = bool(info.get("requires_safe_eject", False))
        recovery = {
            "required": True,
            "code": code,
            "message": str(exc)[:2_000],
            "device_dirty": bool(info.get("device_dirty", True)),
            "content_verified": bool(info.get("content_verified", False)),
            "requires_safe_eject": requires_safe_eject,
            "source_archive_id": str(record["source_archive_id"]),
            "source_snapshot_id": str(record["source_snapshot_id"]),
            "safety_snapshot_id": safety_snapshot_id,
            "next_action": (
                "Keep the iPod connected and use safe eject before unplugging."
                if requires_safe_eject
                else (
                    "Reconnect this exact iPod and rerun the same snapshot restore "
                    "before syncing or using it."
                )
            ),
        }
        self.operation_journal.finish(
            operation_id,
            "failed",
            phase=(
                "durability_pending"
                if requires_safe_eject
                else "recovery_required"
            ),
            safety_snapshot_id=safety_snapshot_id,
            reconnect=self._restore_reconnect_info(device, record),
            recovery=recovery,
            error_code=code,
            error_message=str(exc),
        )
        with self._operation_state_lock:
            if self._active_operation_id == operation_id:
                self._active_operation_phase = (
                    "durability_pending"
                    if requires_safe_eject
                    else "recovery_required"
                )
                self._active_operation_can_cancel = False

    def _begin_operation(
        self,
        kind: str,
        *,
        phase: str,
        can_cancel: bool,
        target_id: str = "",
        source_id: str = "",
        target_archive_id: str = "",
        source_archive_id: str = "",
        snapshot_id: str = "",
        reconnect: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        self._cancel.clear()
        try:
            record = self.operation_journal.start(
                kind,
                phase=phase,
                can_cancel=can_cancel,
                target_id=target_id,
                source_id=source_id,
                target_archive_id=target_archive_id,
                source_archive_id=source_archive_id,
                snapshot_id=snapshot_id,
                reconnect=reconnect,
                metadata=metadata,
            )
        except OperationJournalError as exc:
            raise IPodServiceError(
                "operation_journal_failed",
                str(exc),
            ) from exc
        operation_id = str(record["operation_id"])
        with self._operation_state_lock:
            self._active_operation_id = operation_id
            self._active_operation_kind = kind
            self._active_operation_phase = phase
            self._active_operation_can_cancel = bool(can_cancel)
        return operation_id

    def _transition_operation(
        self,
        operation_id: str,
        phase: str,
        *,
        can_cancel: bool,
        safety_snapshot_id: str | None = None,
    ) -> None:
        if not operation_id:
            return
        with self._operation_state_lock:
            unchanged = (
                self._active_operation_id == operation_id
                and self._active_operation_phase == phase
                and self._active_operation_can_cancel == bool(can_cancel)
                and safety_snapshot_id is None
            )
        if unchanged:
            return
        try:
            self.operation_journal.transition(
                operation_id,
                phase,
                can_cancel=can_cancel,
                safety_snapshot_id=safety_snapshot_id,
            )
        except OperationJournalError as exc:
            raise IPodServiceError(
                "operation_journal_failed",
                str(exc),
            ) from exc
        with self._operation_state_lock:
            if self._active_operation_id == operation_id:
                self._active_operation_phase = phase
                self._active_operation_can_cancel = bool(can_cancel)
                if not can_cancel:
                    self._cancel.clear()

    def _finish_operation(
        self,
        operation_id: str,
        status: str,
        *,
        phase: str = "complete",
        safety_snapshot_id: str = "",
    ) -> None:
        if not operation_id:
            return
        try:
            self.operation_journal.finish(
                operation_id,
                status,
                phase=phase,
                safety_snapshot_id=safety_snapshot_id,
            )
        except OperationJournalError as exc:
            raise IPodServiceError(
                "operation_journal_failed",
                str(exc),
            ) from exc
        with self._operation_state_lock:
            if self._active_operation_id == operation_id:
                self._active_operation_phase = phase
                self._active_operation_can_cancel = False

    def _finish_failed_operation(
        self,
        operation_id: str,
        exc: Exception,
    ) -> None:
        if not operation_id:
            return
        code = exc.code if isinstance(exc, IPodServiceError) else "operation_failed"
        try:
            self.operation_journal.finish(
                operation_id,
                "failed",
                phase="failed",
                error_code=str(code),
                error_message=str(exc),
            )
        except OperationJournalError:
            pass
        with self._operation_state_lock:
            if self._active_operation_id == operation_id:
                self._active_operation_phase = "failed"
                self._active_operation_can_cancel = False

    def _clear_active_operation(self, operation_id: str) -> None:
        if not operation_id:
            return
        with self._operation_state_lock:
            if self._active_operation_id == operation_id:
                self._active_operation_id = ""
                self._active_operation_kind = ""
                self._active_operation_phase = ""
                self._active_operation_can_cancel = False
                self._cancel.clear()

    def _combined_cancel(
        self,
        operation_id: str,
        external: Callable[[], bool] | None,
    ) -> Callable[[], bool]:
        def is_cancelled() -> bool:
            with self._operation_state_lock:
                can_cancel = (
                    self._active_operation_id == operation_id
                    and self._active_operation_can_cancel
                    and self._active_operation_phase
                    not in {"committing", "finalizing"}
                )
            if not can_cancel:
                return False
            return self._cancel.is_set() or bool(external and external())

        return is_cancelled

    def _operation_progress_adapter(
        self,
        stage_prefix: str,
        emit: Callable[[dict[str, Any]], None] | None,
        operation_id: str,
    ) -> Callable[[Any], None]:
        def callback(event: Any) -> None:
            raw_stage = str(
                getattr(event, "stage", "") or stage_prefix
            ).casefold()
            phase, can_cancel = self._operation_phase(
                stage_prefix,
                raw_stage,
            )
            self._transition_operation(
                operation_id,
                phase,
                can_cancel=can_cancel,
                safety_snapshot_id=(
                    str(getattr(event, "safety_snapshot_id", "") or "")
                    or None
                ),
            )
            with self._operation_state_lock:
                operation_kind = self._active_operation_kind
            self._emit(
                emit,
                f"{stage_prefix}:{raw_stage}",
                int(getattr(event, "current", 0) or 0),
                int(getattr(event, "total", 0) or 0),
                str(getattr(event, "message", "") or ""),
                operation_id=operation_id,
                operation_kind=operation_kind,
                phase=phase,
                can_cancel=can_cancel,
            )

        return callback

    @staticmethod
    def _operation_phase(
        stage_prefix: str,
        raw_stage: str,
    ) -> tuple[str, bool]:
        if raw_stage in {
            "committing",
            "cleaning",
            "restoring",
            "execute_files",
            "assemble_commit",
            "commit",
        }:
            return "committing", False
        if raw_stage in {"finalizing", "post_commit"}:
            return "finalizing", False
        if stage_prefix in {"restore", "execute"} and raw_stage == "complete":
            return "finalizing", False
        if stage_prefix == "restore-safety":
            return "safety_checkpoint", True
        if stage_prefix == "revalidate":
            return "revalidating", True
        if "verify" in stage_prefix or raw_stage == "verifying":
            return "verifying", True
        if "export" in stage_prefix or raw_stage == "exporting":
            return "exporting", True
        if "backup" in stage_prefix:
            return "backing_up", True
        return "preparing", True

    def _load_for_plan(self, device: Any) -> tuple[Any, tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
        before = self.adapter.capture_database_generation(device.path)
        library = self.adapter.load_library(device.path)
        after = self.adapter.capture_database_generation(device.path)
        if before != after:
            raise IPodServiceError("database_changed", "The iPod database changed while preparing the plan.")
        return after, tuple(library.get("mhlt", []) or []), tuple(library.get("mhlp", []) or [])

    def _validate_source_files(self, source_files: Iterable[str | Path]) -> tuple[str, ...]:
        files: list[str] = []
        for item in source_files:
            path = _canonical_path(item)
            if not os.path.isfile(path):
                raise IPodServiceError("source_missing", f"A staged source file is missing: {path}")
            if Path(path).suffix.lower() not in MEDIA_EXTENSIONS:
                raise IPodServiceError("unsupported_source", f"Unsupported staged media file: {path}")
            files.append(path)
        result = tuple(sorted(set(files)))
        if not result:
            raise IPodServiceError("empty_source", "Choose at least one completed local media file.")
        return result

    def _validate_library_completed_files(
        self,
        completed_files: Iterable[str | Path],
        library_root: str | Path,
    ) -> tuple[str, ...]:
        root = _canonical_path(library_root)
        if not root or not os.path.isdir(root):
            raise IPodServiceError("invalid_library_root", "The configured Vela library root is unavailable.")
        files = self._validate_source_files(completed_files)
        for path in files:
            try:
                contained = os.path.commonpath((root, path)) == root
            except ValueError:
                contained = False
            if not contained or path == root:
                raise IPodServiceError(
                    "source_outside_library",
                    "Only completed files under the configured Vela library root can be staged.",
                )
        return files

    def _read_staging(self, staging_id: str) -> dict[str, Any]:
        if not staging_id or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in staging_id
        ):
            raise IPodServiceError("invalid_staging", "The staged-source ID is invalid.")
        try:
            payload = json.loads(
                (self.staging_dir / f"{staging_id}.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise IPodServiceError("staging_not_found", "The staged download set was not found.") from exc
        if payload.get("staging_id") != staging_id or payload.get("schema_version") != 1:
            raise IPodServiceError("invalid_staging", "The staged download set is invalid.")
        return payload

    @staticmethod
    def _source_fingerprint(files: tuple[str, ...]) -> str:
        digest = hashlib.sha256()
        for path in files:
            stat = os.stat(path)
            digest.update(path.encode("utf-8", errors="surrogatepass"))
            digest.update(f"|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
        return digest.hexdigest()

    @staticmethod
    def _plan_item(group: str, item: Any, index: int) -> dict[str, Any]:
        pc_track = getattr(item, "pc_track", None)
        ipod_track = getattr(item, "ipod_track", None) or {}
        source_path = str(getattr(item, "source_path", "") or getattr(pc_track, "path", "") or "")
        title = str(
            getattr(pc_track, "title", "")
            or ipod_track.get("Title")
            or ipod_track.get("title")
            or Path(source_path).stem
            or getattr(item, "display_label", "")
            or "Track"
        )
        artist = str(
            getattr(pc_track, "artist", "")
            or getattr(pc_track, "artist_string", "")
            or ipod_track.get("Artist")
            or ipod_track.get("artist")
            or ""
        )
        album = str(
            getattr(pc_track, "album", "")
            or ipod_track.get("Album")
            or ipod_track.get("album")
            or ""
        )
        action_value = getattr(getattr(item, "action", None), "name", None)
        action = str(action_value or getattr(item, "action", "") or group).lower()
        fingerprint = str(getattr(item, "fingerprint", "") or "")
        db_track_id = int(getattr(item, "db_track_id", 0) or ipod_track.get("db_track_id", 0) or ipod_track.get("db_id", 0) or 0)
        ipod_location = str(getattr(item, "ipod_location", "") or ipod_track.get("Location") or ipod_track.get("location") or "")
        identity = "|".join((
            group, action, fingerprint, _canonical_path(source_path) if source_path else "",
            str(db_track_id), ipod_location, str(index),
        ))
        result: dict[str, Any] = {
            "item_id": hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:24],
            "group": group,
            "action": action,
            "title": title[:300],
            "artist": artist[:300],
            "album": album[:300],
            "description": str(getattr(item, "description", "") or "")[:500],
            "estimated_bytes": max(0, int(getattr(item, "planned_add_size", 0) or getattr(item, "estimated_size", 0) or 0)),
            "removed_bytes": max(0, int(getattr(item, "planned_remove_size", 0) or 0)),
        }
        if source_path:
            result["source_path"] = _canonical_path(source_path)
        if ipod_location:
            result["ipod_location"] = ipod_location[:500]
        if db_track_id:
            result["db_track_id"] = db_track_id
        metadata_changes = getattr(item, "metadata_changes", None)
        if isinstance(metadata_changes, dict) and metadata_changes:
            result["metadata_fields"] = sorted(str(key)[:100] for key in metadata_changes)[:100]
        transcode = getattr(item, "transcode_plan", None)
        if transcode is not None:
            result["conversion"] = {
                "required": True,
                "target_format": str(
                    getattr(transcode, "target_format", "")
                    or getattr(transcode, "output_format", "")
                    or ""
                )[:100],
                "reason": str(getattr(transcode, "reason", "") or "")[:300],
            }
        return result

    @staticmethod
    def _playlist_item(effect: str, playlist: Mapping[str, Any], index: int) -> dict[str, Any]:
        title = str(playlist.get("Title") or playlist.get("title") or "Playlist")
        playlist_id = str(playlist.get("playlist_id") or playlist.get("persistent_id") or "")
        source_path = str(playlist.get("_sync_playlist_path") or "")
        identity = f"playlist|{effect}|{playlist_id}|{source_path}|{index}"
        result = {
            "item_id": hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:24],
            "group": "playlist_effects",
            "action": effect,
            "title": title[:300],
            "track_count": min(1_000_000, len(playlist.get("items") or [])),
            "skipped_count": max(0, int(playlist.get("_sync_playlist_skipped_count", 0) or 0)),
        }
        if playlist_id:
            result["playlist_id"] = playlist_id[:200]
        if source_path:
            result["source_path"] = _canonical_path(source_path)
        return result

    @classmethod
    def _plan_details(cls, plan: Any) -> dict[str, list[dict[str, Any]]]:
        details: dict[str, list[dict[str, Any]]] = {
            "additions": [],
            "removals": [],
            "metadata_updates": [],
            "artwork_updates": [],
            "conversions": [],
            "playlist_effects": [],
            "warnings": [],
            "unsupported": [],
        }
        buckets = (
            ("additions", "to_add"),
            ("removals", "to_remove"),
            ("metadata_updates", "to_update_metadata"),
            ("artwork_updates", "to_update_artwork"),
        )
        for group, attribute in buckets:
            for index, item in enumerate(getattr(plan, attribute, ()) or ()):
                row = cls._plan_item(group, item, index)
                details[group].append(row)
                if getattr(item, "transcode_plan", None) is not None:
                    conversion = dict(row)
                    conversion["group"] = "conversions"
                    conversion["item_id"] = hashlib.sha256(
                        f"conversion|{row['item_id']}".encode()
                    ).hexdigest()[:24]
                    details["conversions"].append(conversion)
        for index, item in enumerate(getattr(plan, "to_update_file", ()) or ()):
            row = cls._plan_item("conversions", item, index)
            details["conversions"].append(row)

        playlist_index = 0
        for effect, attribute in (
            ("add", "playlists_to_add"),
            ("update", "playlists_to_edit"),
            ("remove", "playlists_to_remove"),
        ):
            for playlist in getattr(plan, attribute, ()) or ():
                if isinstance(playlist, Mapping):
                    details["playlist_effects"].append(
                        cls._playlist_item(effect, playlist, playlist_index)
                    )
                    playlist_index += 1

        warning_index = 0
        for code, entries in (
            ("fingerprint_error", getattr(plan, "fingerprint_errors", ()) or ()),
            ("unresolved_collision", getattr(plan, "unresolved_collisions", ()) or ()),
            ("duplicate", (getattr(plan, "duplicates", {}) or {}).items()),
        ):
            for entry in entries:
                message = str(entry[1] if isinstance(entry, tuple) and len(entry) > 1 else entry)
                identity = f"warning|{code}|{message}|{warning_index}"
                details["warnings"].append({
                    "item_id": hashlib.sha256(identity.encode()).hexdigest()[:24],
                    "group": "warnings",
                    "action": "warning",
                    "code": code,
                    "message": message[:500],
                })
                warning_index += 1

        unsupported = getattr(plan, "unsupported", None) or getattr(plan, "unsupported_files", None) or ()
        for index, entry in enumerate(unsupported):
            message = str(getattr(entry, "reason", "") or entry)
            identity = f"unsupported|{message}|{index}"
            details["unsupported"].append({
                "item_id": hashlib.sha256(identity.encode()).hexdigest()[:24],
                "group": "unsupported",
                "action": "unsupported",
                "message": message[:500],
            })
        return details

    @staticmethod
    def _detail_group_descriptors(details: Mapping[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        return [
            {"group": group, "total": len(items), "page_size_max": MAX_PLAN_DETAIL_PAGE_SIZE}
            for group, items in details.items()
        ]

    @staticmethod
    def _plan_summary(
        plan: Any,
        files: tuple[str, ...],
        details: Mapping[str, list[dict[str, Any]]],
        device: Any,
    ) -> dict[str, Any]:
        def count(*names: str) -> int:
            for name in names:
                value = getattr(plan, name, None)
                if value is not None:
                    try:
                        return len(value)
                    except TypeError:
                        return int(value or 0)
            return 0

        required = sum(os.path.getsize(path) for path in files)
        planned_size = getattr(plan, "planned_add_size", None)
        if callable(planned_size):
            required = int(planned_size() or required)
        storage = getattr(plan, "storage", None)
        bytes_to_add = max(0, int(getattr(storage, "bytes_to_add", required) or required))
        bytes_to_remove = max(0, int(getattr(storage, "bytes_to_remove", 0) or 0))
        bytes_to_update = max(0, int(getattr(storage, "bytes_to_update", 0) or 0))
        free_before = max(0, int(float(getattr(device, "free_space_gb", 0) or 0) * 1_000_000_000))
        summary = {
            "additions": len(details["additions"]),
            "removals": len(details["removals"]),
            "updates": len(details["metadata_updates"]) + len(details["artwork_updates"]),
            "conversions": len(details["conversions"]),
            "playlist_changes": len(details["playlist_effects"]),
            "warnings": len(details["warnings"]),
            "unsupported": len(details["unsupported"]),
            "required_bytes": max(0, required),
            "source_count": len(files),
            "storage": {
                "bytes_to_add": bytes_to_add,
                "bytes_to_remove": bytes_to_remove,
                "bytes_to_update": bytes_to_update,
                "net_change_bytes": bytes_to_add + bytes_to_update - bytes_to_remove,
                "required_free_bytes": max(0, required),
                "free_before_bytes": free_before,
                "free_after_bytes": max(
                    0,
                    free_before - max(0, bytes_to_add + bytes_to_update - bytes_to_remove),
                ),
            },
        }
        summary["review_fingerprint"] = hashlib.sha256(
            json.dumps({"summary": summary, "details": details}, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return summary

    def _revalidate_record(self, record: Mapping[str, Any]) -> tuple[Any, Any, Any]:
        if self.clock() > float(record["expires_at"]):
            raise IPodServiceError("stale_plan", "The reviewed sync plan expired; review a new plan.")
        files = tuple(str(item) for item in record["source_files"])
        if self._source_fingerprint(files) != record["source_fingerprint"]:
            raise IPodServiceError("stale_plan", "A reviewed source file changed; review a new plan.")
        device = self.adapter.identify_read_only(str(record["mount_path"]))
        self._require_supported_identity(device)
        if self._stable_device_id(device) != record["device_id"]:
            raise IPodServiceError("device_changed", "A different iPod is mounted at the reviewed path.")
        profile = self._inspect_write_readiness(device)
        if self.adapter.volume_key(profile) != record["volume_identity_key"]:
            raise IPodServiceError("device_changed", "The mounted volume identity changed after review.")
        generation = self.adapter.capture_database_generation(device.path)
        if _generation_dict(generation) != record["database_generation"]:
            raise IPodServiceError("stale_plan", "The iPod database changed after review.")
        free_bytes = int(float(getattr(device, "free_space_gb", 0) or 0) * 1_000_000_000)
        if int(record["required_bytes"]) > free_bytes:
            raise IPodServiceError("insufficient_space", "The iPod no longer has enough free space.")
        return device, generation, profile

    def _write_plan(self, record: Mapping[str, Any]) -> None:
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        self._purge_expired_plans()
        self._atomic_json(self.plan_dir / f"{record['plan_id']}.json", record)

    def _read_plan(self, plan_id: str) -> dict[str, Any]:
        if not plan_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in plan_id):
            raise IPodServiceError("invalid_plan", "The reviewed plan ID is invalid.")
        path = self.plan_dir / f"{plan_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise IPodServiceError("plan_not_found", "The reviewed sync plan was not found.") from exc
        if payload.get("plan_id") != plan_id or payload.get("schema_version") != PLAN_SCHEMA_VERSION:
            raise IPodServiceError("invalid_plan", "The reviewed sync plan is invalid.")
        return payload

    def _delete_plan(self, plan_id: str) -> None:
        try:
            (self.plan_dir / f"{plan_id}.json").unlink()
        except FileNotFoundError:
            pass

    def _purge_expired_plans(self) -> None:
        for path in self.plan_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if self.clock() > float(payload.get("expires_at", 0)):
                    path.unlink()
            except (OSError, ValueError, TypeError):
                try:
                    path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    @staticmethod
    def _atomic_bytes(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(bytes(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _progress_adapter(stage_prefix: str, emit: Callable[[dict[str, Any]], None] | None):
        if emit is None:
            return None

        def callback(event: Any) -> None:
            stage = str(getattr(event, "stage", "") or stage_prefix)
            IPodService._emit(
                emit,
                f"{stage_prefix}:{stage}",
                int(getattr(event, "current", 0) or 0),
                int(getattr(event, "total", 0) or 0),
                str(getattr(event, "message", "") or ""),
            )
        return callback

    @staticmethod
    def _emit(
        emit: Callable[[dict[str, Any]], None] | None,
        stage: str,
        current: int,
        total: int,
        message: str,
        *,
        operation_id: str = "",
        operation_kind: str = "",
        phase: str = "",
        can_cancel: bool | None = None,
    ) -> None:
        if emit:
            event = {
                "type": "ipod_progress",
                "protocol_version": PROTOCOL_VERSION,
                "stage": stage[:64],
                "current": max(0, current),
                "total": max(0, total),
                "message": message[:500],
            }
            if operation_id:
                event.update({
                    "operation_id": operation_id[:128],
                    "operation_kind": operation_kind[:64],
                    "phase": phase[:64],
                    "can_cancel": bool(can_cancel),
                })
            emit(event)
