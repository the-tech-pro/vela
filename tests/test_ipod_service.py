from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from antra.core.ipod_service import (
    IOpenPodAdapter,
    IPodService,
    IPodServiceError,
)
from antra.core.ipod_cli import add_ipod_arguments
from antra.core.ipod_migration import _payload_fingerprint


@dataclasses.dataclass(frozen=True)
class FakeGeneration:
    filename: str = "iTunesDB"
    exists: bool = True
    size: int = 12
    modified_ns: int = 1
    device: int = 2
    inode: int = 3
    digest: str = "abc"


class FakePlan:
    def __init__(self, required_bytes: int, source_path: str, additions: int = 1):
        base_item = SimpleNamespace(
            action=SimpleNamespace(name="ADD_TO_IPOD"),
            fingerprint="stable-fingerprint",
            pc_track=SimpleNamespace(
                path=source_path, title="Planned track", artist="Artist", album="Album"
            ),
            source_path=source_path,
            estimated_size=required_bytes,
            planned_add_size=required_bytes,
            planned_remove_size=0,
            transcode_plan=SimpleNamespace(target_format="aac", reason="device compatibility"),
            db_track_id=None,
            ipod_track={},
            metadata_changes={},
            description="Add Planned track",
            ipod_location="",
        )
        self.to_add = [base_item for _ in range(additions)]
        self.to_remove = []
        self.to_update_metadata = [
            SimpleNamespace(
                action=SimpleNamespace(name="UPDATE_METADATA"),
                fingerprint="metadata-fingerprint",
                pc_track=SimpleNamespace(
                    path=source_path, title="Metadata track", artist="Artist", album="Album"
                ),
                source_path=source_path,
                estimated_size=0,
                planned_add_size=0,
                planned_remove_size=0,
                transcode_plan=None,
                db_track_id=12,
                ipod_track={},
                metadata_changes={"title": ("Old", "New")},
                description="Update title",
                ipod_location="",
            )
        ]
        self.to_update_file = []
        self.to_update_artwork = []
        self.playlists_to_add = [
            {"Title": "Road Trip", "playlist_id": 42, "items": [{"db_track_id": 1}]}
        ]
        self.playlists_to_edit = []
        self.playlists_to_remove = []
        self.fingerprint_errors = [("bad.flac", "Unreadable fingerprint")]
        self.unresolved_collisions = []
        self.duplicates = {}
        self.unsupported = ["DRM-protected source"]
        self.storage = SimpleNamespace(
            bytes_to_add=required_bytes,
            bytes_to_remove=0,
            bytes_to_update=0,
        )
        self._required_bytes = required_bytes

    def planned_add_size(self):
        return self._required_bytes


class FakeAdapter:
    def __init__(self, root: Path):
        self.device = SimpleNamespace(
            path=str(root / "ipod"),
            ipod_name="Test iPod",
            display_name="Test iPod",
            model_family="iPod Classic",
            generation="6th Gen",
            model_number="MB145",
            serial="SERIAL123",
            firewire_guid="0011223344556677",
            firmware="2.0.5",
            filesystem_type="fat32",
            volume_identity_key="virtual|device|volume|mount",
            disk_size_gb=20.0,
            free_space_gb=10.0,
            reported_volume_format="FAT32",
            uses_sqlite_db=False,
            db_version=48,
            shadow_db_version=0,
            hashing_scheme=2,
            checksum_type=1,
            podcasts_supported=True,
            videos_supported=True,
        )
        Path(self.device.path).mkdir(parents=True)
        self.generation = FakeGeneration()
        self.profile = SimpleNamespace(
            identity=SimpleNamespace(is_complete=True),
            volume_identity_key=self.device.volume_identity_key,
            reported_volume_format="FAT32",
        )
        self.library = {
            "mhlt": [
                {"id": 1, "title": "One", "secret": "drop"},
                {"id": 2, "title": "Two"},
                {"id": 3, "title": "Three"},
            ],
            "mhla": [{"id": 7, "album": "Album"}],
            "mhlp": [{"id": 9, "title": "Playlist", "items": []}],
        }
        self.calls: list[str] = []
        self.backup_result = {"snapshot_id": "verified"}
        self.cancel_during_backup = False
        self.eject_result = (True, "Ejected")

    def scan_read_only(self):
        return [self.device]

    def identify_read_only(self, mount_path):
        if os.path.normcase(os.path.realpath(mount_path)) != os.path.normcase(os.path.realpath(self.device.path)):
            raise IPodServiceError("device_not_found", "gone")
        return self.device

    def capture_database_generation(self, _mount_path):
        return self.generation

    def load_library(self, _mount_path):
        return self.library

    def inspect_write_readiness(self, _device):
        return self.profile

    def volume_key(self, _profile):
        return self.device.volume_identity_key

    def compute_plan(self, _device, files, _tracks, _playlists, progress, cancelled):
        self.calls.append("plan")
        if cancelled and cancelled():
            raise IPodServiceError("cancelled", "cancelled")
        if progress:
            progress(SimpleNamespace(stage="plan", current=1, total=1, message="planned"))
        return FakePlan(sum(os.path.getsize(path) for path in files), files[0])

    def create_backup(self, _device, _backup_root, _volume_key, progress, cancelled):
        self.calls.append("backup")
        if progress:
            progress(SimpleNamespace(stage="hashing", current=1, total=1, message="verified"))
        if self.cancel_during_backup or (cancelled and cancelled()):
            return None
        return self.backup_result

    def execute_plan(self, _device, _plan, _generation, _profile, progress, cancelled):
        self.calls.append("execute")
        assert not (cancelled and cancelled())
        if progress:
            progress(SimpleNamespace(stage="commit", current=1, total=1, message="done"))
        return {"success": True}

    def eject(self, _device):
        self.calls.append("eject")
        return self.eject_result


@pytest.fixture
def setup(tmp_path):
    adapter = FakeAdapter(tmp_path)
    source = tmp_path / "staging" / "track.mp3"
    source.parent.mkdir()
    source.write_bytes(b"audio")
    service = IPodService(tmp_path / "appdata", adapter=adapter, plan_ttl_seconds=60)
    return service, adapter, source


def test_physical_identification_never_calls_enrich(tmp_path):
    calls = []
    scanner = SimpleNamespace(
        _get_disk_info=lambda _path: (10.0, 5.0),
        _probe_hardware=lambda _path, _name: calls.append("hardware") or {},
        _probe_filesystem=lambda _path: calls.append("filesystem") or {
            "model_family": "iPod Classic",
            "generation": "6th Gen",
            "model_number": "MB145",
            "serial": "SERIAL",
            "firewire_guid": "0011223344556677",
            "filesystem_type": "fat32",
        },
        _resolve_model=lambda _hw, fs, _size: fs,
        _extract_ipod_name=lambda _path: "Read Only",
        _estimate_capacity_from_disk_size=lambda _size: "10GB",
    )
    with patch("iopenpod.device.info.enrich", side_effect=AssertionError("must not enrich")):
        device = IOpenPodAdapter._identify_physical_read_only(scanner, str(tmp_path), "IPOD")
    assert calls == ["hardware", "filesystem"]
    assert device.ipod_name == "Read Only"


def test_browse_real_iopenpod_virtual_fixture(tmp_path):
    from iopenpod.device.virtual import (
        available_virtual_ipod_models,
        create_virtual_ipod,
        load_virtual_ipod_info,
    )

    root = tmp_path / "virtual-ipod"
    model = next(
        row["model_number"]
        for row in available_virtual_ipod_models()
        if row["model_family"] == "iPod Classic"
    )
    create_virtual_ipod(root, model, ipod_name="Vela Test")

    class VirtualAdapter(IOpenPodAdapter):
        def identify_read_only(self, mount_path):
            assert os.path.normcase(os.path.realpath(mount_path)) == os.path.normcase(os.path.realpath(root))
            return load_virtual_ipod_info(root)

    service = IPodService(tmp_path / "appdata", adapter=VirtualAdapter())
    result = service.browse(str(root), "tracks", page=1, page_size=25)
    assert result["total"] == 0
    assert result["database_generation"]["exists"] is True


def test_browse_is_paged_and_bounded(setup):
    service, _adapter, _source = setup
    result = service.browse(service.adapter.device.path, "tracks", page=2, page_size=2)
    assert result["total"] == 3
    assert [row["title"] for row in result["items"]] == ["Three"]
    assert "secret" not in result["items"][0]


def test_rejects_stale_device_identity_and_database_generation(setup):
    service, adapter, source = setup
    plan = service.create_plan(adapter.device.path, [source])
    adapter.device.serial = "DIFFERENT"
    with pytest.raises(IPodServiceError, match="different iPod"):
        service.execute(plan["plan_id"], confirmed=True)

    adapter.device.serial = "SERIAL123"
    plan = service.create_plan(adapter.device.path, [source])
    adapter.generation = FakeGeneration(digest="changed")
    with pytest.raises(IPodServiceError, match="database changed"):
        service.execute(plan["plan_id"], confirmed=True)


def test_rejects_expired_reviewed_plan(tmp_path):
    adapter = FakeAdapter(tmp_path)
    source = tmp_path / "track.mp3"
    source.write_bytes(b"audio")
    now = [1000.0]
    service = IPodService(
        tmp_path / "appdata",
        adapter=adapter,
        plan_ttl_seconds=60,
        clock=lambda: now[0],
    )
    plan = service.create_plan(adapter.device.path, [source])
    now[0] += 61
    with pytest.raises(IPodServiceError, match="expired"):
        service.execute(plan["plan_id"], confirmed=True)


def test_rejects_insufficient_space_at_plan_and_execute(setup):
    service, adapter, source = setup
    adapter.device.free_space_gb = 0
    with pytest.raises(IPodServiceError, match="free space"):
        service.create_plan(adapter.device.path, [source])

    adapter.device.free_space_gb = 10
    plan = service.create_plan(adapter.device.path, [source])
    adapter.device.free_space_gb = 0
    with pytest.raises(IPodServiceError, match="enough free space"):
        service.execute(plan["plan_id"], confirmed=True)


def test_verified_backup_happens_before_write(setup):
    service, adapter, source = setup
    plan = service.create_plan(adapter.device.path, [source])
    adapter.calls.clear()
    result = service.execute(plan["plan_id"], confirmed=True)
    assert result["ok"] is True
    assert adapter.calls == ["plan", "backup", "execute"]


def test_backup_cancellation_prevents_write(setup):
    service, adapter, source = setup
    plan = service.create_plan(adapter.device.path, [source])
    adapter.calls.clear()
    adapter.cancel_during_backup = True
    with pytest.raises(IPodServiceError, match="backup"):
        service.execute(plan["plan_id"], confirmed=True)
    assert adapter.calls == ["plan", "backup"]


def test_staging_contract_is_local_and_plan_reviewed(setup):
    service, adapter, source = setup
    staged = service.create_staging_contract(adapter.device.path, [source], source.parent)
    assert staged["completed_files"] == [os.path.normcase(os.path.realpath(source))]
    manifest = service.staging_dir / f"{staged['staging_id']}.json"
    assert manifest.is_file()
    assert not str(manifest).startswith(adapter.device.path)
    plan = service.create_plan(
        adapter.device.path,
        staged["completed_files"],
        staging_id=staged["staging_id"],
    )
    assert plan["plan_id"] and plan["source_count"] == 1


def test_reviewed_plan_exposes_bounded_stable_group_details(setup):
    service, adapter, source = setup
    plan = service.create_plan(adapter.device.path, [source])

    assert plan["additions"] == 1
    assert plan["updates"] == 1
    assert plan["conversions"] == 1
    assert plan["playlist_changes"] == 1
    assert plan["warnings"] == 1
    assert plan["unsupported"] == 1
    assert plan["storage"]["bytes_to_add"] == source.stat().st_size
    assert plan["group_previews"]["additions"][0]["item_id"]

    page = service.plan_details(plan["plan_id"], "additions", page=1, page_size=999)
    assert page["page_size"] == 100
    assert page["total"] == 1
    assert page["items"][0]["item_id"] == plan["group_previews"]["additions"][0]["item_id"]
    assert page["items"][0]["source_path"] == os.path.normcase(os.path.realpath(source))


def test_staging_rejects_outside_missing_and_changed_sets(setup, tmp_path):
    service, adapter, source = setup
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"outside")
    with pytest.raises(IPodServiceError, match="library root"):
        service.create_staging_contract(adapter.device.path, [outside], source.parent)

    missing = source.parent / "missing.mp3"
    with pytest.raises(IPodServiceError, match="missing"):
        service.create_staging_contract(adapter.device.path, [missing], source.parent)

    staged = service.create_staging_contract(adapter.device.path, [source], source.parent)
    with pytest.raises(IPodServiceError, match="staged download set changed"):
        service.create_plan(
            adapter.device.path,
            [outside],
            staging_id=staged["staging_id"],
        )


def test_safe_eject_uses_verified_selected_device(setup):
    service, adapter, _source = setup
    result = service.eject(adapter.device.path)
    assert result["ok"] is True
    assert adapter.calls[-1] == "eject"


def test_rejects_touch_shuffle_and_incomplete_identity(setup):
    service, adapter, source = setup
    adapter.device.model_family = "iPod Touch"
    with pytest.raises(IPodServiceError, match="Classic, Mini, and Nano"):
        service.create_plan(adapter.device.path, [source])
    adapter.device.model_family = "iPod Nano"
    adapter.device.serial = ""
    assert service._backup_archive_id(adapter.device) == adapter.device.firewire_guid
    with pytest.raises(IPodServiceError, match="serial and FireWire"):
        service.create_plan(adapter.device.path, [source])
    adapter.device.firewire_guid = ""
    with pytest.raises(IPodServiceError, match="serial and FireWire"):
        service.create_plan(adapter.device.path, [source])


class RestoreIncompleteError(RuntimeError):
    pass


class BackupFakeAdapter(FakeAdapter):
    def __init__(self, root: Path):
        super().__init__(root)
        self.archive_id = self.device.serial
        self.snapshot_id = "20260812T120000_000001Z"
        self.fingerprint = "a" * 64
        identity_material = (
            f"{self.device.serial.casefold()}|"
            f"{self.device.firewire_guid.casefold()}"
        )
        self.stable_device_id = hashlib.sha256(
            identity_material.encode()
        ).hexdigest()[:32]
        self.manifest = {
            "version": 3,
            "id": self.snapshot_id,
            "timestamp": "2026-08-12T12:00:00+00:00",
            "sequence": 1,
            "device_id": self.archive_id,
            "device_name": self.device.ipod_name,
            "device_meta": {
                "model_family": self.device.model_family,
                "generation": self.device.generation,
                "uses_sqlite_db": False,
                "db_version": self.device.db_version,
                "shadow_db_version": self.device.shadow_db_version,
                "hashing_scheme": self.device.hashing_scheme,
                "checksum_type": self.device.checksum_type,
                "stable_device_id": self.stable_device_id,
            },
            "identity_is_stable": True,
            "source_volume_identity_key": self.device.volume_identity_key,
            "reason": "manual",
            "note": "",
            "file_count": 1,
            "total_size": 5,
            "source_verification": "full_sha256",
            "files": {
                "iPod_Control/Music/F00/track.mp3": {
                    "hash": "b" * 64,
                    "size": 5,
                    "mtime_ns": 1,
                }
            },
        }
        self.restore_error: Exception | None = None
        self.restore_preflight_error: Exception | None = None
        self.restore_preflight_cancelled = False
        self.cancel_probe = None
        self.cancel_decision = None
        self.cancel_seen_after_commit = None

    def backup_archive_id(self, device):
        raw = str(device.serial or device.firewire_guid)
        return "".join(
            char if char.isalnum() or char in "-_" else "_"
            for char in raw
        )

    def list_backup_devices(self, _backup_root):
        return [{
            "device_id": self.archive_id,
            "device_name": self.device.ipod_name,
            "snapshot_count": 1,
            "identity_is_stable": True,
            "device_meta": self.manifest["device_meta"],
        }]

    def list_backup_snapshots(self, archive_id, _backup_root):
        assert archive_id == self.archive_id
        return [SimpleNamespace(
            id=self.snapshot_id,
            timestamp=self.manifest["timestamp"],
            device_id=archive_id,
            device_name=self.device.ipod_name,
            file_count=1,
            total_size=5,
            reason="manual",
            note=self.manifest["note"],
            files_added=0,
            files_removed=0,
            files_changed=0,
            device_meta=self.manifest["device_meta"],
            is_valid=True,
            validation_error="",
        )]

    def backup_repository_size(self, _archive_id, _backup_root):
        return 1234

    def load_backup_manifest(self, archive_id, snapshot_id, _backup_root):
        if snapshot_id != self.snapshot_id:
            raise RuntimeError("snapshot missing")
        manifest = {
            key: (
                dict(value)
                if isinstance(value, dict)
                else value
            )
            for key, value in self.manifest.items()
        }
        manifest["device_id"] = archive_id
        if archive_id != self.archive_id:
            manifest["device_meta"] = {
                **manifest["device_meta"],
                "stable_device_id": "different-source-device",
            }
        return manifest, manifest["files"], self.fingerprint

    def preflight_restore_snapshot(
        self,
        _device,
        archive_id,
        snapshot_id,
        _backup_root,
        _profile,
        progress,
        cancelled,
    ):
        assert archive_id == self.archive_id
        assert snapshot_id == self.snapshot_id
        self.calls.append("restore_preflight")
        if self.restore_preflight_error is not None:
            raise self.restore_preflight_error
        if self.restore_preflight_cancelled or (cancelled and cancelled()):
            return None
        if progress:
            progress(SimpleNamespace(
                stage="verifying",
                current=1,
                total=1,
                message="Restore preflight verified",
            ))
        return {
            "snapshot_id": snapshot_id,
            "file_count": self.manifest["file_count"],
            "unique_blobs_verified": 1,
            "verified_bytes": self.manifest["total_size"],
            "verification": "full_sha256",
            "filesystem_names_valid": True,
            "final_allocated_bytes": self.manifest["total_size"],
            "volume_total_bytes": 20_000_000_000,
            "volume_free_bytes": 10_000_000_000,
            "final_state_fits": True,
            "atomic_temp_capacity_rechecked_on_execute": True,
            "ok": True,
        }

    def restore_backup_snapshot(
        self,
        _device,
        archive_id,
        snapshot_id,
        _backup_root,
        _volume_key,
        progress,
        safety_progress,
        cancelled,
    ):
        assert archive_id == self.archive_id
        assert snapshot_id == self.snapshot_id
        self.calls.append("restore_begin")
        self.calls.append("safety_checkpoint")
        if safety_progress:
            safety_progress(SimpleNamespace(
                stage="complete",
                current=1,
                total=1,
                message="Safety checkpoint complete",
                safety_snapshot_id="20260812T120100_000001Z",
            ))
        if self.restore_error is not None:
            setattr(
                self.restore_error,
                "safety_snapshot_id",
                "20260812T120100_000001Z",
            )
            raise self.restore_error
        if progress:
            progress(SimpleNamespace(
                stage="verifying",
                current=1,
                total=1,
                message="Verified",
            ))
            progress(SimpleNamespace(
                stage="committing",
                current=0,
                total=1,
                message="Commit started",
            ))
        self.calls.append("restore_commit")
        if self.cancel_probe is not None:
            self.cancel_decision = self.cancel_probe()
            self.cancel_seen_after_commit = cancelled()
        if progress:
            progress(SimpleNamespace(
                stage="finalizing",
                current=1,
                total=1,
                message="Finalized",
            ))
        return {
            "restored": True,
            "safety_snapshot": SimpleNamespace(
                id="20260812T120100_000001Z",
                timestamp="2026-08-12T12:01:00+00:00",
                device_id=archive_id,
                device_name=self.device.ipod_name,
                file_count=1,
                total_size=5,
                reason="pre_restore_safety",
                note="",
                files_added=0,
                files_removed=0,
                files_changed=0,
                device_meta=self.manifest["device_meta"],
                is_valid=True,
                validation_error="",
            ),
        }


class MigrationFakePlan(FakePlan):
    def __init__(self, source_path: str):
        super().__init__(5, source_path)
        self.to_remove = []
        self.to_update_metadata = []
        self.fingerprint_errors = []
        self.unresolved_collisions = []
        self.unsupported = []


class MigrationFakeAdapter(BackupFakeAdapter):
    def __init__(self, root: Path):
        super().__init__(root)
        self.migration_cancel_probe = None
        self.migration_cancel_decision = None
        self.migration_cancel_seen_after_commit = None

    def deep_verify_backup_snapshot(
        self,
        archive_id,
        snapshot_id,
        _backup_root,
        progress,
        cancelled,
    ):
        assert archive_id == "OLD_SERIAL"
        assert snapshot_id == self.snapshot_id
        if cancelled and cancelled():
            return None
        if progress:
            progress(SimpleNamespace(
                stage="verifying",
                current=1,
                total=1,
                message="verified",
            ))
        return {"verified": True}

    def build_migration_bundle(
        self,
        _entries,
        _backup_root,
        bundle_dir,
        snapshot_fingerprint,
        progress,
        cancelled,
    ):
        assert not (cancelled and cancelled())
        root = Path(bundle_dir)
        media = root / "media" / "000001-source.mp3"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"audio")
        payload = {
            "schema_version": 1,
            "snapshot_fingerprint": snapshot_fingerprint,
            "tracks": [{
                "staged_path": "media/000001-source.mp3",
                "source_relative_path": "iPod_Control/Music/F00/track.mp3",
                "blob_sha256": hashlib.sha256(b"audio").hexdigest(),
                "size": 5,
                "metadata": {
                    "Title": "Migrated",
                    "Artist": "Source Artist",
                    "Album": "Source Album",
                    "rating": 80,
                    "play_count_1": 12,
                },
            }],
            "playlists": [],
            "media_file_count": 1,
            "playlist_count": 0,
            "total_media_bytes": 5,
            "limitations": {
                "preserved": ["ratings", "play counts"],
                "not_preserved": ["artwork", "skip counts"],
            },
        }
        payload["bundle_fingerprint"] = _payload_fingerprint(payload)
        (root / "bundle.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        if progress:
            progress("staging_media", 1, 1, "staged")
        return payload

    def compute_migration_plan(
        self,
        _device,
        bundle_dir,
        bundle,
        _tracks,
        _playlists,
        progress,
        cancelled,
    ):
        self.calls.append("migration_plan")
        assert bundle["snapshot_fingerprint"] == self.fingerprint
        if cancelled and cancelled():
            raise IPodServiceError("cancelled", "cancelled")
        source_path = str(Path(bundle_dir) / bundle["tracks"][0]["staged_path"])
        if progress:
            progress(SimpleNamespace(
                stage="plan",
                current=1,
                total=1,
                message="planned",
            ))
        return MigrationFakePlan(source_path)

    def execute_plan(
        self,
        _device,
        _plan,
        _generation,
        _profile,
        progress,
        cancelled,
    ):
        self.calls.append("execute")
        if progress:
            progress(SimpleNamespace(
                stage="commit",
                current=0,
                total=1,
                message="commit started",
            ))
        if self.migration_cancel_probe is not None:
            self.migration_cancel_decision = self.migration_cancel_probe()
            self.migration_cancel_seen_after_commit = cancelled()
        if progress:
            progress(SimpleNamespace(
                stage="finalizing",
                current=1,
                total=1,
                message="done",
            ))
        return {"success": True}


@pytest.fixture
def backup_setup(tmp_path):
    adapter = BackupFakeAdapter(tmp_path)
    service = IPodService(
        tmp_path / "appdata",
        adapter=adapter,
        restore_plan_ttl_seconds=60,
    )
    return service, adapter


@pytest.fixture
def migration_setup(tmp_path):
    adapter = MigrationFakeAdapter(tmp_path)
    service = IPodService(
        tmp_path / "appdata",
        adapter=adapter,
        restore_plan_ttl_seconds=60,
    )
    return service, adapter


def test_stable_device_and_archive_ids_do_not_depend_on_mount_path(backup_setup, tmp_path):
    service, adapter = backup_setup
    first = service.scan()["devices"][0]
    original_archive = service._backup_archive_id(adapter.device)
    replacement_mount = tmp_path / "remounted-ipod"
    replacement_mount.mkdir()
    adapter.device.path = str(replacement_mount)
    second = service.scan()["devices"][0]

    assert first["device_id"] == second["device_id"]
    assert original_archive == service._backup_archive_id(adapter.device)
    assert original_archive == adapter.device.serial


def test_backup_dtos_are_bounded_and_ids_reject_traversal(backup_setup):
    service, adapter = backup_setup
    devices = service.list_backup_devices()
    snapshots = service.list_backup_snapshots(adapter.archive_id, page_size=999)
    details = service.backup_snapshot_details(
        adapter.archive_id,
        adapter.snapshot_id,
    )

    assert devices["devices"][0]["archive_id"] == adapter.archive_id
    assert devices["devices"][0]["repository_size_bytes"] == 1234
    assert devices["repository_size_bytes"] == 1234
    assert snapshots["page_size"] == 100
    assert snapshots["repository_size_bytes"] == 1234
    assert snapshots["items"][0]["snapshot_id"] == adapter.snapshot_id
    assert details["scope"]["kind"] == "full_regular_file_tree"
    assert details["exclusions"]
    assert "files" not in details["snapshot"]
    with pytest.raises(IPodServiceError) as archive_error:
        service.list_backup_snapshots("../escape")
    assert archive_error.value.code == "invalid_archive_id"
    with pytest.raises(IPodServiceError) as snapshot_error:
        service.backup_snapshot_details(adapter.archive_id, "../../escape")
    assert snapshot_error.value.code == "invalid_snapshot_id"


def test_restore_preflight_binds_confirmation_target_generation_and_snapshot(
    backup_setup,
):
    service, adapter = backup_setup
    preflight = service.restore_preflight(
        adapter.device.path,
        adapter.archive_id,
        adapter.snapshot_id,
    )
    assert preflight["confirmation_required"] is True
    assert preflight["verification"] == {
        "ok": True,
        "method": "full_sha256",
        "file_count": 1,
        "unique_blobs_verified": 1,
        "verified_bytes": 5,
        "filesystem_names_valid": True,
    }
    assert preflight["storage"]["final_state_fits"] is True
    assert (
        preflight["storage"]["atomic_temp_capacity_rechecked_on_execute"]
        is True
    )

    with pytest.raises(IPodServiceError) as confirmation:
        service.restore(preflight["restore_plan_id"], confirmed=False)
    assert confirmation.value.code == "confirmation_required"

    adapter.generation = FakeGeneration(digest="changed")
    with pytest.raises(IPodServiceError) as stale_generation:
        service.restore(preflight["restore_plan_id"], confirmed=True)
    assert stale_generation.value.code == "stale_restore_plan"

    adapter.generation = FakeGeneration()
    fresh = service.restore_preflight(
        adapter.device.path,
        adapter.archive_id,
        adapter.snapshot_id,
    )
    adapter.fingerprint = "c" * 64
    with pytest.raises(IPodServiceError) as stale_snapshot:
        service.restore(fresh["restore_plan_id"], confirmed=True)
    assert stale_snapshot.value.code == "stale_restore_plan"


def test_restore_preflight_rejects_failed_or_cancelled_full_verification(
    backup_setup,
):
    service, adapter = backup_setup
    adapter.restore_preflight_error = RuntimeError("corrupt backup blob")
    with pytest.raises(IPodServiceError) as corrupt:
        service.restore_preflight(
            adapter.device.path,
            adapter.archive_id,
            adapter.snapshot_id,
        )
    assert corrupt.value.code == "restore_preflight_failed"

    adapter.restore_preflight_error = None
    adapter.restore_preflight_cancelled = True
    with pytest.raises(IPodServiceError) as cancelled:
        service.restore_preflight(
            adapter.device.path,
            adapter.archive_id,
            adapter.snapshot_id,
        )
    assert cancelled.value.code == "cancelled"


def test_restore_rejects_wrong_target_and_blocks_cancellation_after_commit(
    backup_setup,
):
    service, adapter = backup_setup
    preflight = service.restore_preflight(
        adapter.device.path,
        adapter.archive_id,
        adapter.snapshot_id,
    )
    adapter.device.serial = "DIFFERENT"
    with pytest.raises(IPodServiceError) as wrong_target:
        service.restore(preflight["restore_plan_id"], confirmed=True)
    assert wrong_target.value.code == "wrong_restore_target"

    adapter.device.serial = adapter.archive_id
    preflight = service.restore_preflight(
        adapter.device.path,
        adapter.archive_id,
        adapter.snapshot_id,
    )
    adapter.calls.clear()
    adapter.cancel_probe = service.cancel
    result = service.restore(preflight["restore_plan_id"], confirmed=True)

    assert result["ok"] is True
    assert adapter.calls == [
        "restore_begin",
        "safety_checkpoint",
        "restore_commit",
    ]
    assert adapter.cancel_decision["cancel_requested"] is False
    assert adapter.cancel_seen_after_commit is False
    state = service.recovery_state()
    assert state["operation"]["status"] == "succeeded"
    assert state["operation"]["safety_snapshot_id"]


def test_restore_incomplete_error_persists_recovery_contract(backup_setup):
    service, adapter = backup_setup
    preflight = service.restore_preflight(
        adapter.device.path,
        adapter.archive_id,
        adapter.snapshot_id,
    )
    adapter.restore_error = RestoreIncompleteError("device changed")

    with pytest.raises(IPodServiceError) as failure:
        service.restore(preflight["restore_plan_id"], confirmed=True)
    assert failure.value.code == "restore_incomplete"
    recovered = IPodService(
        service.app_data_dir,
        adapter=adapter,
    ).recovery_state()
    assert recovered["requires_recovery"] is True
    assert recovered["recovery"]["code"] == "restore_incomplete"
    assert (
        recovered["recovery"]["safety_snapshot_id"]
        == "20260812T120100_000001Z"
    )
    assert recovered["reconnect"]["required_device_id"] == adapter.stable_device_id


def test_replacement_migration_preflight_rejects_incompatible_profile(
    migration_setup,
):
    service, adapter = migration_setup
    adapter.manifest["device_meta"]["generation"] = "5th Gen"
    result = service.migration_preflight(
        adapter.device.path,
        "OLD_SERIAL",
        adapter.snapshot_id,
    )
    assert result["blocked"] is True
    assert result["compatible"] is False
    assert result["raw_restore_allowed"] is False
    assert result["safe_migration_available"] is False
    assert result["code"] == "migration_profile_incompatible"
    assert any(
        issue["field"] == "generation"
        for issue in result["issues"]
    )


def test_replacement_migration_preflight_binds_distinct_target_and_bundle(
    migration_setup,
):
    service, adapter = migration_setup
    result = service.migration_preflight(
        adapter.device.path,
        "OLD_SERIAL",
        adapter.snapshot_id,
    )

    assert result["blocked"] is False
    assert result["compatible"] is True
    assert result["safe_migration_available"] is True
    assert result["raw_restore_allowed"] is False
    assert result["confirmation_required"] is True
    assert result["target_safety_backup_required"] is True
    assert result["source"]["device_id"] != result["target"]["device_id"]
    assert result["staging_bundle"]["media_file_count"] == 1
    assert result["additions"] == 1
    assert result["removals"] == 0
    assert result["unsupported"] == 0
    assert "skip counts" in result["metadata"]["not_preserved"]


def test_replacement_migration_rejects_stale_snapshot_and_target(
    migration_setup,
):
    service, adapter = migration_setup
    first = service.migration_preflight(
        adapter.device.path,
        "OLD_SERIAL",
        adapter.snapshot_id,
    )
    adapter.fingerprint = "c" * 64
    with pytest.raises(IPodServiceError) as stale_snapshot:
        service.migration(first["migration_plan_id"], confirmed=True)
    assert stale_snapshot.value.code == "stale_migration_plan"

    adapter.fingerprint = "a" * 64
    second = service.migration_preflight(
        adapter.device.path,
        "OLD_SERIAL",
        adapter.snapshot_id,
    )
    adapter.generation = FakeGeneration(digest="changed")
    with pytest.raises(IPodServiceError) as stale_target:
        service.migration(second["migration_plan_id"], confirmed=True)
    assert stale_target.value.code == "stale_migration_plan"


def test_replacement_migration_backups_before_write_and_preserves_target_identity(
    migration_setup,
):
    service, adapter = migration_setup
    serial = adapter.device.serial
    guid = adapter.device.firewire_guid
    preflight = service.migration_preflight(
        adapter.device.path,
        "OLD_SERIAL",
        adapter.snapshot_id,
    )
    with pytest.raises(IPodServiceError) as confirmation:
        service.migration(
            preflight["migration_plan_id"],
            confirmed=False,
        )
    assert confirmation.value.code == "confirmation_required"

    adapter.calls.clear()
    adapter.migration_cancel_probe = service.cancel
    result = service.migration(
        preflight["migration_plan_id"],
        confirmed=True,
    )

    assert result["ok"] is True
    assert adapter.calls == ["migration_plan", "backup", "execute"]
    assert adapter.migration_cancel_decision["cancel_requested"] is False
    assert adapter.migration_cancel_seen_after_commit is False
    assert adapter.device.serial == serial
    assert adapter.device.firewire_guid == guid
    assert service.recovery_state()["operation"]["status"] == "succeeded"
    assert not (
        service.migration_plan_dir
        / f"{preflight['migration_plan_id']}.json"
    ).exists()
    assert not Path(preflight["staging_bundle"]["path"]).exists()


def test_replacement_migration_cancelled_backup_never_writes(migration_setup):
    service, adapter = migration_setup
    preflight = service.migration_preflight(
        adapter.device.path,
        "OLD_SERIAL",
        adapter.snapshot_id,
    )
    adapter.calls.clear()
    adapter.cancel_during_backup = True

    with pytest.raises(IPodServiceError) as failure:
        service.migration(
            preflight["migration_plan_id"],
            confirmed=True,
        )

    assert failure.value.code == "backup_failed"
    assert adapter.calls == ["migration_plan", "backup"]


def test_cli_parser_exposes_backup_and_recovery_operations():
    parser = argparse.ArgumentParser()
    add_ipod_arguments(parser)
    operation_action = next(
        action
        for action in parser._actions
        if action.dest == "ipod_operation"
    )
    assert {
        "backup-devices",
        "backup-snapshots",
        "backup-details",
        "backup-verify",
        "backup-note",
        "backup-manual",
        "backup-export",
        "backup-delete",
        "restore-preflight",
        "restore",
        "recovery-state",
        "capacity-unlock-eligibility",
        "capacity-unlock-start",
        "capacity-unlock-advance",
        "migration-preflight",
        "migration",
    }.issubset(set(operation_action.choices))


def test_real_iopenpod_backup_repository_lifecycle(tmp_path):
    from iopenpod.device.virtual import (
        available_virtual_ipod_models,
        create_virtual_ipod,
        load_virtual_ipod_info,
    )

    root = tmp_path / "virtual-backup-ipod"
    model = next(
        row["model_number"]
        for row in available_virtual_ipod_models()
        if row["model_family"] == "iPod Classic"
    )
    create_virtual_ipod(root, model, ipod_name="Backup Test")
    restore_target = root / "restore-target.bin"
    restore_target.write_bytes(b"original")

    class VirtualAdapter(IOpenPodAdapter):
        def identify_read_only(self, mount_path):
            assert os.path.normcase(os.path.realpath(mount_path)) == os.path.normcase(
                os.path.realpath(root)
            )
            return load_virtual_ipod_info(root)

    service = IPodService(tmp_path / "appdata", adapter=VirtualAdapter())
    created = service.manual_backup(str(root))
    assert created["created"] is True
    archive_id = created["archive_id"]
    snapshot_id = created["snapshot"]["snapshot_id"]

    assert service.list_backup_devices()["devices"][0]["archive_id"] == archive_id
    assert service.list_backup_snapshots(archive_id)["total"] == 1
    details = service.backup_snapshot_details(archive_id, snapshot_id)
    assert details["snapshot"]["identity_is_stable"] is True
    assert details["repository_size_bytes"] > 0
    verified = service.verify_backup_snapshot(archive_id, snapshot_id)
    assert verified["ok"] is True
    assert verified["verification"] == "full_sha256"
    note = service.update_backup_note(archive_id, snapshot_id, "Known good")
    assert note["snapshot"]["note"] == "Known good"
    restore_plan = service.restore_preflight(
        str(root),
        archive_id,
        snapshot_id,
    )
    restore_target.write_bytes(b"changed")
    try:
        restored = service.restore(
            restore_plan["restore_plan_id"],
            confirmed=True,
        )
    except IPodServiceError as exc:
        # A virtual directory may not support iOpenPod's full-volume flush.
        assert exc.code == "restore_durability_pending"
        recovery = service.recovery_state()
        assert recovery["recovery"]["content_verified"] is True
        assert recovery["recovery"]["requires_safe_eject"] is True
        safety_snapshot_id = recovery["recovery"]["safety_snapshot_id"]
    else:
        assert restored["ok"] is True
        assert restored["safety_snapshot"]["reason"] == "pre_restore_safety"
        safety_snapshot_id = restored["safety_snapshot"]["snapshot_id"]
    assert restore_target.read_bytes() == b"original"
    exported = service.export_backup_snapshot(
        archive_id,
        snapshot_id,
        tmp_path / "exports",
    )
    assert Path(exported["export"]["destination"]).is_dir()
    _manifest, entries, _fingerprint = service.adapter.load_backup_manifest(
        archive_id,
        snapshot_id,
        str(service.backup_dir),
    )
    file_hash = next(iter(entries.values()))["hash"]
    blob = service.backup_dir / "blobs" / file_hash[:2] / file_hash
    blob.write_bytes(b"corrupt")
    with pytest.raises(IPodServiceError) as corrupt:
        service.verify_backup_snapshot(archive_id, snapshot_id)
    assert corrupt.value.code == "backup_verification_failed"
    with pytest.raises(IPodServiceError) as unsafe_restore:
        service.restore_preflight(str(root), archive_id, snapshot_id)
    assert unsafe_restore.value.code == "restore_preflight_failed"
    with pytest.raises(IPodServiceError) as confirmation:
        service.delete_backup_snapshot(
            archive_id,
            snapshot_id,
            confirmed=False,
        )
    assert confirmation.value.code == "confirmation_required"
    deleted = service.delete_backup_snapshot(
        archive_id,
        snapshot_id,
        confirmed=True,
    )
    assert deleted["ok"] is True
    assert service.list_backup_snapshots(archive_id)["total"] == 1
    service.delete_backup_snapshot(
        archive_id,
        safety_snapshot_id,
        confirmed=True,
    )
    assert service.list_backup_snapshots(archive_id)["total"] == 0
