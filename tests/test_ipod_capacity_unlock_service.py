from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from antra.core import ipod_cli
from antra.core.ipod_cli import run_ipod_command
from antra.core.ipod_service import (
    UNLOCK_ACKNOWLEDGEMENT_FIELDS,
    UNLOCK_ACTIONS,
    IPodService,
    IPodServiceError,
)
from antra.core.ipod_syscfg import (
    CLASSIC_UNKNOWN1,
    CLASSIC_UNKNOWN2,
    CLASSIC_VERSION,
    HEADER_SIZE,
    SYSCFG_MAGIC,
    tag_bytes,
)
from antra.core.ipod_unlock_artifacts import (
    HELPER_BUILD_LOCK_SHA256,
    ArtifactReceipt,
    HelperBuildReceipt,
)


def _ascii(value: str) -> bytes:
    raw = value.encode("ascii")
    return raw + b"\x00" * (16 - len(raw))


def _entry(tag: str, payload: bytes) -> bytes:
    return tag_bytes(tag) + payload


def make_original_syscfg() -> bytes:
    entries = [
        _entry("SrNm", _ascii("SYNTHETIC000")),
        _entry("Mod#", _ascii("MB145LL/A")),
        _entry("FwId", bytes(range(16))),
        _entry("HwId", b"\x55" * 16),
        _entry("HwVr", b"\x66" * 16),
        _entry("SwVr", _ascii("1.1.2")),
        _entry("Regn", b"\x77" * 16),
    ]
    size = HEADER_SIZE + 20 * len(entries)
    return struct.pack(
        "<6I",
        SYSCFG_MAGIC,
        size,
        CLASSIC_UNKNOWN1,
        CLASSIC_VERSION,
        CLASSIC_UNKNOWN2,
        len(entries),
    ) + b"".join(entries)


class FakeDownloader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def download(self, spec, destination, **kwargs):
        assert kwargs["explicit_user_action"] is True
        assert not Path(destination).exists()
        self.calls.append(spec.artifact_id)
        return ArtifactReceipt(
            artifact_id=spec.artifact_id,
            path=str(destination),
            size=spec.expected_size,
            sha1=spec.sha1,
            sha256=spec.sha256,
            metadata_sha256=spec.metadata_sha256,
        )


class UnlockAdapter:
    def __init__(self, root: Path) -> None:
        mount = root / "ipod"
        mount.mkdir()
        self.device = SimpleNamespace(
            path=str(mount),
            ipod_name="Synthetic Classic",
            display_name="Synthetic Classic",
            model_family="iPod Classic",
            generation="6G",
            model_number="MB145",
            serial="SYNTHETIC01",
            firewire_guid="0011223344556677",
            firmware="1.1.2",
            usb_vid=0x05AC,
            usb_pid=0x1261,
            identity_conflicts=[],
            filesystem_type="fat32",
            reported_volume_format="FAT32",
            volume_identity_key="windows|disk|volume|mount",
            disk_size_gb=160.0,
            free_space_gb=120.0,
            uses_sqlite_db=False,
            checksum_type=1,
            _field_sources={
                "serial": "ioctl",
                "firewire_guid": "windows_device_tree",
                "model_number": "sysinfo",
                "firmware": "ioctl",
                "usb_vid": "windows_device_tree",
                "usb_pid": "windows_device_tree",
            },
        )
        self.profile = SimpleNamespace(
            safe_for_writes=True,
            filesystem_type="fat32",
            identity=SimpleNamespace(
                operating_system="windows",
                is_complete=True,
            ),
        )
        self.generation = {
            "filename": "iTunesDB",
            "exists": True,
            "size": 128,
            "modified_ns": 1,
            "device": 2,
            "inode": 3,
            "digest": "d" * 64,
        }
        self.snapshot_id = "unlock-snapshot"

    def identify_read_only(self, mount_path):
        if Path(mount_path).resolve() != Path(self.device.path).resolve():
            raise IPodServiceError("device_not_found", "The iPod was not found.")
        return self.device

    def inspect_write_readiness(self, _device):
        return self.profile

    def capture_database_generation(self, _mount_path):
        return self.generation

    def volume_key(self, _profile):
        return self.device.volume_identity_key

    def backup_archive_id(self, _device):
        return "SYNTHETIC01"

    def create_manual_backup(
        self, _device, _backup_root, _volume_key, progress, cancelled
    ):
        assert not (cancelled and cancelled())
        return SimpleNamespace(
            id=self.snapshot_id,
            timestamp="2026-08-12T12:00:00Z",
            device_id="SYNTHETIC01",
            reason="manual",
            file_count=1,
            total_size=128,
            identity_is_stable=True,
            device_meta={},
        )

    create_backup = create_manual_backup

    def deep_verify_backup_snapshot(
        self, archive_id, snapshot_id, _root, _progress, _cancelled
    ):
        assert archive_id == "SYNTHETIC01"
        assert snapshot_id == self.snapshot_id
        return {
            "snapshot_id": snapshot_id,
            "ok": True,
            "verification": "full_sha256",
        }

    def load_backup_manifest(self, archive_id, snapshot_id, _root):
        return (
            {"id": snapshot_id, "device_id": archive_id, "files": {}},
            {},
            "a" * 64,
        )


def acknowledgements() -> dict[str, bool]:
    return {field: True for field in UNLOCK_ACKNOWLEDGEMENT_FIELDS}


def test_unlock_action_surface_is_exact_and_bounded() -> None:
    assert UNLOCK_ACTIONS == {
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
    }


def test_eligibility_fails_closed_without_usb_and_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = UnlockAdapter(tmp_path)
    adapter.device.usb_vid = 0
    adapter.device.usb_pid = 0
    adapter.device._field_sources = {}
    service = IPodService(tmp_path / "app", adapter=adapter)
    monkeypatch.setattr("antra.core.ipod_service.sys.platform", "win32")

    result = service.capacity_unlock_eligibility(adapter.device.path)
    codes = {issue["code"] for issue in result["eligibility"]["issues"]}

    assert result["experimental"] is True
    assert result["eligibility"]["eligible"] is False
    assert {"unstable_serial", "unstable_firewire", "usb_identity_mismatch"} <= codes
    assert result["actions"] == sorted(UNLOCK_ACTIONS)


def test_start_requires_every_acknowledgement(tmp_path: Path, monkeypatch) -> None:
    adapter = UnlockAdapter(tmp_path)
    service = IPodService(tmp_path / "app", adapter=adapter)
    monkeypatch.setattr("antra.core.ipod_service.sys.platform", "win32")

    with pytest.raises(IPodServiceError) as error:
        service.start_capacity_unlock(adapter.device.path, True, {})
    assert error.value.code == "acknowledgements_incomplete"


def test_backup_metadata_includes_recovery_identity_and_media_fields(
    tmp_path: Path,
) -> None:
    adapter = UnlockAdapter(tmp_path)
    service = IPodService(tmp_path / "app", adapter=adapter)

    metadata = service._backup_device_meta(adapter.device)

    assert {
        "firmware",
        "filesystem_type",
        "reported_volume_format",
        "capacity",
        "disk_size_gb",
        "free_space_gb",
        "uses_sqlite_db",
        "db_version",
        "shadow_db_version",
        "hashing_scheme",
        "checksum_type",
        "audio_codecs",
        "podcasts_supported",
        "voice_memos_supported",
        "supports_sparse_artwork",
        "photos_supported",
        "videos_supported",
    } <= set(metadata)


def test_full_service_action_sequence_is_bounded_and_never_downloads_network(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = UnlockAdapter(tmp_path)
    downloader = FakeDownloader()
    service = IPodService(
        tmp_path / "app",
        adapter=adapter,
        artifact_downloader=downloader,
    )
    monkeypatch.setattr("antra.core.ipod_service.sys.platform", "win32")
    started = service.start_capacity_unlock(
        adapter.device.path,
        True,
        acknowledgements(),
    )
    session_id = started["session"]["session_id"]
    revision = started["session"]["revision"]
    status = service.advance_capacity_unlock(session_id, "status", False, {})
    listed = service.advance_capacity_unlock("", "list", False, {})
    assert status["session"]["state"] == "environment_ready"
    assert [item["session_id"] for item in listed["sessions"]] == [session_id]

    def advance(action: str, **data):
        nonlocal revision
        result = service.advance_capacity_unlock(
            session_id,
            action,
            True,
            {"expected_revision": revision, **data},
        )
        revision = result["session"]["revision"]
        return result

    advance("recovery", reason_code="test_recovery")
    advance("resume")
    advance("backup", mount_path=adapter.device.path)
    advance(
        "artifacts",
        artifacts={
            artifact["artifact_id"]: {"mode": "download"}
            for artifact in service.capacity_unlock_eligibility(
                adapter.device.path
            )["artifacts"]
        },
    )
    assert len(downloader.calls) == 5
    advance("bootloader-await")
    helper_output = tmp_path / "helper-output"
    helper_output.mkdir()
    helper_path = helper_output / "vela-ipod6g-syscfg-helper.zip"
    helper_source_path = (
        helper_output / "vela-ipod6g-helper-corresponding-source.tar.gz"
    )
    helper_manifest_path = helper_output / "BUILD-MANIFEST.txt"
    helper_path.write_bytes(b"synthetic helper")
    helper_source_path.write_bytes(b"synthetic source")
    helper_manifest_path.write_text("synthetic manifest\n", encoding="utf-8")
    monkeypatch.setattr(
        "antra.core.ipod_service.validate_rockbox_helper_build",
        lambda *_args, **_kwargs: HelperBuildReceipt(
            helper_filename=helper_path.name,
            helper_size=helper_path.stat().st_size,
            helper_sha256="b" * 64,
            source_filename=helper_source_path.name,
            source_size=helper_source_path.stat().st_size,
            source_sha256="c" * 64,
            manifest_filename=helper_manifest_path.name,
            manifest_sha256="d" * 64,
            compiler="arm-elf-eabi-gcc synthetic-test",
            lock_fingerprint=HELPER_BUILD_LOCK_SHA256,
        ),
    )
    advance(
        "bootloader-installed",
        user_attested=True,
        helper_path=str(helper_path),
        source_path=str(helper_source_path),
        manifest_path=str(helper_manifest_path),
        selected_directories=[str(helper_output)],
    )

    original = make_original_syscfg()
    selected = tmp_path / "selected"
    copy_one_dir = tmp_path / "copy-one"
    copy_two_dir = tmp_path / "copy-two"
    selected.mkdir()
    copy_one_dir.mkdir()
    copy_two_dir.mkdir()
    original_path = selected / "original.bin"
    copy_one = copy_one_dir / "original.bin"
    copy_two = copy_two_dir / "original.bin"
    original_path.write_bytes(original)
    copy_one.write_bytes(original)
    copy_two.write_bytes(original)
    selected_directories = [
        str(selected),
        str(copy_one_dir),
        str(copy_two_dir),
    ]
    advance(
        "syscfg-original",
        source_path=str(original_path),
        backup_paths=[str(copy_one), str(copy_two)],
        selected_directories=selected_directories,
    )
    candidate_result = advance(
        "syscfg-candidate",
        original_path=str(original_path),
        selected_directories=selected_directories,
    )
    candidate_path = candidate_result["candidate"]["path"]
    assert Path(candidate_path).is_file()
    assert Path(candidate_path).is_relative_to(
        service.capacity_unlock_session_dir.resolve()
    )
    advance("syscfg-stage", staged_path=candidate_path)
    advance("nor-await")
    advance("nor-attested", user_attested=True, readback_path=candidate_path)
    advance("dfu-await")
    advance("dfu-detected", usb_vendor_id=0x05AC, usb_product_id=0x1223)
    advance(
        "itunes-handoff",
        user_attested=True,
        firmware_sha256=next(
            artifact["sha256"]
            for artifact in service.capacity_unlock_eligibility(
                adapter.device.path
            )["artifacts"]
            if artifact["artifact_id"] == "apple-ipod-classic-2.0.2-ipsw"
        ),
    )
    advance("restore-finished", user_attested=True)
    adapter.device.model_number = "MC293"
    adapter.device.firmware = "2.0.2"
    completed = advance("postflight", mount_path=adapter.device.path)
    assert completed["session"]["state"] == "complete"
    assert completed["session"]["nor_committed"] is True


def test_service_dispatches_safe_pre_nor_cancel(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = UnlockAdapter(tmp_path)
    service = IPodService(tmp_path / "app", adapter=adapter)
    monkeypatch.setattr("antra.core.ipod_service.sys.platform", "win32")
    started = service.start_capacity_unlock(
        adapter.device.path,
        True,
        acknowledgements(),
    )

    result = service.advance_capacity_unlock(
        started["session"]["session_id"],
        "cancel",
        True,
        {"expected_revision": started["session"]["revision"]},
    )

    assert result["session"]["state"] == "cancelled"
    assert result["session"]["nor_committed"] is False


def test_unlock_input_path_must_be_under_explicit_selection(
    tmp_path: Path,
) -> None:
    adapter = UnlockAdapter(tmp_path)
    service = IPodService(tmp_path / "app", adapter=adapter)
    selected = tmp_path / "selected"
    selected.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"not allowed")

    with pytest.raises(IPodServiceError) as error:
        service._unlock_selected_file(
            outside,
            {"selected_directories": [str(selected)]},
            "",
        )
    assert error.value.code == "unlock_path_outside_selection"


@pytest.mark.parametrize(
    ("operation", "event_type"),
    [
        ("capacity-unlock-eligibility", "ipod_capacity_unlock_eligibility"),
        ("capacity-unlock-start", "ipod_capacity_unlock_start"),
        ("capacity-unlock-advance", "ipod_capacity_unlock_advance"),
    ],
)
def test_cli_routes_capacity_unlock_envelopes(
    tmp_path: Path,
    monkeypatch,
    capsys,
    operation: str,
    event_type: str,
) -> None:
    class FakeService:
        def __init__(self, _app_data):
            pass

        def capacity_unlock_eligibility(self, _mount):
            return {"experimental": True}

        def start_capacity_unlock(self, _mount, _confirmed, _acknowledgements):
            return {"experimental": True}

        def advance_capacity_unlock(self, *_args, **_kwargs):
            return {"experimental": True}

    request = tmp_path / f"{operation}.json"
    request.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "mount_path": "X:/",
                "confirmed": True,
                "acknowledgements": acknowledgements(),
                "session_id": "session-1",
                "action": "status",
                "data": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ipod_cli, "IPodService", FakeService)
    args = argparse.Namespace(
        ipod_request=str(request),
        ipod_app_data=str(tmp_path / "app"),
        ipod_operation=operation,
        ipod_cancel_file="",
        config="",
    )

    assert run_ipod_command(args) == 0
    event = json.loads(capsys.readouterr().out)
    assert event["type"] == event_type
    assert event["data"]["experimental"] is True
