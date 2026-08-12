from __future__ import annotations

import json
import struct

import pytest

from antra.core.ipod_syscfg import (
    CAPACITY_UNLOCK_TAGS,
    CLASSIC_UNKNOWN1,
    CLASSIC_UNKNOWN2,
    CLASSIC_VERSION,
    ENTRY_SIZE,
    HEADER_SIZE,
    MAX_ENTRIES,
    SYSCFG_MAGIC,
    CapacityUnlockPreset,
    SysCfgDiffError,
    SysCfgFormatError,
    SysCfgPresetError,
    audited_olsro_202_preset,
    build_capacity_unlock_candidate,
    parse_syscfg,
    tag_bytes,
    validate_capacity_unlock_diff,
)


SOURCE_FIRMWARE_ID = bytes(range(16))
SOURCE_HARDWARE_VERSION = bytes([0x11]) * 16
SOURCE_REGION = bytes([0x44]) * 16
TARGET_FIRMWARE_ID = bytes(range(16, 32))
TARGET_HARDWARE_VERSION = bytes([0x22]) * 16
TARGET_REGION = bytes([0x33]) * 16
UNKNOWN_PAYLOAD = b"opaque-synthetic"


def _ascii_payload(value: str) -> bytes:
    encoded = value.encode("ascii")
    return encoded + b"\x00" * (16 - len(encoded))


def _entry(tag: str, payload: bytes) -> bytes:
    assert len(payload) == 16
    return tag_bytes(tag) + payload


def make_syscfg(
    *,
    model: str = "MB147",
    software: str = "1.0",
    serial: str = "SYNTHETICYMV",
    firmware_id: bytes = SOURCE_FIRMWARE_ID,
    hardware_version: bytes = SOURCE_HARDWARE_VERSION,
    region: bytes = SOURCE_REGION,
) -> bytes:
    entries = [
        _entry("SrNm", _ascii_payload(serial)),
        _entry("Mod#", _ascii_payload(model)),
        _entry("FwId", firmware_id),
        _entry("HwId", bytes([0x55]) * 16),
        _entry("HwVr", hardware_version),
        _entry("SwVr", _ascii_payload(software)),
        _entry("Regn", region),
        _entry("MLBN", _ascii_payload("NOT-A-DEVICE")),
        _entry("Test", UNKNOWN_PAYLOAD),
    ]
    size = HEADER_SIZE + ENTRY_SIZE * len(entries)
    header = struct.pack(
        "<6I",
        SYSCFG_MAGIC,
        size,
        CLASSIC_UNKNOWN1,
        CLASSIC_VERSION,
        CLASSIC_UNKNOWN2,
        len(entries),
    )
    return header + b"".join(entries)


def make_preset(*, audited: bool = True) -> CapacityUnlockPreset:
    return CapacityUnlockPreset(
        preset_id="synthetic-audited-v1",
        audited=audited,
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


def test_little_endian_rockbox_layout_round_trips_unknown_entries() -> None:
    raw = make_syscfg()

    # Rockbox compares native little-endian uint32 values to the logical
    # big-endian constants, so SCfg and every tag are reversed on disk.
    assert raw[:4] == b"gfCS"
    assert raw[HEADER_SIZE : HEADER_SIZE + 4] == b"mNrS"

    parsed = parse_syscfg(raw)
    assert parsed.header.num_entries == MAX_ENTRIES
    assert parsed.entry("Test").data == UNKNOWN_PAYLOAD
    assert parsed.to_bytes() == raw


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda data: data.__setitem__(slice(0, 4), b"SCfg"), "invalid_magic"),
        (
            lambda data: struct.pack_into("<I", data, 4, len(data) - 1),
            "size_mismatch",
        ),
        (
            lambda data: struct.pack_into("<I", data, 20, MAX_ENTRIES + 1),
            "invalid_entry_count",
        ),
        (
            lambda data: struct.pack_into("<I", data, 8, 0),
            "invalid_classic_header",
        ),
        (
            lambda data: struct.pack_into("<I", data, 12, 2),
            "invalid_classic_version",
        ),
    ],
)
def test_corrupt_headers_and_count_fail_closed(mutator, code: str) -> None:
    corrupt = bytearray(make_syscfg())
    mutator(corrupt)

    with pytest.raises(SysCfgFormatError) as error:
        parse_syscfg(corrupt)

    assert error.value.code == code


def test_duplicate_tags_and_trailing_bytes_are_rejected() -> None:
    duplicate = bytearray(make_syscfg())
    last_entry_offset = HEADER_SIZE + (MAX_ENTRIES - 1) * ENTRY_SIZE
    duplicate[last_entry_offset : last_entry_offset + 4] = tag_bytes("SrNm")

    with pytest.raises(SysCfgFormatError, match="duplicate") as duplicate_error:
        parse_syscfg(duplicate)
    assert duplicate_error.value.code == "duplicate_tag"

    with pytest.raises(SysCfgFormatError) as trailing_error:
        parse_syscfg(make_syscfg() + b"\x00")
    assert trailing_error.value.code == "oversized_syscfg"


def test_missing_required_capacity_tag_is_rejected() -> None:
    missing_firmware = bytearray(make_syscfg())
    firmware_tag_offset = HEADER_SIZE + 2 * ENTRY_SIZE
    missing_firmware[firmware_tag_offset : firmware_tag_offset + 4] = tag_bytes(
        "Gone"
    )

    with pytest.raises(SysCfgFormatError) as missing:
        build_capacity_unlock_candidate(
            missing_firmware,
            source_model_number="MB147",
            preset=make_preset(),
        )

    assert missing.value.code == "missing_tag"


def test_dto_and_repr_redact_identifiers_and_unknown_payloads() -> None:
    parsed = parse_syscfg(make_syscfg())

    rendered = json.dumps(parsed.to_redacted_dto(), sort_keys=True)
    representation = repr(parsed.entry("SrNm"))

    assert "SYNTHETICYMV" not in rendered
    assert "NOT-A-DEVICE" not in rendered
    assert UNKNOWN_PAYLOAD.hex() not in rendered
    assert "<redacted>" in rendered
    assert "SYNTHETICYMV" not in representation


def test_explicit_audited_preset_builds_a_narrow_candidate() -> None:
    original = make_syscfg()
    preset = make_preset()

    result = build_capacity_unlock_candidate(
        original,
        source_model_number="MB147LL/A",
        preset=preset,
    )
    candidate = parse_syscfg(result.candidate_bytes)
    source = parse_syscfg(original)

    assert set(result.changed_tags) == set(CAPACITY_UNLOCK_TAGS)
    assert result.original_sha256 != result.candidate_sha256
    assert candidate.entry("SrNm").data.startswith(b"SYNTHETIC")
    assert candidate.entry("SrNm").data.split(b"\x00", 1)[0].endswith(b"9ZU")
    assert candidate.entry("Mod#").data.startswith(b"MC297\x00")
    assert candidate.entry("FwId").data == TARGET_FIRMWARE_ID
    assert candidate.entry("HwVr").data == TARGET_HARDWARE_VERSION
    assert candidate.entry("SwVr").data.startswith(b"2.0.2\x00")
    assert candidate.entry("Regn").data == TARGET_REGION
    assert candidate.entry("HwId").to_bytes() == source.entry("HwId").to_bytes()
    assert candidate.entry("MLBN").to_bytes() == source.entry("MLBN").to_bytes()
    assert candidate.entry("Test").to_bytes() == source.entry("Test").to_bytes()

    diff = validate_capacity_unlock_diff(
        original, result.candidate_bytes, preset=preset
    )
    assert diff.candidate_sha256 == result.candidate_sha256


def test_missing_or_unaudited_preset_blocks_candidate_execution() -> None:
    with pytest.raises(SysCfgPresetError) as missing:
        build_capacity_unlock_candidate(
            make_syscfg(), source_model_number="MB147", preset=None
        )
    assert missing.value.code == "missing_preset"

    with pytest.raises(SysCfgPresetError) as unaudited:
        build_capacity_unlock_candidate(
            make_syscfg(),
            source_model_number="MB147",
            preset=make_preset(audited=False),
        )
    assert unaudited.value.code == "unaudited_preset"


def test_wrong_model_or_source_profile_rejects_the_preset() -> None:
    with pytest.raises(SysCfgPresetError) as wrong_model:
        build_capacity_unlock_candidate(
            make_syscfg(), source_model_number="MB565", preset=make_preset()
        )
    assert wrong_model.value.code == "source_model_conflict"

    wrong_hardware = bytearray(make_syscfg())
    hwvr_offset = HEADER_SIZE + 4 * ENTRY_SIZE + 4
    wrong_hardware[hwvr_offset : hwvr_offset + 16] = bytes([0x99]) * 16
    with pytest.raises(SysCfgPresetError) as wrong_profile:
        build_capacity_unlock_candidate(
            wrong_hardware, source_model_number="MB147", preset=make_preset()
        )
    assert wrong_profile.value.code == "wrong_source_hardware"


def test_diff_validation_rejects_unexpected_or_inexact_changes() -> None:
    preset = make_preset()
    built = build_capacity_unlock_candidate(
        make_syscfg(), source_model_number="MB147", preset=preset
    )

    unapproved = bytearray(built.candidate_bytes)
    hwid_data_offset = HEADER_SIZE + 3 * ENTRY_SIZE + 4
    unapproved[hwid_data_offset] ^= 0x01
    with pytest.raises(SysCfgDiffError) as unexpected:
        validate_capacity_unlock_diff(make_syscfg(), unapproved, preset=preset)
    assert unexpected.value.code == "unapproved_change"

    wrong_target = bytearray(built.candidate_bytes)
    firmware_data_offset = HEADER_SIZE + 2 * ENTRY_SIZE + 4
    wrong_target[firmware_data_offset] ^= 0x01
    with pytest.raises(SysCfgDiffError) as wrong:
        validate_capacity_unlock_diff(make_syscfg(), wrong_target, preset=preset)
    assert wrong.value.code == "preset_value_mismatch"


@pytest.mark.parametrize(
    ("model", "software", "serial_suffix", "target_model"),
    [
        ("MB147", "1.1.2", "9ZU", "MC297"),
        ("MB150", "1.1.2", "9ZU", "MC297"),
        ("MB565", "2.0.1", "9ZU", "MC297"),
        ("MB029", "1.1.2", "9ZS", "MC293"),
        ("MB145", "1.1.2", "9ZS", "MC293"),
        ("MB562", "2.0.1", "9ZS", "MC293"),
    ],
)
def test_audited_olsro_factory_maps_every_supported_model_and_color(
    model: str,
    software: str,
    serial_suffix: str,
    target_model: str,
) -> None:
    original = make_syscfg(
        model=f"{model}LL/A",
        software=software,
        serial="SYNTHETIC000",
    )
    preset = audited_olsro_202_preset(
        original,
        source_model_number=model,
    )
    result = build_capacity_unlock_candidate(
        original,
        source_model_number=model,
        preset=preset,
    )
    candidate = parse_syscfg(result.candidate_bytes)

    assert candidate.entry("SrNm").data == _ascii_payload(
        f"SYNTHETIC{serial_suffix}"
    )
    assert candidate.entry("Mod#").data == _ascii_payload(f"{target_model}LL/A")
    assert preset.bound_original_sha256 == result.original_sha256
    assert preset.audit_revision == "1f3d33805259c1c2b58a5076bb3580e86bacdaf1"


def test_audited_olsro_factory_matches_java_transform_byte_for_byte() -> None:
    firmware = bytes.fromhex("10111213202122233031323340414243")
    hardware = bytes.fromhex("50515253606162637071727380818283")
    region = bytes.fromhex("90919293a0a1a2a3b0b1b2b3c0c1c2c3")
    original = make_syscfg(
        model="MB147LL/A",
        software="1.1.2",
        serial="SYNTHETIC000",
        firmware_id=firmware,
        hardware_version=hardware,
        region=region,
    )
    preset = audited_olsro_202_preset(
        original,
        source_model_number="MB147LL/A",
    )
    result = build_capacity_unlock_candidate(
        original,
        source_model_number="MB147",
        preset=preset,
    )
    source = parse_syscfg(original)
    candidate = parse_syscfg(result.candidate_bytes)

    expected_firmware = bytearray(firmware)
    expected_firmware[4:8] = struct.pack("<I", 0x21414D71)
    expected_hardware = bytearray(hardware)
    expected_hardware[4:8] = struct.pack("<I", 0x00130200)
    expected_region = bytearray(region)
    expected_region[:4] = struct.pack("<I", 0x00020001)
    expected_region[4:8] = struct.pack("<I", 0x00210020)

    assert candidate.entry("FwId").data == bytes(expected_firmware)
    assert candidate.entry("HwVr").data == bytes(expected_hardware)
    assert candidate.entry("Regn").data == bytes(expected_region)
    assert candidate.entry("FwId").data[:4] == source.entry("FwId").data[:4]
    assert candidate.entry("FwId").data[8:] == source.entry("FwId").data[8:]
    assert candidate.entry("HwVr").data[:4] == source.entry("HwVr").data[:4]
    assert candidate.entry("HwVr").data[8:] == source.entry("HwVr").data[8:]
    assert candidate.entry("Regn").data[8:] == source.entry("Regn").data[8:]


@pytest.mark.parametrize(
    ("model", "software", "code"),
    [
        ("MB147", "2.0.5", "wrong_source_software"),
        ("ZZ999", "1.1.2", "unsupported_olsro_model"),
    ],
)
def test_audited_olsro_factory_rejects_wrong_software_and_unknown_models(
    model: str,
    software: str,
    code: str,
) -> None:
    original = make_syscfg(model=model, software=software)
    with pytest.raises(SysCfgPresetError) as error:
        audited_olsro_202_preset(original, source_model_number=model)
    assert error.value.code == code


def test_audited_olsro_preset_cannot_be_reused_with_another_original() -> None:
    original = make_syscfg(software="1.1.2", serial="SYNTHETIC000")
    preset = audited_olsro_202_preset(original, source_model_number="MB147")
    other = make_syscfg(software="1.1.2", serial="SYNTHETIC111")

    with pytest.raises(SysCfgPresetError) as error:
        build_capacity_unlock_candidate(
            other,
            source_model_number="MB147",
            preset=preset,
        )
    assert error.value.code == "original_binding_mismatch"
