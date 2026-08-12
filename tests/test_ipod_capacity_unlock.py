from __future__ import annotations

import dataclasses
import hashlib
import struct
from pathlib import Path

import pytest

from antra.core.ipod_capacity_unlock import (
    APPLE_USB_VENDOR_ID,
    IPOD_NORMAL_USB_PRODUCT_ID,
    IPOD_RECOVERY_USB_PRODUCT_IDS,
    SUPPORTED_ORIGINAL_MODELS,
    CapacityUnlockError,
    CapacityUnlockStateMachine,
    PostflightEvidence,
    UnlockAcknowledgements,
    UnlockDeviceEvidence,
    UnlockState,
    evaluate_capacity_unlock_eligibility,
)
from antra.core.ipod_syscfg import (
    CAPACITY_UNLOCK_TAGS,
    CLASSIC_UNKNOWN1,
    CLASSIC_UNKNOWN2,
    CLASSIC_VERSION,
    ENTRY_SIZE,
    HEADER_SIZE,
    SYSCFG_MAGIC,
    CapacityUnlockPreset,
    build_capacity_unlock_candidate,
    tag_bytes,
)
from antra.core.ipod_unlock_artifacts import (
    APPLE_IPSW_SHA256,
    HELPER_BUILD_LOCK_SHA256,
    PINNED_UNLOCK_ARTIFACTS,
    ArtifactReceipt,
    HelperBuildReceipt,
)


SOURCE_FIRMWARE_ID = bytes(range(16))
SOURCE_HARDWARE_VERSION = bytes([0x11]) * 16
TARGET_FIRMWARE_ID = bytes(range(16, 32))
TARGET_HARDWARE_VERSION = bytes([0x22]) * 16
TARGET_REGION = bytes([0x33]) * 16
SYNTHETIC_FIREWIRE_GUID = "0011223344556677"


def _ascii_payload(value: str) -> bytes:
    encoded = value.encode("ascii")
    return encoded + b"\x00" * (16 - len(encoded))


def _entry(tag: str, payload: bytes) -> bytes:
    return tag_bytes(tag) + payload


def make_syscfg() -> bytes:
    entries = [
        _entry("SrNm", _ascii_payload("SYNTHETICYMV")),
        _entry("Mod#", _ascii_payload("MB147")),
        _entry("FwId", SOURCE_FIRMWARE_ID),
        _entry("HwId", bytes([0x55]) * 16),
        _entry("HwVr", SOURCE_HARDWARE_VERSION),
        _entry("SwVr", _ascii_payload("1.0")),
        _entry("Regn", bytes([0x44]) * 16),
        _entry("MLBN", _ascii_payload("NOT-A-DEVICE")),
        _entry("Test", b"opaque-synthetic"),
    ]
    size = HEADER_SIZE + ENTRY_SIZE * len(entries)
    return struct.pack(
        "<6I",
        SYSCFG_MAGIC,
        size,
        CLASSIC_UNKNOWN1,
        CLASSIC_VERSION,
        CLASSIC_UNKNOWN2,
        len(entries),
    ) + b"".join(entries)


def make_preset() -> CapacityUnlockPreset:
    return CapacityUnlockPreset(
        preset_id="synthetic-audited-v1",
        audited=True,
        audit_reference="unit-test synthetic bytes; not for hardware",
        audit_sha256="a" * 64,
        source_model_numbers=("MB147",),
        expected_source_firmware_ids=(SOURCE_FIRMWARE_ID,),
        expected_source_hardware_versions=(SOURCE_HARDWARE_VERSION,),
        expected_source_software_versions=("1.0",),
        serial_suffix="9ZU",
        target_model_number="MC297",
        target_firmware_id=TARGET_FIRMWARE_ID,
        target_hardware_version=TARGET_HARDWARE_VERSION,
        target_software_version="2.0.2",
        target_region=TARGET_REGION,
        required_changed_tags=CAPACITY_UNLOCK_TAGS,
    )


def eligible_evidence() -> UnlockDeviceEvidence:
    return UnlockDeviceEvidence(
        platform="win32",
        model_family="iPod Classic",
        generation="6th Gen",
        model_number="MB147LL/A",
        firmware_version="1.1.2",
        filesystem="FAT32",
        serial_number="SYNTHETICYMV",
        firewire_guid=SYNTHETIC_FIREWIRE_GUID,
        serial_is_stable=True,
        firewire_is_stable=True,
        identity_conflicts=(),
        writable=True,
        writable_evidence="synthetic write-read-delete probe",
        storage_healthy=True,
        health_evidence="synthetic bounded read and filesystem check",
        usb_vendor_id=APPLE_USB_VENDOR_ID,
        usb_product_id=IPOD_NORMAL_USB_PRODUCT_ID,
        is_virtual=False,
        active_device_mutation=False,
    )


def acknowledgements() -> UnlockAcknowledgements:
    return UnlockAcknowledgements(
        destructive_restore_erases_device=True,
        nor_flash_can_make_device_unbootable=True,
        manual_rockbox_nor_dfu_steps_required=True,
        hardware_recovery_may_be_required=True,
        itunes_restore_is_user_controlled=True,
        cancellation_ends_after_nor_commit=True,
    )


def artifact_receipts() -> tuple[ArtifactReceipt, ...]:
    return tuple(
        ArtifactReceipt(
            artifact_id=spec.artifact_id,
            path=f"<synthetic-{spec.artifact_id}>",
            size=spec.expected_size,
            sha1=spec.sha1,
            sha256=spec.sha256,
            metadata_sha256=spec.metadata_sha256,
        )
        for spec in PINNED_UNLOCK_ARTIFACTS.values()
    )


def helper_build_receipt() -> HelperBuildReceipt:
    return HelperBuildReceipt(
        helper_filename="vela-ipod6g-syscfg-helper.zip",
        helper_size=1024,
        helper_sha256="c" * 64,
        source_filename="vela-ipod6g-helper-corresponding-source.tar.gz",
        source_size=2048,
        source_sha256="d" * 64,
        manifest_filename="BUILD-MANIFEST.txt",
        manifest_sha256="e" * 64,
        compiler="arm-elf-eabi-gcc synthetic-test",
        lock_fingerprint=HELPER_BUILD_LOCK_SHA256,
    )


def make_machine(tmp_path: Path) -> CapacityUnlockStateMachine:
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"synthetic-session-{counter}"

    return CapacityUnlockStateMachine(
        tmp_path / "unlock-sessions.json",
        session_id_factory=next_id,
    )


def test_default_session_ids_always_start_with_a_valid_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "antra.core.ipod_capacity_unlock.secrets.token_urlsafe",
        lambda _size: "-leading-token",
    )
    machine = CapacityUnlockStateMachine(tmp_path / "unlock-sessions.json")

    session = machine.start_session(eligible_evidence())

    assert session.session_id == "unlock--leading-token"


def write_syscfg_copies(tmp_path: Path, data: bytes) -> tuple[Path, Path]:
    first_dir = tmp_path / "copy-a"
    second_dir = tmp_path / "copy-b"
    first_dir.mkdir(exist_ok=True)
    second_dir.mkdir(exist_ok=True)
    first = first_dir / "syscfg.original"
    second = second_dir / "syscfg.original"
    first.write_bytes(data)
    second.write_bytes(data)
    return first, second


def advance_to_nor_commit(
    machine: CapacityUnlockStateMachine, tmp_path: Path
):
    original = make_syscfg()
    candidate = build_capacity_unlock_candidate(
        original,
        source_model_number="MB147",
        preset=make_preset(),
    )
    session = machine.start_session(eligible_evidence())
    session = machine.acknowledge_environment(
        session.session_id, acknowledgements()
    )
    session = machine.record_filesystem_backup(
        session.session_id,
        backup_reference="synthetic-backup-reference",
        verification_sha256="b" * 64,
        verified=True,
    )
    session = machine.record_artifacts_verified(
        session.session_id, artifact_receipts()
    )
    session = machine.await_bootloader_install(session.session_id)
    session = machine.record_bootloader_installed(
        session.session_id,
        user_attested=True,
        helper_build=helper_build_receipt(),
    )
    session = machine.record_original_syscfg(
        session.session_id,
        original,
        write_syscfg_copies(tmp_path, original),
    )
    forged_bytes = bytearray(candidate.candidate_bytes)
    hwid_offset = HEADER_SIZE + 3 * ENTRY_SIZE + 4
    forged_bytes[hwid_offset] ^= 1
    forged = dataclasses.replace(
        candidate,
        candidate_bytes=bytes(forged_bytes),
        candidate_sha256=hashlib.sha256(forged_bytes).hexdigest(),
    )
    with pytest.raises(CapacityUnlockError) as forged_error:
        machine.record_candidate_syscfg(
            session.session_id,
            forged,
            original_data=original,
            preset=make_preset(),
        )
    assert forged_error.value.code == "candidate_diff_invalid"

    session = machine.record_candidate_syscfg(
        session.session_id,
        candidate,
        original_data=original,
        preset=make_preset(),
    )
    session = machine.record_candidate_staged(
        session.session_id, candidate.candidate_bytes
    )
    session = machine.await_manual_nor_flash(session.session_id)
    session = machine.attest_nor_flash(
        session.session_id,
        user_attested=True,
        reread_nor_data=candidate.candidate_bytes,
    )
    return session, candidate


def test_supported_original_model_matrix_and_expected_firmware() -> None:
    assert set(SUPPORTED_ORIGINAL_MODELS) == {
        "MB029",
        "MB147",
        "MB145",
        "MB150",
        "MB562",
        "MB565",
    }
    assert {
        model.expected_firmware
        for model in SUPPORTED_ORIGINAL_MODELS.values()
        if model.generation == "6G"
    } == {"1.1.2"}
    assert {
        model.expected_firmware
        for model in SUPPORTED_ORIGINAL_MODELS.values()
        if model.generation == "6.5G"
    } == {"2.0.1"}


def test_recovery_usb_matrix_matches_supported_classic_dfu_and_wtf_ids() -> None:
    assert IPOD_RECOVERY_USB_PRODUCT_IDS == {
        0x1223,
        0x1241,
        0x1245,
        0x1247,
        0x1250,
    }


def test_windows_fat32_stable_healthy_original_is_eligible_and_redacted() -> None:
    evidence = eligible_evidence()
    result = evaluate_capacity_unlock_eligibility(evidence)

    assert result.eligible is True
    assert result.issues == ()
    assert result.profile is not None
    assert result.profile.model_number == "MB147"
    assert result.identity_fingerprint
    assert result.firewire_fingerprint
    assert "SYNTHETICYMV" not in repr(evidence)
    assert SYNTHETIC_FIREWIRE_GUID not in repr(evidence)


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"platform": "darwin"}, "not_windows"),
        ({"model_family": "iPod Nano"}, "unsupported_family"),
        ({"model_number": "MC297"}, "unsupported_model"),
        ({"generation": "6.5G"}, "generation_mismatch"),
        ({"firmware_version": "2.0.2"}, "firmware_mismatch"),
        ({"filesystem": "NTFS"}, "filesystem_not_fat32"),
        ({"serial_is_stable": False}, "unstable_serial"),
        ({"firewire_guid": ""}, "unstable_firewire"),
        ({"identity_conflicts": ("synthetic conflict",)}, "identity_conflict"),
        ({"writable": False}, "not_writable"),
        ({"writable_evidence": ""}, "missing_writable_evidence"),
        ({"storage_healthy": False}, "storage_unhealthy"),
        ({"health_evidence": ""}, "missing_health_evidence"),
        ({"usb_product_id": 0x1241}, "usb_identity_mismatch"),
        ({"is_virtual": True}, "virtualized_environment"),
        ({"active_device_mutation": True}, "active_device_mutation"),
    ],
)
def test_eligibility_fails_closed_for_each_required_guard(
    changes: dict, expected_code: str
) -> None:
    result = evaluate_capacity_unlock_eligibility(
        dataclasses.replace(eligible_evidence(), **changes)
    )

    assert result.eligible is False
    assert expected_code in {issue.code for issue in result.issues}


def test_ineligible_device_cannot_start_a_session(tmp_path: Path) -> None:
    machine = make_machine(tmp_path)

    with pytest.raises(CapacityUnlockError) as error:
        machine.start_session(
            dataclasses.replace(eligible_evidence(), filesystem="exFAT")
        )

    assert error.value.code == "device_ineligible"
    assert not machine.state_path.exists()


def test_full_state_sequence_is_legal_persisted_and_resumable(tmp_path: Path) -> None:
    machine = make_machine(tmp_path)
    session, _candidate = advance_to_nor_commit(machine, tmp_path)
    assert session.state == UnlockState.NOR_FLASH_ATTESTED
    assert session.nor_committed is True
    assert session.can_cancel is False

    session = machine.await_dfu(session.session_id)
    session = machine.record_dfu_detected(
        session.session_id,
        usb_vendor_id=APPLE_USB_VENDOR_ID,
        usb_product_id=0x1241,
    )
    session = machine.record_itunes_handoff(
        session.session_id,
        user_attested=True,
        firmware_sha256=APPLE_IPSW_SHA256,
    )
    session = machine.attest_restore_finished(
        session.session_id, user_attested=True
    )
    session = machine.record_postflight(
        session.session_id,
        PostflightEvidence(
            firmware_version="2.0.2",
            model_number="MC297",
            filesystem="FAT32",
            firewire_guid=SYNTHETIC_FIREWIRE_GUID,
            identity_conflicts=(),
            writable=True,
            writable_evidence="synthetic postflight write probe",
            storage_healthy=True,
            health_evidence="synthetic postflight health check",
        ),
    )

    assert session.state == UnlockState.COMPLETE
    assert session.terminal is True
    assert [record.to_state for record in session.history] == [
        UnlockState.ELIGIBILITY_CHECKED.value,
        UnlockState.ENVIRONMENT_READY.value,
        UnlockState.FILESYSTEM_BACKUP_VERIFIED.value,
        UnlockState.ARTIFACTS_VERIFIED.value,
        UnlockState.AWAITING_BOOTLOADER_INSTALL.value,
        UnlockState.AWAITING_SYSCFG_DUMP.value,
        UnlockState.ORIGINAL_SYSCFG_VERIFIED.value,
        UnlockState.CANDIDATE_SYSCFG_VERIFIED.value,
        UnlockState.CANDIDATE_STAGED.value,
        UnlockState.AWAITING_MANUAL_NOR_FLASH.value,
        UnlockState.NOR_FLASH_ATTESTED.value,
        UnlockState.AWAITING_DFU.value,
        UnlockState.ITUNES_HANDOFF.value,
        UnlockState.AWAITING_RESTORE.value,
        UnlockState.POSTFLIGHT_VERIFICATION.value,
        UnlockState.COMPLETE.value,
    ]

    reloaded = CapacityUnlockStateMachine(machine.state_path).get_session(
        session.session_id
    )
    assert reloaded == session
    persisted = machine.state_path.read_text(encoding="utf-8")
    assert "SYNTHETICYMV" not in persisted
    assert SYNTHETIC_FIREWIRE_GUID not in persisted
    assert "NOT-A-DEVICE" not in persisted


def test_illegal_transition_and_stale_revision_are_rejected(tmp_path: Path) -> None:
    machine = make_machine(tmp_path)
    session = machine.start_session(eligible_evidence())

    with pytest.raises(CapacityUnlockError) as illegal:
        machine.record_filesystem_backup(
            session.session_id,
            backup_reference="synthetic-backup-reference",
            verification_sha256="b" * 64,
            verified=True,
        )
    assert illegal.value.code == "illegal_transition"

    advanced = machine.acknowledge_environment(
        session.session_id,
        acknowledgements(),
        expected_revision=session.revision,
    )
    with pytest.raises(CapacityUnlockError) as stale:
        machine.record_filesystem_backup(
            session.session_id,
            backup_reference="synthetic-backup-reference",
            verification_sha256="b" * 64,
            verified=True,
            expected_revision=session.revision,
        )
    assert stale.value.code == "stale_session"
    assert advanced.state == UnlockState.ENVIRONMENT_READY


def test_acknowledgements_and_multiple_syscfg_copies_are_mandatory(
    tmp_path: Path,
) -> None:
    machine = make_machine(tmp_path)
    session = machine.start_session(eligible_evidence())
    incomplete = dataclasses.replace(
        acknowledgements(), hardware_recovery_may_be_required=False
    )
    with pytest.raises(CapacityUnlockError) as acknowledgement_error:
        machine.acknowledge_environment(session.session_id, incomplete)
    assert acknowledgement_error.value.code == "acknowledgements_incomplete"

    session = machine.acknowledge_environment(
        session.session_id, acknowledgements()
    )
    session = machine.record_filesystem_backup(
        session.session_id,
        backup_reference="synthetic-backup-reference",
        verification_sha256="b" * 64,
        verified=True,
    )
    session = machine.record_artifacts_verified(
        session.session_id, artifact_receipts()
    )
    session = machine.await_bootloader_install(session.session_id)
    session = machine.record_bootloader_installed(
        session.session_id,
        user_attested=True,
        helper_build=helper_build_receipt(),
    )
    only_copy = tmp_path / "only-copy"
    only_copy.write_bytes(make_syscfg())

    with pytest.raises(CapacityUnlockError) as copies_error:
        machine.record_original_syscfg(
            session.session_id, make_syscfg(), (only_copy,)
        )
    assert copies_error.value.code == "insufficient_syscfg_copies"


def test_cancel_is_safe_only_before_nor_commit(tmp_path: Path) -> None:
    machine = make_machine(tmp_path)
    precommit = machine.start_session(eligible_evidence())
    cancelled = machine.cancel(precommit.session_id)
    assert cancelled.state == UnlockState.CANCELLED
    assert cancelled.can_cancel is False

    committed, _ = advance_to_nor_commit(machine, tmp_path)
    with pytest.raises(CapacityUnlockError) as error:
        machine.cancel(committed.session_id)
    assert error.value.code == "cancellation_unsafe"
    assert machine.get_session(committed.session_id).state == (
        UnlockState.NOR_FLASH_ATTESTED
    )


def test_recovery_preserves_checkpoint_and_commit_boundary(tmp_path: Path) -> None:
    machine = make_machine(tmp_path)
    precommit = machine.start_session(eligible_evidence())
    recovery = machine.require_recovery(
        precommit.session_id, reason_code="environment_interrupted"
    )
    assert recovery.state == UnlockState.RECOVERY_REQUIRED
    assert recovery.can_cancel is True
    resumed = machine.resume_recovery(recovery.session_id)
    assert resumed.state == UnlockState.ELIGIBILITY_CHECKED

    committed, _ = advance_to_nor_commit(machine, tmp_path)
    recovery = machine.require_recovery(
        committed.session_id, reason_code="dfu_not_seen"
    )
    assert recovery.state == UnlockState.RECOVERY_REQUIRED
    assert recovery.nor_committed is True
    assert recovery.can_cancel is False

    reloaded_machine = CapacityUnlockStateMachine(machine.state_path)
    reloaded = reloaded_machine.get_session(recovery.session_id)
    assert reloaded.state == UnlockState.RECOVERY_REQUIRED
    with pytest.raises(CapacityUnlockError) as cancellation:
        reloaded_machine.cancel(recovery.session_id)
    assert cancellation.value.code == "cancellation_unsafe"
    resumed = reloaded_machine.resume_recovery(recovery.session_id)
    assert resumed.state == UnlockState.NOR_FLASH_ATTESTED


def test_nor_attestation_requires_byte_identical_readback(tmp_path: Path) -> None:
    machine = make_machine(tmp_path)
    original = make_syscfg()
    candidate = build_capacity_unlock_candidate(
        original, source_model_number="MB147", preset=make_preset()
    )
    session = machine.start_session(eligible_evidence())
    session = machine.acknowledge_environment(
        session.session_id, acknowledgements()
    )
    session = machine.record_filesystem_backup(
        session.session_id,
        backup_reference="synthetic-backup-reference",
        verification_sha256=hashlib.sha256(b"backup").hexdigest(),
        verified=True,
    )
    session = machine.record_artifacts_verified(
        session.session_id, artifact_receipts()
    )
    session = machine.await_bootloader_install(session.session_id)
    session = machine.record_bootloader_installed(
        session.session_id,
        user_attested=True,
        helper_build=helper_build_receipt(),
    )
    session = machine.record_original_syscfg(
        session.session_id,
        original,
        write_syscfg_copies(tmp_path, original),
    )
    session = machine.record_candidate_syscfg(
        session.session_id,
        candidate,
        original_data=original,
        preset=make_preset(),
    )
    session = machine.record_candidate_staged(
        session.session_id, candidate.candidate_bytes
    )
    session = machine.await_manual_nor_flash(session.session_id)
    wrong_readback = bytearray(candidate.candidate_bytes)
    # A structurally valid but byte-different unknown payload must never count
    # as a successful NOR readback.
    unknown_offset = HEADER_SIZE + 8 * ENTRY_SIZE + 4
    wrong_readback[unknown_offset] ^= 1

    with pytest.raises(CapacityUnlockError) as error:
        machine.attest_nor_flash(
            session.session_id,
            user_attested=True,
            reread_nor_data=wrong_readback,
        )
    assert error.value.code == "nor_readback_mismatch"
    assert machine.get_session(session.session_id).can_cancel is True
