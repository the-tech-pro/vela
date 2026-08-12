"""Strict, side-effect-free parser and transformer for iPod Classic SysCfg.

Rockbox's S5L8702 definitions describe a 24-byte native little-endian header
followed by at most nine 20-byte entries.  Four-character tags are represented
as big-endian integer constants by Rockbox, so their bytes are reversed in the
little-endian NOR image (the logical ``SCfg`` magic is stored as ``gfCS``).

This module never reads or writes NOR.  It only validates bytes and can build a
candidate from an explicit, independently audited preset.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import struct
from typing import Any, Iterable

HEADER_SIZE = 24
ENTRY_SIZE = 20
MAX_ENTRIES = 9
MAX_SERIALIZED_SIZE = HEADER_SIZE + ENTRY_SIZE * MAX_ENTRIES

SYSCFG_MAGIC = 0x53436667  # Logical ASCII "SCfg", stored little-endian.
CLASSIC_UNKNOWN1 = 0x00000200
CLASSIC_VERSION = 0x00010001
CLASSIC_UNKNOWN2 = 0x00000000

TAG_SERIAL_NUMBER = "SrNm"
TAG_MODEL_NUMBER = "Mod#"
TAG_FIRMWARE_ID = "FwId"
TAG_HARDWARE_ID = "HwId"
TAG_HARDWARE_VERSION = "HwVr"
TAG_SOFTWARE_VERSION = "SwVr"
TAG_REGION = "Regn"
TAG_LOGIC_BOARD_SERIAL = "MLBN"
TAG_CODEC = "Codc"

CAPACITY_UNLOCK_TAGS = frozenset(
    {
        TAG_SERIAL_NUMBER,
        TAG_MODEL_NUMBER,
        TAG_FIRMWARE_ID,
        TAG_HARDWARE_VERSION,
        TAG_SOFTWARE_VERSION,
        TAG_REGION,
    }
)
CAPACITY_UNLOCK_CRITICAL_TAGS = frozenset(
    {TAG_FIRMWARE_ID, TAG_HARDWARE_VERSION}
)
IDENTIFIER_TAGS = frozenset(
    {TAG_SERIAL_NUMBER, TAG_LOGIC_BOARD_SERIAL, TAG_HARDWARE_ID}
)
ASCII_TAGS = frozenset(
    {
        TAG_SERIAL_NUMBER,
        TAG_MODEL_NUMBER,
        TAG_SOFTWARE_VERSION,
        TAG_LOGIC_BOARD_SERIAL,
        TAG_CODEC,
    }
)

_HEADER_STRUCT = struct.Struct("<6I")
_MODEL_RE = re.compile(r"^[A-Z]{2}[0-9]{3}$")
_MODEL_TEXT_RE = re.compile(r"^[A-Z]{2}[0-9]{3}(?:[A-Z]{1,3}/A)?$")
_PRESET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

OLSRO_202_AUDIT_REFERENCE = (
    "Olsro/reddit-ipod-guides:"
    "guides/ipod6g-flash-more-recent-firmwares/"
    "iPodSysCFGEditor-SourcesOnlyForDevs.7z:onTurnInto202Pod"
)
OLSRO_202_AUDIT_REVISION = "1f3d33805259c1c2b58a5076bb3580e86bacdaf1"
OLSRO_202_AUDIT_SHA256 = (
    "2e51d5e8fcf4ac36d42222869a3edb77f1527a4c34d326fb112be37b46c31017"
)
OLSRO_202_MODEL_TRANSFORMS: dict[str, tuple[str, str, str]] = {
    # source model: (source SysCfg software, serial suffix, target model prefix)
    "MB147": ("1.1.2", "9ZU", "MC297"),
    "MB150": ("1.1.2", "9ZU", "MC297"),
    "MB565": ("2.0.1", "9ZU", "MC297"),
    "MB029": ("1.1.2", "9ZS", "MC293"),
    "MB145": ("1.1.2", "9ZS", "MC293"),
    "MB562": ("2.0.1", "9ZS", "MC293"),
}


class SysCfgError(ValueError):
    """Base error with a stable, non-sensitive machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SysCfgFormatError(SysCfgError):
    """The source bytes are not a canonical Classic SysCfg image."""


class SysCfgPresetError(SysCfgError):
    """An unlock preset is absent, unaudited, or incompatible."""


class SysCfgDiffError(SysCfgError):
    """A candidate changes bytes outside its audited allowance."""


def sha256_hex(data: bytes) -> str:
    """Return a lowercase SHA-256 digest."""

    return hashlib.sha256(data).hexdigest()


def _logical_tag_from_raw(raw: bytes) -> str:
    if len(raw) != 4:
        raise SysCfgFormatError("invalid_tag_size", "A SysCfg tag must be four bytes.")
    logical = raw[::-1]
    if any(byte < 0x21 or byte > 0x7E for byte in logical):
        raise SysCfgFormatError(
            "invalid_tag_encoding", "A SysCfg tag contains non-printable bytes."
        )
    return logical.decode("ascii")


def tag_bytes(tag: str) -> bytes:
    """Encode a logical four-character tag as stored in little-endian SysCfg."""

    if not isinstance(tag, str) or len(tag) != 4:
        raise SysCfgFormatError("invalid_tag", "A logical SysCfg tag must be four characters.")
    try:
        logical = tag.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SysCfgFormatError("invalid_tag", "A logical SysCfg tag must be ASCII.") from exc
    if any(byte < 0x21 or byte > 0x7E for byte in logical):
        raise SysCfgFormatError("invalid_tag", "A logical SysCfg tag must be printable ASCII.")
    return logical[::-1]


def _validate_payload(payload: bytes, field_name: str) -> bytes:
    value = bytes(payload)
    if len(value) != 16:
        raise SysCfgPresetError(
            "invalid_preset_payload",
            f"The preset {field_name} payload must contain exactly 16 bytes.",
        )
    return value


def _encode_ascii_payload(value: str, field_name: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise SysCfgPresetError(
            "invalid_preset_text", f"The preset {field_name} value is required."
        )
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SysCfgPresetError(
            "invalid_preset_text", f"The preset {field_name} value must be ASCII."
        ) from exc
    if (
        len(encoded) > 15
        or b"\x00" in encoded
        or any(byte < 0x20 or byte > 0x7E for byte in encoded)
    ):
        raise SysCfgPresetError(
            "invalid_preset_text",
            f"The preset {field_name} value must be printable and fit a "
            "null-terminated 16-byte field.",
        )
    return encoded + b"\x00" * (16 - len(encoded))


def _decode_ascii_payload(payload: bytes, field_name: str) -> str:
    if len(payload) != 16:
        raise SysCfgFormatError(
            "invalid_entry_payload", f"The {field_name} entry payload is not 16 bytes."
        )
    terminator = payload.find(b"\x00")
    if terminator < 0:
        raise SysCfgFormatError(
            "unterminated_text", f"The {field_name} entry is not null-terminated."
        )
    if any(payload[terminator + 1 :]):
        raise SysCfgFormatError(
            "nonzero_text_padding", f"The {field_name} entry has non-zero text padding."
        )
    try:
        text = payload[:terminator].decode("ascii")
    except UnicodeDecodeError as exc:
        raise SysCfgFormatError(
            "invalid_text_encoding", f"The {field_name} entry is not ASCII."
        ) from exc
    if not text or any(ord(character) < 0x20 or ord(character) > 0x7E for character in text):
        raise SysCfgFormatError(
            "invalid_text_value", f"The {field_name} entry is empty or non-printable."
        )
    return text


def normalize_model_number(value: str) -> str:
    """Normalize an order number such as ``MB147LL/A`` to its SysCfg prefix."""

    compact = str(value or "").strip().upper().replace(" ", "")
    match = re.match(r"^([A-Z]{2}[0-9]{3})(?:[A-Z]{1,3}/A)?$", compact)
    if not match:
        raise SysCfgPresetError(
            "invalid_model_number", "The model number is not a supported Apple order number."
        )
    return match.group(1)


@dataclasses.dataclass(frozen=True, slots=True)
class SysCfgHeader:
    magic: int
    size: int
    unknown1: int
    version: int
    unknown2: int
    num_entries: int

    def to_bytes(self) -> bytes:
        return _HEADER_STRUCT.pack(
            self.magic,
            self.size,
            self.unknown1,
            self.version,
            self.unknown2,
            self.num_entries,
        )

    def to_redacted_dto(self) -> dict[str, int | str]:
        return {
            "magic": "SCfg",
            "size": self.size,
            "unknown1": self.unknown1,
            "version": self.version,
            "unknown2": self.unknown2,
            "num_entries": self.num_entries,
        }


@dataclasses.dataclass(frozen=True, slots=True, repr=False)
class SysCfgEntry:
    raw_tag: bytes
    data: bytes = dataclasses.field(repr=False)

    def __post_init__(self) -> None:
        raw_tag = bytes(self.raw_tag)
        data = bytes(self.data)
        _logical_tag_from_raw(raw_tag)
        if len(data) != 16:
            raise SysCfgFormatError(
                "invalid_entry_payload", "A SysCfg entry payload must be 16 bytes."
            )
        object.__setattr__(self, "raw_tag", raw_tag)
        object.__setattr__(self, "data", data)

    @property
    def tag(self) -> str:
        return _logical_tag_from_raw(self.raw_tag)

    def to_bytes(self) -> bytes:
        return self.raw_tag + self.data

    def with_data(self, data: bytes) -> "SysCfgEntry":
        return SysCfgEntry(self.raw_tag, bytes(data))

    def to_redacted_dto(self) -> dict[str, Any]:
        tag = self.tag
        result: dict[str, Any] = {"tag": tag, "size": len(self.data)}
        if tag in IDENTIFIER_TAGS:
            result.update({"encoding": "redacted", "value": "<redacted>"})
        elif tag in ASCII_TAGS:
            result.update(
                {
                    "encoding": "ascii",
                    "value": _decode_ascii_payload(self.data, tag),
                }
            )
        elif tag in {
            TAG_FIRMWARE_ID,
            TAG_HARDWARE_VERSION,
            TAG_REGION,
        }:
            result.update({"encoding": "hex", "value": self.data.hex()})
        else:
            # Unknown payloads may themselves contain identifiers or calibration
            # data, so DTO/log forms deliberately expose no bytes or digest.
            result.update({"encoding": "opaque", "value": "<opaque>"})
        return result

    def __repr__(self) -> str:
        value = "<redacted>" if self.tag in IDENTIFIER_TAGS else "<16 bytes>"
        return f"SysCfgEntry(tag={self.tag!r}, data={value})"


@dataclasses.dataclass(frozen=True, slots=True, repr=False)
class SysCfg:
    header: SysCfgHeader
    entries: tuple[SysCfgEntry, ...]

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        object.__setattr__(self, "entries", entries)
        _validate_structure(self.header, entries)

    def entry(self, tag: str) -> SysCfgEntry:
        for entry in self.entries:
            if entry.tag == tag:
                return entry
        raise SysCfgFormatError("missing_tag", f"The required {tag} entry is missing.")

    def to_bytes(self) -> bytes:
        _validate_structure(self.header, self.entries)
        payload = self.header.to_bytes() + b"".join(entry.to_bytes() for entry in self.entries)
        if len(payload) != self.header.size:
            raise SysCfgFormatError(
                "size_mismatch", "The serialized SysCfg size does not match its header."
            )
        return payload

    def to_redacted_dto(self) -> dict[str, Any]:
        data = self.to_bytes()
        return {
            "header": self.header.to_redacted_dto(),
            "entries": [entry.to_redacted_dto() for entry in self.entries],
            "sha256": sha256_hex(data),
        }

    def __repr__(self) -> str:
        tags = ", ".join(entry.tag for entry in self.entries)
        return f"SysCfg(entries={len(self.entries)}, tags=[{tags}])"


def _validate_structure(
    header: SysCfgHeader, entries: Iterable[SysCfgEntry]
) -> None:
    entries_tuple = tuple(entries)
    if header.magic != SYSCFG_MAGIC:
        raise SysCfgFormatError("invalid_magic", "The logical SCfg magic is missing.")
    if header.unknown1 != CLASSIC_UNKNOWN1:
        raise SysCfgFormatError(
            "invalid_classic_header", "The SysCfg header is not an iPod Classic header."
        )
    if header.version != CLASSIC_VERSION:
        raise SysCfgFormatError(
            "invalid_classic_version", "The SysCfg header version is unsupported."
        )
    if header.unknown2 != CLASSIC_UNKNOWN2:
        raise SysCfgFormatError(
            "invalid_classic_header", "The SysCfg header contains an unsupported field."
        )
    if not 1 <= header.num_entries <= MAX_ENTRIES:
        raise SysCfgFormatError(
            "invalid_entry_count", "The SysCfg entry count is outside the Classic bound."
        )
    if len(entries_tuple) != header.num_entries:
        raise SysCfgFormatError(
            "entry_count_mismatch", "The parsed entry count does not match the header."
        )
    expected_size = HEADER_SIZE + ENTRY_SIZE * header.num_entries
    if header.size != expected_size or header.size > MAX_SERIALIZED_SIZE:
        raise SysCfgFormatError(
            "size_mismatch", "The SysCfg size does not match its bounded entry count."
        )
    tags = [entry.tag for entry in entries_tuple]
    if len(tags) != len(set(tags)):
        raise SysCfgFormatError("duplicate_tag", "The SysCfg contains a duplicate tag.")
    for entry in entries_tuple:
        if entry.tag in ASCII_TAGS:
            _decode_ascii_payload(entry.data, entry.tag)


def parse_syscfg(data: bytes | bytearray | memoryview) -> SysCfg:
    """Parse and fully validate a canonical iPod Classic SysCfg file."""

    raw = bytes(data)
    if len(raw) < HEADER_SIZE:
        raise SysCfgFormatError("truncated_header", "The SysCfg header is truncated.")
    if len(raw) > MAX_SERIALIZED_SIZE:
        raise SysCfgFormatError("oversized_syscfg", "The SysCfg file exceeds the Classic bound.")

    header = SysCfgHeader(*_HEADER_STRUCT.unpack_from(raw, 0))
    if header.magic != SYSCFG_MAGIC:
        raise SysCfgFormatError("invalid_magic", "The logical SCfg magic is missing.")
    if not 1 <= header.num_entries <= MAX_ENTRIES:
        raise SysCfgFormatError(
            "invalid_entry_count", "The SysCfg entry count is outside the Classic bound."
        )
    calculated_size = HEADER_SIZE + ENTRY_SIZE * header.num_entries
    if header.size != calculated_size:
        raise SysCfgFormatError(
            "size_mismatch", "The SysCfg header size does not match its entry count."
        )
    if len(raw) != header.size:
        raise SysCfgFormatError(
            "file_size_mismatch", "The SysCfg file length does not exactly match its header."
        )

    entries = tuple(
        SysCfgEntry(
            raw[offset : offset + 4],
            raw[offset + 4 : offset + ENTRY_SIZE],
        )
        for offset in range(HEADER_SIZE, header.size, ENTRY_SIZE)
    )
    return SysCfg(header, entries)


@dataclasses.dataclass(frozen=True, slots=True, repr=False)
class CapacityUnlockPreset:
    """Explicit candidate values and source constraints from an external audit.

    No production preset is defined in this module.  A caller must supply every
    source and target byte sequence and mark it audited with a traceable digest.
    """

    preset_id: str
    audited: bool
    audit_reference: str
    audit_sha256: str
    source_model_numbers: tuple[str, ...]
    expected_source_firmware_ids: tuple[bytes, ...]
    expected_source_hardware_versions: tuple[bytes, ...]
    expected_source_software_versions: tuple[str, ...]
    serial_suffix: str
    target_model_number: str
    target_firmware_id: bytes = dataclasses.field(repr=False)
    target_hardware_version: bytes = dataclasses.field(repr=False)
    target_software_version: str
    target_region: bytes = dataclasses.field(repr=False)
    required_changed_tags: frozenset[str]
    audit_revision: str = ""
    bound_original_sha256: str | None = None

    def __post_init__(self) -> None:
        if not _PRESET_ID_RE.fullmatch(str(self.preset_id or "")):
            raise SysCfgPresetError(
                "invalid_preset_id", "The preset ID must be a stable, bounded identifier."
            )
        if not isinstance(self.audited, bool):
            raise SysCfgPresetError(
                "invalid_audit_flag", "The preset audited field must be explicit."
            )
        if not str(self.audit_reference or "").strip():
            raise SysCfgPresetError(
                "missing_audit_reference", "The preset requires an audit reference."
            )
        audit_sha256 = str(self.audit_sha256 or "").lower()
        if not _SHA256_RE.fullmatch(audit_sha256):
            raise SysCfgPresetError(
                "invalid_audit_digest", "The preset audit SHA-256 is invalid."
            )

        source_models = tuple(
            normalize_model_number(model) for model in self.source_model_numbers
        )
        if not source_models or len(source_models) != len(set(source_models)):
            raise SysCfgPresetError(
                "invalid_source_models",
                "The preset requires distinct supported source model numbers.",
            )
        source_firmware_ids = tuple(
            _validate_payload(value, "source firmware ID")
            for value in self.expected_source_firmware_ids
        )
        source_hardware_versions = tuple(
            _validate_payload(value, "source hardware version")
            for value in self.expected_source_hardware_versions
        )
        source_software_versions = tuple(
            str(value) for value in self.expected_source_software_versions
        )
        if not source_firmware_ids or not source_hardware_versions:
            raise SysCfgPresetError(
                "missing_source_profile",
                "The preset requires exact source firmware and hardware payloads.",
            )
        if (
            not source_software_versions
            or any(
                not value
                or len(value.encode("ascii", errors="ignore")) != len(value)
                or len(value) > 15
                or any(
                    ord(character) < 0x20 or ord(character) > 0x7E
                    for character in value
                )
                for value in source_software_versions
            )
        ):
            raise SysCfgPresetError(
                "invalid_source_profile",
                "The preset requires exact ASCII source software versions.",
            )

        serial_suffix = str(self.serial_suffix or "").upper()
        if len(serial_suffix) != 3 or not serial_suffix.isascii() or not serial_suffix.isalnum():
            raise SysCfgPresetError(
                "invalid_serial_suffix",
                "The audited serial suffix must be exactly three ASCII alphanumeric characters.",
            )
        target_model = str(self.target_model_number or "").strip().replace(" ", "")
        if not _MODEL_TEXT_RE.fullmatch(target_model):
            raise SysCfgPresetError(
                "invalid_target_model", "The preset target model number is invalid."
            )
        audit_revision = str(self.audit_revision or "").strip()
        bound_original_sha256 = (
            str(self.bound_original_sha256 or "").strip().lower() or None
        )
        if bound_original_sha256 is not None and not _SHA256_RE.fullmatch(
            bound_original_sha256
        ):
            raise SysCfgPresetError(
                "invalid_original_binding",
                "The preset original SysCfg SHA-256 binding is invalid.",
            )
        _encode_ascii_payload(self.target_software_version, "target software version")

        changed_tags = frozenset(self.required_changed_tags)
        if (
            not changed_tags
            or not changed_tags.issubset(CAPACITY_UNLOCK_TAGS)
            or not CAPACITY_UNLOCK_CRITICAL_TAGS.issubset(changed_tags)
        ):
            raise SysCfgPresetError(
                "invalid_change_allowance",
                "The preset must require the critical capacity-unlock tags and no others.",
            )

        object.__setattr__(self, "audit_sha256", audit_sha256)
        object.__setattr__(self, "source_model_numbers", source_models)
        object.__setattr__(self, "expected_source_firmware_ids", source_firmware_ids)
        object.__setattr__(
            self, "expected_source_hardware_versions", source_hardware_versions
        )
        object.__setattr__(
            self, "expected_source_software_versions", source_software_versions
        )
        object.__setattr__(self, "serial_suffix", serial_suffix)
        object.__setattr__(self, "target_model_number", target_model)
        object.__setattr__(
            self,
            "target_firmware_id",
            _validate_payload(self.target_firmware_id, "target firmware ID"),
        )
        object.__setattr__(
            self,
            "target_hardware_version",
            _validate_payload(self.target_hardware_version, "target hardware version"),
        )
        object.__setattr__(
            self, "target_region", _validate_payload(self.target_region, "target region")
        )
        object.__setattr__(self, "required_changed_tags", changed_tags)
        object.__setattr__(self, "audit_revision", audit_revision)
        object.__setattr__(self, "bound_original_sha256", bound_original_sha256)

    def assert_usable(self) -> None:
        if not self.audited:
            raise SysCfgPresetError(
                "unaudited_preset",
                "Capacity-unlock execution is blocked until the explicit preset is audited.",
            )

    @property
    def digest(self) -> str:
        canonical = {
            "preset_id": self.preset_id,
            "audited": self.audited,
            "audit_reference": self.audit_reference,
            "audit_revision": self.audit_revision,
            "audit_sha256": self.audit_sha256,
            "bound_original_sha256": self.bound_original_sha256,
            "source_model_numbers": list(self.source_model_numbers),
            "expected_source_firmware_ids": [
                value.hex() for value in self.expected_source_firmware_ids
            ],
            "expected_source_hardware_versions": [
                value.hex() for value in self.expected_source_hardware_versions
            ],
            "expected_source_software_versions": list(
                self.expected_source_software_versions
            ),
            "serial_suffix": self.serial_suffix,
            "target_model_number": self.target_model_number,
            "target_firmware_id": self.target_firmware_id.hex(),
            "target_hardware_version": self.target_hardware_version.hex(),
            "target_software_version": self.target_software_version,
            "target_region": self.target_region.hex(),
            "required_changed_tags": sorted(self.required_changed_tags),
        }
        return sha256_hex(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    def to_redacted_dto(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "audited": self.audited,
            "audit_reference": self.audit_reference,
            "audit_revision": self.audit_revision,
            "audit_sha256": self.audit_sha256,
            "bound_original_sha256": self.bound_original_sha256,
            "preset_sha256": self.digest,
            "source_model_numbers": list(self.source_model_numbers),
            "target_model_number": self.target_model_number,
            "serial_suffix": "<redacted>",
            "required_changed_tags": sorted(self.required_changed_tags),
        }

    def __repr__(self) -> str:
        return (
            f"CapacityUnlockPreset(preset_id={self.preset_id!r}, "
            f"audited={self.audited!r}, digest={self.digest!r})"
        )


def audited_olsro_202_preset(
    original: bytes | SysCfg,
    *,
    source_model_number: str,
) -> CapacityUnlockPreset:
    """Bind the audited Java ``onTurnInto202Pod`` transform to one SysCfg.

    The factory deliberately derives the preserved bytes from the exact input
    image.  Its SHA-256 binding prevents the resulting preset from being reused
    with another device's SysCfg, even when that image has the same model.
    """

    original_cfg = parse_syscfg(original) if not isinstance(original, SysCfg) else original
    original_bytes = original_cfg.to_bytes()
    normalized_model = normalize_model_number(source_model_number)
    model_text = _decode_ascii_payload(
        original_cfg.entry(TAG_MODEL_NUMBER).data, TAG_MODEL_NUMBER
    )
    if normalize_model_number(model_text) != normalized_model:
        raise SysCfgPresetError(
            "source_model_conflict",
            "The supplied source model does not match the SysCfg model entry.",
        )
    transform = OLSRO_202_MODEL_TRANSFORMS.get(normalized_model)
    if transform is None:
        raise SysCfgPresetError(
            "unsupported_olsro_model",
            "The audited Olsro transform does not support this source model.",
        )
    expected_software, serial_suffix, target_model_prefix = transform
    actual_software = _decode_ascii_payload(
        original_cfg.entry(TAG_SOFTWARE_VERSION).data, TAG_SOFTWARE_VERSION
    )
    if actual_software != expected_software:
        raise SysCfgPresetError(
            "wrong_source_software",
            f"The audited {normalized_model} transform requires SysCfg software "
            f"{expected_software}.",
        )

    target_firmware_id = bytearray(original_cfg.entry(TAG_FIRMWARE_ID).data)
    target_firmware_id[4:8] = struct.pack("<I", 0x21414D71)
    target_hardware_version = bytearray(
        original_cfg.entry(TAG_HARDWARE_VERSION).data
    )
    target_hardware_version[4:8] = struct.pack("<I", 0x00130200)
    target_region = bytearray(original_cfg.entry(TAG_REGION).data)
    target_region[0:4] = struct.pack("<I", 0x00020001)
    target_region[4:8] = struct.pack("<I", 0x00210020)

    original_sha256 = sha256_hex(original_bytes)
    return CapacityUnlockPreset(
        preset_id=f"olsro-202-{normalized_model.lower()}-{original_sha256[:16]}",
        audited=True,
        audit_reference=OLSRO_202_AUDIT_REFERENCE,
        audit_revision=OLSRO_202_AUDIT_REVISION,
        audit_sha256=OLSRO_202_AUDIT_SHA256,
        bound_original_sha256=original_sha256,
        source_model_numbers=(normalized_model,),
        expected_source_firmware_ids=(
            original_cfg.entry(TAG_FIRMWARE_ID).data,
        ),
        expected_source_hardware_versions=(
            original_cfg.entry(TAG_HARDWARE_VERSION).data,
        ),
        expected_source_software_versions=(expected_software,),
        serial_suffix=serial_suffix,
        target_model_number=target_model_prefix + model_text[5:],
        target_firmware_id=bytes(target_firmware_id),
        target_hardware_version=bytes(target_hardware_version),
        target_software_version="2.0.2",
        target_region=bytes(target_region),
        required_changed_tags=CAPACITY_UNLOCK_TAGS,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class CapacityUnlockDiff:
    changed_tags: tuple[str, ...]
    original_sha256: str
    candidate_sha256: str

    def to_redacted_dto(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, slots=True, repr=False)
class CapacityUnlockCandidate:
    preset_id: str
    preset_sha256: str
    source_model_number: str
    original_sha256: str
    candidate_sha256: str
    changed_tags: tuple[str, ...]
    candidate_bytes: bytes = dataclasses.field(repr=False)

    def to_redacted_dto(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "preset_sha256": self.preset_sha256,
            "source_model_number": self.source_model_number,
            "original_sha256": self.original_sha256,
            "candidate_sha256": self.candidate_sha256,
            "changed_tags": list(self.changed_tags),
            "candidate_size": len(self.candidate_bytes),
        }

    def __repr__(self) -> str:
        return (
            f"CapacityUnlockCandidate(preset_id={self.preset_id!r}, "
            f"source_model_number={self.source_model_number!r}, "
            f"changed_tags={self.changed_tags!r}, candidate_bytes=<redacted>)"
        )


def _replace_serial_suffix(payload: bytes, suffix: str) -> bytes:
    serial = _decode_ascii_payload(payload, TAG_SERIAL_NUMBER)
    suffix_bytes = suffix.encode("ascii")
    serial_bytes = serial.encode("ascii")
    if len(serial_bytes) < len(suffix_bytes):
        raise SysCfgPresetError(
            "invalid_source_serial",
            "The source serial number is too short for the audited suffix change.",
        )
    terminator = payload.index(0)
    candidate = bytearray(payload)
    candidate[terminator - len(suffix_bytes) : terminator] = suffix_bytes
    return bytes(candidate)


def _expected_target_payloads(
    original: SysCfg, preset: CapacityUnlockPreset
) -> dict[str, bytes]:
    return {
        TAG_SERIAL_NUMBER: _replace_serial_suffix(
            original.entry(TAG_SERIAL_NUMBER).data, preset.serial_suffix
        ),
        TAG_MODEL_NUMBER: _encode_ascii_payload(
            preset.target_model_number, "target model number"
        ),
        TAG_FIRMWARE_ID: preset.target_firmware_id,
        TAG_HARDWARE_VERSION: preset.target_hardware_version,
        TAG_SOFTWARE_VERSION: _encode_ascii_payload(
            preset.target_software_version, "target software version"
        ),
        TAG_REGION: preset.target_region,
    }


def _validate_source_profile(
    original: SysCfg, source_model_number: str, preset: CapacityUnlockPreset
) -> str:
    if (
        preset.bound_original_sha256 is not None
        and sha256_hex(original.to_bytes()) != preset.bound_original_sha256
    ):
        raise SysCfgPresetError(
            "original_binding_mismatch",
            "The audited preset is bound to a different original SysCfg image.",
        )
    normalized_model = normalize_model_number(source_model_number)
    source_model_entry = normalize_model_number(
        _decode_ascii_payload(original.entry(TAG_MODEL_NUMBER).data, TAG_MODEL_NUMBER)
    )
    if source_model_entry != normalized_model:
        raise SysCfgPresetError(
            "source_model_conflict",
            "The supplied source model does not match the SysCfg model entry.",
        )
    if normalized_model not in preset.source_model_numbers:
        raise SysCfgPresetError(
            "wrong_preset_model", "The audited preset does not allow this source model."
        )
    if (
        original.entry(TAG_FIRMWARE_ID).data
        not in preset.expected_source_firmware_ids
    ):
        raise SysCfgPresetError(
            "wrong_source_firmware",
            "The source firmware ID does not match the audited preset.",
        )
    if (
        original.entry(TAG_HARDWARE_VERSION).data
        not in preset.expected_source_hardware_versions
    ):
        raise SysCfgPresetError(
            "wrong_source_hardware",
            "The source hardware version does not match the audited preset.",
        )
    software_version = _decode_ascii_payload(
        original.entry(TAG_SOFTWARE_VERSION).data, TAG_SOFTWARE_VERSION
    )
    if software_version not in preset.expected_source_software_versions:
        raise SysCfgPresetError(
            "wrong_source_software",
            "The source software version does not match the audited preset.",
        )
    return normalized_model


def validate_capacity_unlock_diff(
    original: bytes | SysCfg,
    candidate: bytes | SysCfg,
    *,
    preset: CapacityUnlockPreset,
) -> CapacityUnlockDiff:
    """Prove that a candidate is the exact narrow transform in ``preset``."""

    if preset is None:
        raise SysCfgPresetError(
            "missing_preset", "An explicit audited capacity-unlock preset is required."
        )
    preset.assert_usable()
    original_cfg = parse_syscfg(original) if not isinstance(original, SysCfg) else original
    candidate_cfg = parse_syscfg(candidate) if not isinstance(candidate, SysCfg) else candidate
    original_bytes = original_cfg.to_bytes()
    candidate_bytes = candidate_cfg.to_bytes()
    if (
        preset.bound_original_sha256 is not None
        and sha256_hex(original_bytes) != preset.bound_original_sha256
    ):
        raise SysCfgDiffError(
            "original_binding_mismatch",
            "The audited preset is bound to a different original SysCfg image.",
        )

    if original_cfg.header.to_bytes() != candidate_cfg.header.to_bytes():
        raise SysCfgDiffError(
            "header_changed", "The capacity-unlock candidate changes the SysCfg header."
        )
    if len(original_cfg.entries) != len(candidate_cfg.entries):
        raise SysCfgDiffError(
            "entry_count_changed", "The capacity-unlock candidate changes the entry count."
        )

    target_payloads = _expected_target_payloads(original_cfg, preset)
    changed_tags: list[str] = []
    for original_entry, candidate_entry in zip(
        original_cfg.entries, candidate_cfg.entries
    ):
        if original_entry.raw_tag != candidate_entry.raw_tag:
            raise SysCfgDiffError(
                "tag_order_changed",
                "The capacity-unlock candidate changes tag identity or order.",
            )
        tag = original_entry.tag
        if tag not in CAPACITY_UNLOCK_TAGS:
            if original_entry.to_bytes() != candidate_entry.to_bytes():
                raise SysCfgDiffError(
                    "unapproved_change",
                    f"The capacity-unlock candidate unexpectedly changes {tag}.",
                )
            continue
        expected = target_payloads[tag]
        if candidate_entry.data != expected:
            raise SysCfgDiffError(
                "preset_value_mismatch",
                f"The capacity-unlock candidate does not match the audited {tag} value.",
            )
        if original_entry.data != candidate_entry.data:
            changed_tags.append(tag)

    changed_set = frozenset(changed_tags)
    if not preset.required_changed_tags.issubset(changed_set):
        raise SysCfgDiffError(
            "required_change_missing",
            "The candidate does not make every change required by the audited preset.",
        )
    if not changed_set.issubset(CAPACITY_UNLOCK_TAGS):
        raise SysCfgDiffError(
            "unapproved_change", "The candidate contains an unapproved change."
        )

    return CapacityUnlockDiff(
        changed_tags=tuple(changed_tags),
        original_sha256=sha256_hex(original_bytes),
        candidate_sha256=sha256_hex(candidate_bytes),
    )


def build_capacity_unlock_candidate(
    original: bytes | SysCfg,
    *,
    source_model_number: str,
    preset: CapacityUnlockPreset,
) -> CapacityUnlockCandidate:
    """Build a candidate without performing any device or filesystem writes."""

    if preset is None:
        raise SysCfgPresetError(
            "missing_preset", "An explicit audited capacity-unlock preset is required."
        )
    preset.assert_usable()
    original_cfg = parse_syscfg(original) if not isinstance(original, SysCfg) else original
    normalized_model = _validate_source_profile(
        original_cfg, source_model_number, preset
    )
    targets = _expected_target_payloads(original_cfg, preset)
    candidate_cfg = SysCfg(
        original_cfg.header,
        tuple(
            entry.with_data(targets[entry.tag])
            if entry.tag in CAPACITY_UNLOCK_TAGS
            else entry
            for entry in original_cfg.entries
        ),
    )
    diff = validate_capacity_unlock_diff(
        original_cfg, candidate_cfg, preset=preset
    )
    return CapacityUnlockCandidate(
        preset_id=preset.preset_id,
        preset_sha256=preset.digest,
        source_model_number=normalized_model,
        original_sha256=diff.original_sha256,
        candidate_sha256=diff.candidate_sha256,
        changed_tags=diff.changed_tags,
        candidate_bytes=candidate_cfg.to_bytes(),
    )
