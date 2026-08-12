"""Windows-only eligibility and persisted state for Classic capacity unlock.

The coordinator deliberately has no NOR writer, formatter, process launcher,
or iTunes automation.  It validates evidence and records explicit human
handoffs so an interrupted workflow can resume safely.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import threading
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from .ipod_syscfg import (
    CapacityUnlockCandidate,
    CapacityUnlockPreset,
    TAG_MODEL_NUMBER,
    parse_syscfg,
    validate_capacity_unlock_diff,
)
from .ipod_unlock_artifacts import (
    PINNED_UNLOCK_ARTIFACTS,
    REQUIRED_UNLOCK_ARTIFACT_IDS,
    ArtifactReceipt,
    HelperBuildReceipt,
)

STATE_SCHEMA_VERSION = 1
TARGET_FIRMWARE_VERSION = "2.0.2"
APPLE_USB_VENDOR_ID = 0x05AC
IPOD_NORMAL_USB_PRODUCT_ID = 0x1261
IPOD_RECOVERY_USB_PRODUCT_IDS = frozenset(
    {0x1223, 0x1241, 0x1245, 0x1247, 0x1250}
)
MAX_STATE_FILE_BYTES = 2 * 1024 * 1024
MAX_SESSIONS = 100

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_FIREWIRE_RE = re.compile(r"^[0-9A-F]{16}$")
_SERIAL_RE = re.compile(r"^[A-Z0-9]{8,16}$")


class CapacityUnlockError(RuntimeError):
    """Safe user-displayable failure with a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class UnlockState(str, Enum):
    ELIGIBILITY_CHECKED = "eligibility_checked"
    ENVIRONMENT_READY = "environment_ready"
    FILESYSTEM_BACKUP_VERIFIED = "filesystem_backup_verified"
    ARTIFACTS_VERIFIED = "artifacts_verified"
    AWAITING_BOOTLOADER_INSTALL = "awaiting_bootloader_install"
    AWAITING_SYSCFG_DUMP = "awaiting_syscfg_dump"
    ORIGINAL_SYSCFG_VERIFIED = "original_syscfg_verified"
    CANDIDATE_SYSCFG_VERIFIED = "candidate_syscfg_verified"
    CANDIDATE_STAGED = "candidate_staged"
    AWAITING_MANUAL_NOR_FLASH = "awaiting_manual_nor_flash"
    NOR_FLASH_ATTESTED = "nor_flash_attested"
    AWAITING_DFU = "awaiting_dfu"
    ITUNES_HANDOFF = "itunes_handoff"
    AWAITING_RESTORE = "awaiting_restore"
    POSTFLIGHT_VERIFICATION = "postflight_verification"
    COMPLETE = "complete"
    RECOVERY_REQUIRED = "recovery_required"
    CANCELLED = "cancelled"


_NORMAL_TRANSITIONS: Mapping[UnlockState, UnlockState] = MappingProxyType(
    {
        UnlockState.ELIGIBILITY_CHECKED: UnlockState.ENVIRONMENT_READY,
        UnlockState.ENVIRONMENT_READY: UnlockState.FILESYSTEM_BACKUP_VERIFIED,
        UnlockState.FILESYSTEM_BACKUP_VERIFIED: UnlockState.ARTIFACTS_VERIFIED,
        UnlockState.ARTIFACTS_VERIFIED: UnlockState.AWAITING_BOOTLOADER_INSTALL,
        UnlockState.AWAITING_BOOTLOADER_INSTALL: UnlockState.AWAITING_SYSCFG_DUMP,
        UnlockState.AWAITING_SYSCFG_DUMP: UnlockState.ORIGINAL_SYSCFG_VERIFIED,
        UnlockState.ORIGINAL_SYSCFG_VERIFIED: UnlockState.CANDIDATE_SYSCFG_VERIFIED,
        UnlockState.CANDIDATE_SYSCFG_VERIFIED: UnlockState.CANDIDATE_STAGED,
        UnlockState.CANDIDATE_STAGED: UnlockState.AWAITING_MANUAL_NOR_FLASH,
        UnlockState.AWAITING_MANUAL_NOR_FLASH: UnlockState.NOR_FLASH_ATTESTED,
        UnlockState.NOR_FLASH_ATTESTED: UnlockState.AWAITING_DFU,
        UnlockState.AWAITING_DFU: UnlockState.ITUNES_HANDOFF,
        UnlockState.ITUNES_HANDOFF: UnlockState.AWAITING_RESTORE,
        UnlockState.AWAITING_RESTORE: UnlockState.POSTFLIGHT_VERIFICATION,
        UnlockState.POSTFLIGHT_VERIFICATION: UnlockState.COMPLETE,
    }
)

_PRE_NOR_STATES = frozenset(
    {
        UnlockState.ELIGIBILITY_CHECKED,
        UnlockState.ENVIRONMENT_READY,
        UnlockState.FILESYSTEM_BACKUP_VERIFIED,
        UnlockState.ARTIFACTS_VERIFIED,
        UnlockState.AWAITING_BOOTLOADER_INSTALL,
        UnlockState.AWAITING_SYSCFG_DUMP,
        UnlockState.ORIGINAL_SYSCFG_VERIFIED,
        UnlockState.CANDIDATE_SYSCFG_VERIFIED,
        UnlockState.CANDIDATE_STAGED,
        UnlockState.AWAITING_MANUAL_NOR_FLASH,
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class SupportedOriginalModel:
    model_number: str
    generation: str
    nominal_capacity_gb: int
    color: str
    expected_firmware: str

    @property
    def profile_id(self) -> str:
        return (
            f"classic-{self.generation.lower().replace('.', '_')}-"
            f"{self.nominal_capacity_gb}-{self.color}"
        )


SUPPORTED_ORIGINAL_MODELS: Mapping[str, SupportedOriginalModel] = MappingProxyType(
    {
        "MB029": SupportedOriginalModel("MB029", "6G", 80, "silver", "1.1.2"),
        "MB147": SupportedOriginalModel("MB147", "6G", 80, "black", "1.1.2"),
        "MB145": SupportedOriginalModel("MB145", "6G", 160, "silver", "1.1.2"),
        "MB150": SupportedOriginalModel("MB150", "6G", 160, "black", "1.1.2"),
        "MB562": SupportedOriginalModel("MB562", "6.5G", 120, "silver", "2.0.1"),
        "MB565": SupportedOriginalModel("MB565", "6.5G", 120, "black", "2.0.1"),
    }
)


def _normalize_model_number(value: str) -> str | None:
    compact = str(value or "").strip().upper().replace(" ", "")
    match = re.match(r"^([A-Z]{2}[0-9]{3})(?:[A-Z]{1,3}/A)?$", compact)
    return match.group(1) if match else None


def _normalize_family(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_generation(value: str) -> str | None:
    compact = re.sub(r"[^a-z0-9.]+", "", str(value or "").lower())
    if compact in {
        "6g",
        "6.0g",
        "6thgen",
        "6thgeneration",
        "classic1g",
        "1gclassic",
    }:
        return "6G"
    if compact in {
        "6.5g",
        "6.5thgen",
        "6.5thgeneration",
        "classic2g",
        "2gclassic",
    }:
        return "6.5G"
    return None


def _normalize_firewire_guid(value: str) -> str:
    return re.sub(r"[^0-9A-F]", "", str(value or "").upper())


def _normalize_serial(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").upper())


def _identity_digest(serial: str, firewire_guid: str) -> str:
    return hashlib.sha256(
        f"serial:{serial}\x00firewire:{firewire_guid}".encode("ascii")
    ).hexdigest()


def _firewire_digest(firewire_guid: str) -> str:
    return hashlib.sha256(f"firewire:{firewire_guid}".encode("ascii")).hexdigest()


@dataclasses.dataclass(frozen=True, slots=True, repr=False)
class UnlockDeviceEvidence:
    platform: str
    model_family: str
    generation: str
    model_number: str
    firmware_version: str
    filesystem: str
    serial_number: str = dataclasses.field(repr=False)
    firewire_guid: str = dataclasses.field(repr=False)
    serial_is_stable: bool
    firewire_is_stable: bool
    identity_conflicts: tuple[str, ...]
    writable: bool
    writable_evidence: str
    storage_healthy: bool
    health_evidence: str
    usb_vendor_id: int
    usb_product_id: int
    is_virtual: bool
    active_device_mutation: bool

    def to_redacted_dto(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "model_family": self.model_family,
            "generation": self.generation,
            "model_number": self.model_number,
            "firmware_version": self.firmware_version,
            "filesystem": self.filesystem,
            "serial_number": "<redacted>",
            "firewire_guid": "<redacted>",
            "serial_is_stable": self.serial_is_stable,
            "firewire_is_stable": self.firewire_is_stable,
            "identity_conflict_count": len(self.identity_conflicts),
            "writable": self.writable,
            "has_writable_evidence": bool(self.writable_evidence.strip()),
            "storage_healthy": self.storage_healthy,
            "has_health_evidence": bool(self.health_evidence.strip()),
            "usb_vendor_id": self.usb_vendor_id,
            "usb_product_id": self.usb_product_id,
            "is_virtual": self.is_virtual,
            "active_device_mutation": self.active_device_mutation,
        }

    def __repr__(self) -> str:
        return (
            "UnlockDeviceEvidence("
            f"platform={self.platform!r}, model_family={self.model_family!r}, "
            f"generation={self.generation!r}, model_number={self.model_number!r}, "
            f"firmware_version={self.firmware_version!r}, filesystem={self.filesystem!r}, "
            "serial_number=<redacted>, firewire_guid=<redacted>)"
        )


@dataclasses.dataclass(frozen=True, slots=True)
class EligibilityIssue:
    code: str
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class CapacityUnlockEligibility:
    eligible: bool
    issues: tuple[EligibilityIssue, ...]
    profile: SupportedOriginalModel | None
    identity_fingerprint: str | None
    firewire_fingerprint: str | None

    def to_redacted_dto(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "issues": [dataclasses.asdict(issue) for issue in self.issues],
            "profile": dataclasses.asdict(self.profile) if self.profile else None,
            "identity_fingerprint": self.identity_fingerprint,
            "firewire_fingerprint": self.firewire_fingerprint,
        }


def evaluate_capacity_unlock_eligibility(
    evidence: UnlockDeviceEvidence,
) -> CapacityUnlockEligibility:
    """Fail closed unless every original-device requirement is proven."""

    issues: list[EligibilityIssue] = []
    platform = str(evidence.platform or "").strip().lower()
    if platform not in {"win32", "windows"}:
        issues.append(
            EligibilityIssue(
                "not_windows", "Capacity unlock is supported only on native Windows."
            )
        )
    if evidence.is_virtual:
        issues.append(
            EligibilityIssue(
                "virtualized_environment",
                "Capacity unlock is blocked in a virtualized environment.",
            )
        )
    if evidence.active_device_mutation:
        issues.append(
            EligibilityIssue(
                "active_device_mutation",
                "Another iPod mutation is active for this device.",
            )
        )
    if _normalize_family(evidence.model_family) not in {
        "ipod classic",
        "classic",
    }:
        issues.append(
            EligibilityIssue(
                "unsupported_family", "The connected device is not an iPod Classic."
            )
        )

    normalized_model = _normalize_model_number(evidence.model_number)
    profile = (
        SUPPORTED_ORIGINAL_MODELS.get(normalized_model)
        if normalized_model is not None
        else None
    )
    if profile is None:
        issues.append(
            EligibilityIssue(
                "unsupported_model",
                "The model number is not a known original Classic 6G/6.5G model.",
            )
        )
    else:
        if _normalize_generation(evidence.generation) != profile.generation:
            issues.append(
                EligibilityIssue(
                    "generation_mismatch",
                    "The generation evidence conflicts with the model number.",
                )
            )
        if str(evidence.firmware_version or "").strip() != profile.expected_firmware:
            issues.append(
                EligibilityIssue(
                    "firmware_mismatch",
                    f"This model must still run original firmware {profile.expected_firmware}.",
                )
            )

    filesystem = re.sub(r"[^a-z0-9]", "", str(evidence.filesystem or "").lower())
    if filesystem != "fat32":
        issues.append(
            EligibilityIssue(
                "filesystem_not_fat32", "The iPod must be a writable FAT32 WinPod."
            )
        )

    serial = _normalize_serial(evidence.serial_number)
    firewire = _normalize_firewire_guid(evidence.firewire_guid)
    valid_serial = bool(_SERIAL_RE.fullmatch(serial)) and set(serial) != {"0"}
    valid_firewire = bool(_FIREWIRE_RE.fullmatch(firewire)) and set(firewire) != {"0"}
    if not valid_serial or evidence.serial_is_stable is not True:
        issues.append(
            EligibilityIssue(
                "unstable_serial",
                "A complete, stable original serial identity is required.",
            )
        )
    if not valid_firewire or evidence.firewire_is_stable is not True:
        issues.append(
            EligibilityIssue(
                "unstable_firewire",
                "A complete, stable FireWire identity is required.",
            )
        )
    if tuple(item for item in evidence.identity_conflicts if str(item).strip()):
        issues.append(
            EligibilityIssue(
                "identity_conflict", "Conflicting device identity evidence was detected."
            )
        )
    if evidence.writable is not True:
        issues.append(
            EligibilityIssue("not_writable", "The mounted iPod is not writable.")
        )
    if not str(evidence.writable_evidence or "").strip():
        issues.append(
            EligibilityIssue(
                "missing_writable_evidence",
                "Independent writable-volume evidence is required.",
            )
        )
    if evidence.storage_healthy is not True:
        issues.append(
            EligibilityIssue(
                "storage_unhealthy",
                "The storage is not healthy enough for a firmware workflow.",
            )
        )
    if not str(evidence.health_evidence or "").strip():
        issues.append(
            EligibilityIssue(
                "missing_health_evidence",
                "Independent storage-health evidence is required.",
            )
        )
    if (
        evidence.usb_vendor_id != APPLE_USB_VENDOR_ID
        or evidence.usb_product_id != IPOD_NORMAL_USB_PRODUCT_ID
    ):
        issues.append(
            EligibilityIssue(
                "usb_identity_mismatch",
                "The device is not present as a normal supported iPod USB device.",
            )
        )

    identity_fingerprint = (
        _identity_digest(serial, firewire) if valid_serial and valid_firewire else None
    )
    firewire_fingerprint = _firewire_digest(firewire) if valid_firewire else None
    return CapacityUnlockEligibility(
        eligible=not issues,
        issues=tuple(issues),
        profile=profile,
        identity_fingerprint=identity_fingerprint,
        firewire_fingerprint=firewire_fingerprint,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class UnlockAcknowledgements:
    destructive_restore_erases_device: bool
    nor_flash_can_make_device_unbootable: bool
    manual_rockbox_nor_dfu_steps_required: bool
    hardware_recovery_may_be_required: bool
    itunes_restore_is_user_controlled: bool
    cancellation_ends_after_nor_commit: bool

    @property
    def complete(self) -> bool:
        return all(
            (
                self.destructive_restore_erases_device,
                self.nor_flash_can_make_device_unbootable,
                self.manual_rockbox_nor_dfu_steps_required,
                self.hardware_recovery_may_be_required,
                self.itunes_restore_is_user_controlled,
                self.cancellation_ends_after_nor_commit,
            )
        )


@dataclasses.dataclass(frozen=True, slots=True)
class SysCfgBackupCopy:
    copy_id: str
    path: str
    sha256: str
    reread_sha256: str
    size: int


@dataclasses.dataclass(frozen=True, slots=True, repr=False)
class PostflightEvidence:
    firmware_version: str
    model_number: str
    filesystem: str
    firewire_guid: str = dataclasses.field(repr=False)
    identity_conflicts: tuple[str, ...]
    writable: bool
    writable_evidence: str
    storage_healthy: bool
    health_evidence: str

    def __repr__(self) -> str:
        return (
            "PostflightEvidence("
            f"firmware_version={self.firmware_version!r}, "
            f"model_number={self.model_number!r}, filesystem={self.filesystem!r}, "
            "firewire_guid=<redacted>)"
        )


@dataclasses.dataclass(frozen=True, slots=True)
class TransitionRecord:
    from_state: str | None
    to_state: str
    at: str
    reason: str


@dataclasses.dataclass(frozen=True, slots=True, repr=False)
class CapacityUnlockSession:
    session_id: str
    revision: int
    state: UnlockState
    created_at: str
    updated_at: str
    identity_fingerprint: str
    firewire_fingerprint: str
    source_profile_id: str
    source_model_number: str
    source_generation: str
    source_firmware_version: str
    target_firmware_version: str
    nor_committed: bool
    details: Mapping[str, Any] = dataclasses.field(repr=False)
    history: tuple[TransitionRecord, ...]
    recovery_resume_state: UnlockState | None

    @property
    def can_cancel(self) -> bool:
        return (
            not self.nor_committed
            and self.state not in {UnlockState.COMPLETE, UnlockState.CANCELLED}
        )

    @property
    def terminal(self) -> bool:
        return self.state in {UnlockState.COMPLETE, UnlockState.CANCELLED}

    def to_redacted_dto(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "revision": self.revision,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "identity_fingerprint": self.identity_fingerprint,
            "firewire_fingerprint": self.firewire_fingerprint,
            "source_profile_id": self.source_profile_id,
            "source_model_number": self.source_model_number,
            "source_generation": self.source_generation,
            "source_firmware_version": self.source_firmware_version,
            "target_firmware_version": self.target_firmware_version,
            "nor_committed": self.nor_committed,
            "can_cancel": self.can_cancel,
            "terminal": self.terminal,
            "details": _json_clone(self.details),
            "history": [dataclasses.asdict(item) for item in self.history],
            "recovery_resume_state": (
                self.recovery_resume_state.value
                if self.recovery_resume_state is not None
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"CapacityUnlockSession(session_id={self.session_id!r}, "
            f"revision={self.revision}, state={self.state.value!r}, "
            "identity=<redacted>)"
        )


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True))


def _validate_transition_history(
    history: tuple[TransitionRecord, ...],
) -> tuple[bool, bool]:
    if (
        not history
        or history[0].from_state is not None
        or history[0].to_state != UnlockState.ELIGIBILITY_CHECKED.value
    ):
        return False, False
    previous = UnlockState.ELIGIBILITY_CHECKED
    recovery_target: UnlockState | None = None
    nor_committed = False
    for index, record in enumerate(history):
        try:
            target = UnlockState(record.to_state)
            source = (
                UnlockState(record.from_state)
                if record.from_state is not None
                else None
            )
        except ValueError:
            return False, False
        if index == 0:
            if source is not None:
                return False, False
            continue
        if source != previous:
            return False, False
        if target == UnlockState.RECOVERY_REQUIRED:
            if source in {
                UnlockState.COMPLETE,
                UnlockState.CANCELLED,
                UnlockState.RECOVERY_REQUIRED,
            }:
                return False, False
            recovery_target = source
        elif source == UnlockState.RECOVERY_REQUIRED:
            if target == UnlockState.CANCELLED:
                if nor_committed:
                    return False, False
            elif recovery_target is None or target != recovery_target:
                return False, False
            recovery_target = None
        elif target == UnlockState.CANCELLED:
            if source not in _PRE_NOR_STATES or nor_committed:
                return False, False
        elif _NORMAL_TRANSITIONS.get(source) != target:
            return False, False
        if target == UnlockState.NOR_FLASH_ATTESTED:
            nor_committed = True
        previous = target
    return True, nor_committed


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso_timestamp(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _require_sha256(value: str, code: str) -> str:
    digest = str(value or "").lower()
    if not _SHA256_RE.fullmatch(digest):
        raise CapacityUnlockError(code, "A required SHA-256 value is invalid.")
    return digest


def _require_opaque_id(value: str, code: str, message: str) -> str:
    result = str(value or "")
    if not _OPAQUE_ID_RE.fullmatch(result):
        raise CapacityUnlockError(code, message)
    return result


def _decode_model_entry(data: bytes) -> str:
    entry = parse_syscfg(data).entry(TAG_MODEL_NUMBER).data
    terminator = entry.find(b"\x00")
    if terminator <= 0 or any(entry[terminator + 1 :]):
        raise CapacityUnlockError(
            "invalid_syscfg_model", "The SysCfg model entry is not canonical."
        )
    try:
        value = entry[:terminator].decode("ascii")
    except UnicodeDecodeError as exc:
        raise CapacityUnlockError(
            "invalid_syscfg_model", "The SysCfg model entry is not ASCII."
        ) from exc
    normalized = _normalize_model_number(value)
    if normalized is None:
        raise CapacityUnlockError(
            "invalid_syscfg_model", "The SysCfg model entry is invalid."
        )
    return normalized


class CapacityUnlockStateMachine:
    """Atomic session repository plus legal-transition enforcement."""

    def __init__(
        self,
        state_path: str | os.PathLike[str],
        *,
        clock: Callable[[], dt.datetime] | None = None,
        session_id_factory: Callable[[], str] | None = None,
    ):
        self._path = Path(state_path)
        self._clock = clock or _utc_now
        self._session_id_factory = session_id_factory or (
            lambda: f"unlock-{secrets.token_urlsafe(18)}"
        )
        self._lock = threading.RLock()

    @property
    def state_path(self) -> Path:
        return self._path

    def start_session(self, evidence: UnlockDeviceEvidence) -> CapacityUnlockSession:
        eligibility = evaluate_capacity_unlock_eligibility(evidence)
        if (
            not eligibility.eligible
            or eligibility.profile is None
            or eligibility.identity_fingerprint is None
            or eligibility.firewire_fingerprint is None
        ):
            codes = ", ".join(issue.code for issue in eligibility.issues) or "unknown"
            raise CapacityUnlockError(
                "device_ineligible",
                f"Capacity unlock eligibility failed: {codes}.",
            )
        session_id = _require_opaque_id(
            self._session_id_factory(),
            "invalid_session_id",
            "The generated unlock session ID is invalid.",
        )
        now = _iso_timestamp(self._clock())
        profile = eligibility.profile
        session = CapacityUnlockSession(
            session_id=session_id,
            revision=1,
            state=UnlockState.ELIGIBILITY_CHECKED,
            created_at=now,
            updated_at=now,
            identity_fingerprint=eligibility.identity_fingerprint,
            firewire_fingerprint=eligibility.firewire_fingerprint,
            source_profile_id=profile.profile_id,
            source_model_number=profile.model_number,
            source_generation=profile.generation,
            source_firmware_version=profile.expected_firmware,
            target_firmware_version=TARGET_FIRMWARE_VERSION,
            nor_committed=False,
            details={
                "eligibility": {
                    "profile_id": profile.profile_id,
                    "writable_evidence_recorded": True,
                    "health_evidence_recorded": True,
                    "usb_vendor_id": evidence.usb_vendor_id,
                    "usb_product_id": evidence.usb_product_id,
                }
            },
            history=(
                TransitionRecord(
                    from_state=None,
                    to_state=UnlockState.ELIGIBILITY_CHECKED.value,
                    at=now,
                    reason="eligibility_validated",
                ),
            ),
            recovery_resume_state=None,
        )
        with self._lock:
            sessions = self._load_sessions()
            if len(sessions) >= MAX_SESSIONS:
                raise CapacityUnlockError(
                    "too_many_sessions", "The unlock session repository is full."
                )
            if session_id in sessions:
                raise CapacityUnlockError(
                    "duplicate_session", "The generated unlock session ID already exists."
                )
            sessions[session_id] = session
            self._save_sessions(sessions)
        return session

    def get_session(self, session_id: str) -> CapacityUnlockSession:
        with self._lock:
            sessions = self._load_sessions()
            try:
                return sessions[session_id]
            except KeyError as exc:
                raise CapacityUnlockError(
                    "session_not_found", "The capacity-unlock session was not found."
                ) from exc

    def list_sessions(self) -> tuple[CapacityUnlockSession, ...]:
        with self._lock:
            sessions = self._load_sessions()
        return tuple(
            sorted(sessions.values(), key=lambda item: (item.created_at, item.session_id))
        )

    def acknowledge_environment(
        self,
        session_id: str,
        acknowledgements: UnlockAcknowledgements,
        *,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        if not isinstance(acknowledgements, UnlockAcknowledgements):
            raise CapacityUnlockError(
                "invalid_acknowledgements",
                "Every unlock acknowledgement must be supplied explicitly.",
            )
        if not acknowledgements.complete:
            raise CapacityUnlockError(
                "acknowledgements_incomplete",
                "Every destructive-workflow acknowledgement is required.",
            )
        return self._transition(
            session_id,
            UnlockState.ENVIRONMENT_READY,
            "environment_acknowledged",
            expected_revision=expected_revision,
            detail_updates={
                "acknowledgements": dataclasses.asdict(acknowledgements)
            },
        )

    def record_filesystem_backup(
        self,
        session_id: str,
        *,
        backup_reference: str,
        verification_sha256: str,
        verified: bool,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        if verified is not True:
            raise CapacityUnlockError(
                "backup_not_verified",
                "A verified host-side filesystem backup is required.",
            )
        reference = _require_opaque_id(
            backup_reference,
            "invalid_backup_reference",
            "The backup reference must be an opaque identifier.",
        )
        digest = _require_sha256(
            verification_sha256, "invalid_backup_verification"
        )
        reference_digest = hashlib.sha256(reference.encode("utf-8")).hexdigest()
        return self._transition(
            session_id,
            UnlockState.FILESYSTEM_BACKUP_VERIFIED,
            "filesystem_backup_verified",
            expected_revision=expected_revision,
            detail_updates={
                "filesystem_backup": {
                    "reference_sha256": reference_digest,
                    "verification_sha256": digest,
                    "verified": True,
                }
            },
        )

    def record_artifacts_verified(
        self,
        session_id: str,
        receipts: Iterable[ArtifactReceipt],
        *,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        by_id: dict[str, ArtifactReceipt] = {}
        for receipt in receipts:
            if not isinstance(receipt, ArtifactReceipt):
                raise CapacityUnlockError(
                    "invalid_artifact_receipt",
                    "Artifact verification receipts are required.",
                )
            if receipt.artifact_id in by_id:
                raise CapacityUnlockError(
                    "duplicate_artifact", "An artifact receipt was supplied more than once."
                )
            by_id[receipt.artifact_id] = receipt
        if set(by_id) != set(REQUIRED_UNLOCK_ARTIFACT_IDS):
            raise CapacityUnlockError(
                "artifact_set_incomplete",
                "Every pinned unlock artifact must be verified before continuing.",
            )
        persisted: dict[str, Any] = {}
        for artifact_id in sorted(REQUIRED_UNLOCK_ARTIFACT_IDS):
            receipt = by_id[artifact_id]
            spec = PINNED_UNLOCK_ARTIFACTS[artifact_id]
            if (
                receipt.size != spec.expected_size
                or receipt.sha256 != spec.sha256
                or receipt.sha1 != spec.sha1
                or receipt.metadata_sha256 != spec.metadata_sha256
            ):
                raise CapacityUnlockError(
                    "artifact_receipt_mismatch",
                    f"The {artifact_id} receipt does not match pinned metadata.",
                )
            persisted[artifact_id] = {
                "size": receipt.size,
                "sha1": receipt.sha1,
                "sha256": receipt.sha256,
                "metadata_sha256": receipt.metadata_sha256,
            }
        return self._transition(
            session_id,
            UnlockState.ARTIFACTS_VERIFIED,
            "artifacts_verified",
            expected_revision=expected_revision,
            detail_updates={"artifacts": persisted},
        )

    def await_bootloader_install(
        self,
        session_id: str,
        *,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        return self._transition(
            session_id,
            UnlockState.AWAITING_BOOTLOADER_INSTALL,
            "manual_bootloader_install_required",
            expected_revision=expected_revision,
        )

    def record_bootloader_installed(
        self,
        session_id: str,
        *,
        user_attested: bool,
        helper_build: HelperBuildReceipt,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        if user_attested is not True:
            raise CapacityUnlockError(
                "bootloader_not_attested",
                "The manual Rockbox bootloader step must be explicitly attested.",
            )
        if not isinstance(helper_build, HelperBuildReceipt):
            raise CapacityUnlockError(
                "invalid_helper_build_receipt",
                "The helper, corresponding source, and build manifest must be "
                "verified before recording the bootloader step.",
            )
        return self._transition(
            session_id,
            UnlockState.AWAITING_SYSCFG_DUMP,
            "bootloader_install_attested",
            expected_revision=expected_revision,
            detail_updates={
                "bootloader": {
                    "user_attested": True,
                    "helper_build": helper_build.to_redacted_dto(),
                }
            },
        )

    def record_original_syscfg(
        self,
        session_id: str,
        original_data: bytes,
        backup_paths: Iterable[str | os.PathLike[str]],
        *,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        original_bytes = bytes(original_data)
        parse_syscfg(original_bytes)
        session = self.get_session(session_id)
        if session.state != UnlockState.AWAITING_SYSCFG_DUMP:
            self._raise_wrong_state(session, UnlockState.ORIGINAL_SYSCFG_VERIFIED)
        if _decode_model_entry(original_bytes) != session.source_model_number:
            raise CapacityUnlockError(
                "syscfg_model_mismatch",
                "The dumped SysCfg does not match the eligible original model.",
            )
        original_sha256 = hashlib.sha256(original_bytes).hexdigest()
        copies = self._verify_syscfg_copies(
            original_bytes, backup_paths, original_sha256
        )
        return self._transition(
            session_id,
            UnlockState.ORIGINAL_SYSCFG_VERIFIED,
            "original_syscfg_copies_verified",
            expected_revision=expected_revision,
            detail_updates={
                "original_syscfg": {
                    "sha256": original_sha256,
                    "size": len(original_bytes),
                    "verified_copy_count": len(copies),
                    "copy_ids": [copy.copy_id for copy in copies],
                }
            },
        )

    def record_candidate_syscfg(
        self,
        session_id: str,
        candidate: CapacityUnlockCandidate,
        *,
        original_data: bytes,
        preset: CapacityUnlockPreset,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        if not isinstance(candidate, CapacityUnlockCandidate):
            raise CapacityUnlockError(
                "invalid_candidate", "A validated capacity-unlock candidate is required."
            )
        if not isinstance(preset, CapacityUnlockPreset):
            raise CapacityUnlockError(
                "missing_audited_preset",
                "The explicit audited preset is required again at the session boundary.",
            )
        try:
            preset.assert_usable()
        except ValueError as exc:
            raise CapacityUnlockError(
                "unaudited_preset",
                "The session cannot accept an unaudited capacity-unlock preset.",
            ) from exc
        session = self.get_session(session_id)
        if session.state != UnlockState.ORIGINAL_SYSCFG_VERIFIED:
            self._raise_wrong_state(session, UnlockState.CANDIDATE_SYSCFG_VERIFIED)
        original_details = session.details.get("original_syscfg", {})
        original_bytes = bytes(original_data)
        parse_syscfg(original_bytes)
        original_sha256 = hashlib.sha256(original_bytes).hexdigest()
        if (
            candidate.original_sha256 != original_details.get("sha256")
            or original_sha256 != original_details.get("sha256")
        ):
            raise CapacityUnlockError(
                "candidate_original_mismatch",
                "The candidate was not built from this session's verified original SysCfg.",
            )
        if candidate.source_model_number != session.source_model_number:
            raise CapacityUnlockError(
                "candidate_model_mismatch",
                "The candidate source model does not match this unlock session.",
            )
        candidate_bytes = bytes(candidate.candidate_bytes)
        parse_syscfg(candidate_bytes)
        if hashlib.sha256(candidate_bytes).hexdigest() != candidate.candidate_sha256:
            raise CapacityUnlockError(
                "candidate_digest_mismatch",
                "The candidate bytes no longer match their validated SHA-256.",
            )
        if (
            candidate.preset_id != preset.preset_id
            or candidate.preset_sha256 != preset.digest
        ):
            raise CapacityUnlockError(
                "candidate_preset_mismatch",
                "The candidate does not match the explicit audited preset.",
            )
        try:
            diff = validate_capacity_unlock_diff(
                original_bytes, candidate_bytes, preset=preset
            )
        except ValueError as exc:
            raise CapacityUnlockError(
                "candidate_diff_invalid",
                "The candidate failed independent narrow-diff validation.",
            ) from exc
        if (
            diff.original_sha256 != candidate.original_sha256
            or diff.candidate_sha256 != candidate.candidate_sha256
            or diff.changed_tags != candidate.changed_tags
        ):
            raise CapacityUnlockError(
                "candidate_attestation_mismatch",
                "The candidate metadata does not match independent diff validation.",
            )
        target_model = _decode_model_entry(candidate_bytes)
        return self._transition(
            session_id,
            UnlockState.CANDIDATE_SYSCFG_VERIFIED,
            "candidate_syscfg_verified",
            expected_revision=expected_revision,
            detail_updates={
                "candidate_syscfg": {
                    "preset_id": candidate.preset_id,
                    "preset_sha256": candidate.preset_sha256,
                    "original_sha256": candidate.original_sha256,
                    "candidate_sha256": candidate.candidate_sha256,
                    "size": len(candidate_bytes),
                    "changed_tags": list(candidate.changed_tags),
                    "target_model_number": target_model,
                }
            },
        )

    def record_candidate_staged(
        self,
        session_id: str,
        reread_staged_data: bytes,
        *,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        staged = bytes(reread_staged_data)
        parse_syscfg(staged)
        session = self.get_session(session_id)
        if session.state != UnlockState.CANDIDATE_SYSCFG_VERIFIED:
            self._raise_wrong_state(session, UnlockState.CANDIDATE_STAGED)
        expected = session.details.get("candidate_syscfg", {}).get("candidate_sha256")
        actual = hashlib.sha256(staged).hexdigest()
        if actual != expected:
            raise CapacityUnlockError(
                "staged_candidate_mismatch",
                "The staged SysCfg reread does not match the verified candidate.",
            )
        return self._transition(
            session_id,
            UnlockState.CANDIDATE_STAGED,
            "candidate_staged_and_reread",
            expected_revision=expected_revision,
            detail_updates={
                "candidate_stage": {
                    "reread_sha256": actual,
                    "size": len(staged),
                }
            },
        )

    def await_manual_nor_flash(
        self,
        session_id: str,
        *,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        return self._transition(
            session_id,
            UnlockState.AWAITING_MANUAL_NOR_FLASH,
            "manual_nor_flash_required",
            expected_revision=expected_revision,
        )

    def attest_nor_flash(
        self,
        session_id: str,
        *,
        user_attested: bool,
        reread_nor_data: bytes,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        if user_attested is not True:
            raise CapacityUnlockError(
                "nor_flash_not_attested",
                "The on-device NOR operation must be explicitly attested.",
            )
        reread = bytes(reread_nor_data)
        parse_syscfg(reread)
        session = self.get_session(session_id)
        if session.state != UnlockState.AWAITING_MANUAL_NOR_FLASH:
            self._raise_wrong_state(session, UnlockState.NOR_FLASH_ATTESTED)
        expected = session.details.get("candidate_syscfg", {}).get("candidate_sha256")
        actual = hashlib.sha256(reread).hexdigest()
        if actual != expected:
            raise CapacityUnlockError(
                "nor_readback_mismatch",
                "NOR readback is not byte-identical to the verified candidate.",
            )
        return self._transition(
            session_id,
            UnlockState.NOR_FLASH_ATTESTED,
            "manual_nor_flash_and_readback_attested",
            expected_revision=expected_revision,
            detail_updates={
                "nor_flash": {
                    "user_attested": True,
                    "readback_sha256": actual,
                }
            },
            nor_committed=True,
        )

    def await_dfu(
        self,
        session_id: str,
        *,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        return self._transition(
            session_id,
            UnlockState.AWAITING_DFU,
            "manual_dfu_entry_required",
            expected_revision=expected_revision,
        )

    def record_dfu_detected(
        self,
        session_id: str,
        *,
        usb_vendor_id: int,
        usb_product_id: int,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        if (
            usb_vendor_id != APPLE_USB_VENDOR_ID
            or usb_product_id not in IPOD_RECOVERY_USB_PRODUCT_IDS
        ):
            raise CapacityUnlockError(
                "dfu_not_detected",
                "A supported Apple iPod recovery/DFU USB identity was not detected.",
            )
        return self._transition(
            session_id,
            UnlockState.ITUNES_HANDOFF,
            "dfu_detected",
            expected_revision=expected_revision,
            detail_updates={
                "dfu": {
                    "usb_vendor_id": usb_vendor_id,
                    "usb_product_id": usb_product_id,
                }
            },
        )

    def record_itunes_handoff(
        self,
        session_id: str,
        *,
        user_attested: bool,
        firmware_sha256: str,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        if user_attested is not True:
            raise CapacityUnlockError(
                "itunes_handoff_not_attested",
                "The user-controlled iTunes handoff must be explicitly acknowledged.",
            )
        firmware_spec = PINNED_UNLOCK_ARTIFACTS[
            "apple-ipod-classic-2.0.2-ipsw"
        ]
        if _require_sha256(
            firmware_sha256, "invalid_firmware_digest"
        ) != firmware_spec.sha256:
            raise CapacityUnlockError(
                "firmware_digest_mismatch",
                "The iTunes restore image does not match the pinned Apple firmware.",
            )
        return self._transition(
            session_id,
            UnlockState.AWAITING_RESTORE,
            "itunes_handoff_attested",
            expected_revision=expected_revision,
            detail_updates={
                "itunes_handoff": {
                    "user_attested": True,
                    "firmware_sha256": firmware_spec.sha256,
                }
            },
        )

    def attest_restore_finished(
        self,
        session_id: str,
        *,
        user_attested: bool,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        if user_attested is not True:
            raise CapacityUnlockError(
                "restore_not_attested",
                "Completion of the user-controlled iTunes restore must be attested.",
            )
        return self._transition(
            session_id,
            UnlockState.POSTFLIGHT_VERIFICATION,
            "itunes_restore_attested",
            expected_revision=expected_revision,
            detail_updates={"restore": {"user_attested": True}},
        )

    def record_postflight(
        self,
        session_id: str,
        evidence: PostflightEvidence,
        *,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        if not isinstance(evidence, PostflightEvidence):
            raise CapacityUnlockError(
                "invalid_postflight", "Typed postflight evidence is required."
            )
        session = self.get_session(session_id)
        if session.state != UnlockState.POSTFLIGHT_VERIFICATION:
            self._raise_wrong_state(session, UnlockState.COMPLETE)
        if str(evidence.firmware_version or "").strip() != TARGET_FIRMWARE_VERSION:
            raise CapacityUnlockError(
                "postflight_firmware_mismatch",
                "Postflight firmware is not the pinned 2.0.2 target.",
            )
        target_model = session.details.get("candidate_syscfg", {}).get(
            "target_model_number"
        )
        if _normalize_model_number(evidence.model_number) != target_model:
            raise CapacityUnlockError(
                "postflight_model_mismatch",
                "Postflight model identity does not match the verified candidate.",
            )
        filesystem = re.sub(
            r"[^a-z0-9]", "", str(evidence.filesystem or "").lower()
        )
        if filesystem != "fat32":
            raise CapacityUnlockError(
                "postflight_filesystem_mismatch",
                "Postflight verification requires a FAT32 WinPod.",
            )
        firewire = _normalize_firewire_guid(evidence.firewire_guid)
        if (
            not _FIREWIRE_RE.fullmatch(firewire)
            or _firewire_digest(firewire) != session.firewire_fingerprint
        ):
            raise CapacityUnlockError(
                "postflight_device_mismatch",
                "Postflight FireWire identity does not match the original device.",
            )
        if tuple(item for item in evidence.identity_conflicts if str(item).strip()):
            raise CapacityUnlockError(
                "postflight_identity_conflict",
                "Postflight device identity evidence is conflicting.",
            )
        if evidence.writable is not True or not evidence.writable_evidence.strip():
            raise CapacityUnlockError(
                "postflight_not_writable",
                "Postflight writable-volume evidence is required.",
            )
        if (
            evidence.storage_healthy is not True
            or not evidence.health_evidence.strip()
        ):
            raise CapacityUnlockError(
                "postflight_storage_unhealthy",
                "Postflight storage-health evidence is required.",
            )
        return self._transition(
            session_id,
            UnlockState.COMPLETE,
            "postflight_verified",
            expected_revision=expected_revision,
            detail_updates={
                "postflight": {
                    "firmware_version": TARGET_FIRMWARE_VERSION,
                    "model_number": target_model,
                    "filesystem": "FAT32",
                    "firewire_identity_matches": True,
                    "writable_evidence_recorded": True,
                    "health_evidence_recorded": True,
                }
            },
        )

    def cancel(
        self,
        session_id: str,
        *,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        with self._lock:
            sessions = self._load_sessions()
            session = self._get_existing(sessions, session_id)
            self._check_revision(session, expected_revision)
            if not session.can_cancel:
                raise CapacityUnlockError(
                    "cancellation_unsafe",
                    "Cancellation is no longer safe after NOR commit.",
                )
            return self._replace_session(
                sessions,
                session,
                UnlockState.CANCELLED,
                reason="cancelled_before_nor_commit",
                detail_updates={"cancelled": {"safe_before_nor_commit": True}},
                recovery_resume_state=None,
            )

    def require_recovery(
        self,
        session_id: str,
        *,
        reason_code: str,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        reason = str(reason_code or "").lower()
        if not _REASON_CODE_RE.fullmatch(reason):
            raise CapacityUnlockError(
                "invalid_recovery_reason",
                "The recovery reason must be a bounded machine-readable code.",
            )
        with self._lock:
            sessions = self._load_sessions()
            session = self._get_existing(sessions, session_id)
            self._check_revision(session, expected_revision)
            if session.terminal:
                raise CapacityUnlockError(
                    "terminal_session", "A completed or cancelled session cannot enter recovery."
                )
            if session.state == UnlockState.RECOVERY_REQUIRED:
                raise CapacityUnlockError(
                    "already_in_recovery", "The session already requires recovery."
                )
            resume_state = session.state
            return self._replace_session(
                sessions,
                session,
                UnlockState.RECOVERY_REQUIRED,
                reason="recovery_required",
                detail_updates={
                    "recovery": {
                        "reason_code": reason,
                        "entered_after_nor_commit": session.nor_committed,
                    }
                },
                recovery_resume_state=resume_state,
            )

    def resume_recovery(
        self,
        session_id: str,
        *,
        expected_revision: int | None = None,
    ) -> CapacityUnlockSession:
        with self._lock:
            sessions = self._load_sessions()
            session = self._get_existing(sessions, session_id)
            self._check_revision(session, expected_revision)
            if (
                session.state != UnlockState.RECOVERY_REQUIRED
                or session.recovery_resume_state is None
            ):
                raise CapacityUnlockError(
                    "recovery_not_resumable",
                    "The session has no persisted recovery checkpoint to resume.",
                )
            target = session.recovery_resume_state
            return self._replace_session(
                sessions,
                session,
                target,
                reason="recovery_resumed",
                detail_updates={"recovery": None},
                recovery_resume_state=None,
            )

    def _transition(
        self,
        session_id: str,
        target: UnlockState,
        reason: str,
        *,
        expected_revision: int | None,
        detail_updates: Mapping[str, Any] | None = None,
        nor_committed: bool | None = None,
    ) -> CapacityUnlockSession:
        with self._lock:
            sessions = self._load_sessions()
            session = self._get_existing(sessions, session_id)
            self._check_revision(session, expected_revision)
            expected_target = _NORMAL_TRANSITIONS.get(session.state)
            if expected_target != target:
                self._raise_wrong_state(session, target)
            return self._replace_session(
                sessions,
                session,
                target,
                reason=reason,
                detail_updates=detail_updates,
                nor_committed=nor_committed,
            )

    def _replace_session(
        self,
        sessions: dict[str, CapacityUnlockSession],
        session: CapacityUnlockSession,
        target: UnlockState,
        *,
        reason: str,
        detail_updates: Mapping[str, Any] | None = None,
        nor_committed: bool | None = None,
        recovery_resume_state: UnlockState | None | object = ...,
    ) -> CapacityUnlockSession:
        now = _iso_timestamp(self._clock())
        details = _json_clone(session.details)
        for key, value in (detail_updates or {}).items():
            if value is None:
                details.pop(key, None)
            else:
                details[str(key)] = _json_clone(value)
        recovery_state = (
            session.recovery_resume_state
            if recovery_resume_state is ...
            else recovery_resume_state
        )
        replacement = dataclasses.replace(
            session,
            revision=session.revision + 1,
            state=target,
            updated_at=now,
            nor_committed=(
                session.nor_committed
                if nor_committed is None
                else bool(nor_committed)
            ),
            details=details,
            history=session.history
            + (
                TransitionRecord(
                    from_state=session.state.value,
                    to_state=target.value,
                    at=now,
                    reason=reason,
                ),
            ),
            recovery_resume_state=recovery_state,  # type: ignore[arg-type]
        )
        sessions[session.session_id] = replacement
        self._save_sessions(sessions)
        return replacement

    @staticmethod
    def _check_revision(
        session: CapacityUnlockSession, expected_revision: int | None
    ) -> None:
        if expected_revision is not None and session.revision != expected_revision:
            raise CapacityUnlockError(
                "stale_session",
                "The capacity-unlock session changed; reload it before continuing.",
            )

    @staticmethod
    def _get_existing(
        sessions: Mapping[str, CapacityUnlockSession], session_id: str
    ) -> CapacityUnlockSession:
        try:
            return sessions[session_id]
        except KeyError as exc:
            raise CapacityUnlockError(
                "session_not_found", "The capacity-unlock session was not found."
            ) from exc

    @staticmethod
    def _raise_wrong_state(
        session: CapacityUnlockSession, target: UnlockState
    ) -> None:
        raise CapacityUnlockError(
            "illegal_transition",
            f"Cannot advance from {session.state.value} to {target.value}.",
        )

    @staticmethod
    def _verify_syscfg_copies(
        original: bytes,
        paths: Iterable[str | os.PathLike[str]],
        expected_sha256: str,
    ) -> tuple[SysCfgBackupCopy, ...]:
        candidates = tuple(Path(path) for path in paths)
        if len(candidates) < 2:
            raise CapacityUnlockError(
                "insufficient_syscfg_copies",
                "At least two verified host-side SysCfg copies are required.",
            )
        copies: list[SysCfgBackupCopy] = []
        canonical_paths: set[str] = set()
        canonical_parents: set[str] = set()
        file_identities: set[tuple[int, int]] = set()
        for index, path in enumerate(candidates, start=1):
            try:
                before = path.lstat()
            except OSError as exc:
                raise CapacityUnlockError(
                    "syscfg_copy_unreadable",
                    "A host-side SysCfg backup copy cannot be read.",
                ) from exc
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise CapacityUnlockError(
                    "unsafe_syscfg_copy",
                    "SysCfg backup copies must be regular non-symlink files.",
                )
            canonical = os.path.normcase(str(path.resolve()))
            if canonical in canonical_paths:
                raise CapacityUnlockError(
                    "duplicate_syscfg_copy",
                    "SysCfg backup copies must use distinct host paths.",
                )
            canonical_paths.add(canonical)
            canonical_parent = os.path.normcase(str(path.resolve().parent))
            if canonical_parent in canonical_parents:
                raise CapacityUnlockError(
                    "duplicate_syscfg_location",
                    "SysCfg backup copies must use distinct host locations.",
                )
            canonical_parents.add(canonical_parent)
            file_identity = (before.st_dev, before.st_ino)
            if before.st_ino and file_identity in file_identities:
                raise CapacityUnlockError(
                    "duplicate_syscfg_copy",
                    "Hard-linked paths do not count as independent SysCfg copies.",
                )
            if before.st_ino:
                file_identities.add(file_identity)
            if before.st_size != len(original):
                raise CapacityUnlockError(
                    "syscfg_copy_mismatch",
                    "A host-side SysCfg copy has the wrong size.",
                )
            try:
                first_read = path.read_bytes()
                second_read = path.read_bytes()
            except OSError as exc:
                raise CapacityUnlockError(
                    "syscfg_copy_unreadable",
                    "A host-side SysCfg backup copy cannot be reread.",
                ) from exc
            first_digest = hashlib.sha256(first_read).hexdigest()
            second_digest = hashlib.sha256(second_read).hexdigest()
            if (
                first_read != original
                or second_read != original
                or first_digest != expected_sha256
                or second_digest != expected_sha256
            ):
                raise CapacityUnlockError(
                    "syscfg_copy_mismatch",
                    "A host-side SysCfg copy is not byte-identical to the original.",
                )
            copies.append(
                SysCfgBackupCopy(
                    copy_id=f"copy-{index}",
                    path="<redacted>",
                    sha256=first_digest,
                    reread_sha256=second_digest,
                    size=len(first_read),
                )
            )
        return tuple(copies)

    def _load_sessions(self) -> dict[str, CapacityUnlockSession]:
        if not self._path.exists():
            return {}
        try:
            status = self._path.lstat()
            if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
                raise CapacityUnlockError(
                    "unsafe_state_file",
                    "The capacity-unlock state path is not a regular file.",
                )
            if status.st_size > MAX_STATE_FILE_BYTES:
                raise CapacityUnlockError(
                    "state_file_oversized",
                    "The capacity-unlock state file exceeds its safety bound.",
                )
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except CapacityUnlockError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CapacityUnlockError(
                "state_file_corrupt",
                "The capacity-unlock state file cannot be read safely.",
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != STATE_SCHEMA_VERSION
            or not isinstance(payload.get("sessions"), list)
            or len(payload["sessions"]) > MAX_SESSIONS
        ):
            raise CapacityUnlockError(
                "state_file_corrupt",
                "The capacity-unlock state document is invalid or unsupported.",
            )
        sessions: dict[str, CapacityUnlockSession] = {}
        for item in payload["sessions"]:
            session = self._session_from_dict(item)
            if session.session_id in sessions:
                raise CapacityUnlockError(
                    "state_file_corrupt",
                    "The capacity-unlock state contains duplicate sessions.",
                )
            sessions[session.session_id] = session
        return sessions

    def _save_sessions(
        self, sessions: Mapping[str, CapacityUnlockSession]
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "sessions": [
                self._session_to_dict(session)
                for session in sorted(
                    sessions.values(), key=lambda item: item.session_id
                )
            ],
        }
        serialized = (
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        if len(serialized.encode("utf-8")) > MAX_STATE_FILE_BYTES:
            raise CapacityUnlockError(
                "state_file_oversized",
                "The capacity-unlock state document exceeds its safety bound.",
            )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(serialized)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self._path)
            temporary_path = None
            self._fsync_parent()
        except OSError as exc:
            raise CapacityUnlockError(
                "state_write_failed",
                "The capacity-unlock state could not be saved atomically.",
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _fsync_parent(self) -> None:
        if os.name == "nt":
            return
        descriptor: int | None = None
        try:
            descriptor = os.open(self._path.parent, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            return
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _session_to_dict(session: CapacityUnlockSession) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "revision": session.revision,
            "state": session.state.value,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "identity_fingerprint": session.identity_fingerprint,
            "firewire_fingerprint": session.firewire_fingerprint,
            "source_profile_id": session.source_profile_id,
            "source_model_number": session.source_model_number,
            "source_generation": session.source_generation,
            "source_firmware_version": session.source_firmware_version,
            "target_firmware_version": session.target_firmware_version,
            "nor_committed": session.nor_committed,
            "details": _json_clone(session.details),
            "history": [dataclasses.asdict(item) for item in session.history],
            "recovery_resume_state": (
                session.recovery_resume_state.value
                if session.recovery_resume_state is not None
                else None
            ),
        }

    @staticmethod
    def _session_from_dict(value: Any) -> CapacityUnlockSession:
        if not isinstance(value, dict):
            raise CapacityUnlockError(
                "state_file_corrupt", "An unlock session record is invalid."
            )
        required = {
            "session_id",
            "revision",
            "state",
            "created_at",
            "updated_at",
            "identity_fingerprint",
            "firewire_fingerprint",
            "source_profile_id",
            "source_model_number",
            "source_generation",
            "source_firmware_version",
            "target_firmware_version",
            "nor_committed",
            "details",
            "history",
            "recovery_resume_state",
        }
        if set(value) != required:
            raise CapacityUnlockError(
                "state_file_corrupt", "An unlock session record has unexpected fields."
            )
        try:
            session_id = _require_opaque_id(
                value["session_id"],
                "state_file_corrupt",
                "An unlock session ID is invalid.",
            )
            revision = int(value["revision"])
            state = UnlockState(value["state"])
            identity_fingerprint = _require_sha256(
                value["identity_fingerprint"], "state_file_corrupt"
            )
            firewire_fingerprint = _require_sha256(
                value["firewire_fingerprint"], "state_file_corrupt"
            )
            history = tuple(
                TransitionRecord(
                    from_state=item.get("from_state"),
                    to_state=str(item["to_state"]),
                    at=str(item["at"]),
                    reason=str(item["reason"]),
                )
                for item in value["history"]
            )
            recovery_raw = value["recovery_resume_state"]
            recovery_state = (
                UnlockState(recovery_raw) if recovery_raw is not None else None
            )
            details = _json_clone(value["details"])
        except (KeyError, TypeError, ValueError, CapacityUnlockError) as exc:
            raise CapacityUnlockError(
                "state_file_corrupt", "An unlock session record is malformed."
            ) from exc
        history_valid, history_nor_committed = _validate_transition_history(history)
        if (
            revision < 1
            or revision != len(history)
            or not isinstance(value["nor_committed"], bool)
            or not isinstance(details, dict)
            or not history_valid
            or history_nor_committed != value["nor_committed"]
            or history[-1].to_state != state.value
            or (state in _PRE_NOR_STATES and value["nor_committed"])
            or (
                state == UnlockState.RECOVERY_REQUIRED
                and (
                    recovery_state is None
                    or history[-1].from_state != recovery_state.value
                )
            )
            or (
                state != UnlockState.RECOVERY_REQUIRED
                and recovery_state is not None
            )
            or (
                state
                not in _PRE_NOR_STATES
                | {
                    UnlockState.CANCELLED,
                    UnlockState.RECOVERY_REQUIRED,
                }
                and not value["nor_committed"]
            )
        ):
            raise CapacityUnlockError(
                "state_file_corrupt", "An unlock session record is internally inconsistent."
            )
        return CapacityUnlockSession(
            session_id=session_id,
            revision=revision,
            state=state,
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            identity_fingerprint=identity_fingerprint,
            firewire_fingerprint=firewire_fingerprint,
            source_profile_id=str(value["source_profile_id"]),
            source_model_number=str(value["source_model_number"]),
            source_generation=str(value["source_generation"]),
            source_firmware_version=str(value["source_firmware_version"]),
            target_firmware_version=str(value["target_firmware_version"]),
            nor_committed=value["nor_committed"],
            details=details,
            history=history,
            recovery_resume_state=recovery_state,
        )


# A descriptive alias for integrations that treat the object primarily as a
# persisted repository rather than as a transition coordinator.
CapacityUnlockSessionStore = CapacityUnlockStateMachine
